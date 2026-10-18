import os
import io
import csv
import json
import datetime
from pathlib import Path
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, abort, send_file, Response
)
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

from database.db import (
    init_db, get_db, get_setting, set_setting,
    get_policy_config, get_cities_config, get_categories_config
)
from services.auth import (
    hash_password, verify_password, login_required, admin_required
)
from services.extractor import extract_receipt, compute_file_hash
from services.rules import evaluate_receipt_rules
from services.currency import convert_to_base

# Load environment variables
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "uploads"
UPLOAD_FOLDER.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "pdf"}

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "receipt-graveyard-secret-key-hackathon-2026")
app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024  # 15 MB limit

# Ensure database and initial data exist on startup
init_db()

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

# -------------------------------------------------------------
# Common Routes & Auth
# -------------------------------------------------------------
@app.route("/")
def index():
    if "user_id" in session:
        if session.get("role") == "admin":
            return redirect(url_for("admin_queue"))
        return redirect(url_for("employee_receipts"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        db = get_db()
        user = db.execute("SELECT * FROM users WHERE lower(email) = ?", (email,)).fetchone()
        db.close()

        if user and verify_password(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["name"] = user["name"]
            session["email"] = user["email"]
            session["role"] = user["role"]
            session["employee_code"] = user["employee_code"]
            flash(f"Welcome back, {user['name']}!", "success")
            if user["role"] == "admin":
                return redirect(url_for("admin_queue"))
            return redirect(url_for("employee_receipts"))
        else:
            flash("Invalid email or password. Please try again.", "danger")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("login"))

# -------------------------------------------------------------
# Employee Pages
# -------------------------------------------------------------
@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload_receipt():
    if request.method == "POST":
        if "receipt_file" not in request.files:
            flash("No file was uploaded.", "danger")
            return redirect(request.url)

        file = request.files["receipt_file"]
        if file.filename == "":
            flash("No file was selected.", "danger")
            return redirect(request.url)

        if not allowed_file(file.filename):
            flash("Invalid file type. Only JPG, PNG, and PDF files are allowed.", "danger")
            return redirect(request.url)

        file_bytes = file.read()
        if len(file_bytes) == 0:
            flash("The uploaded file is empty.", "danger")
            return redirect(request.url)

        file_hash = compute_file_hash(file_bytes)
        ext = file.filename.rsplit(".", 1)[1].lower()
        saved_filename = f"{file_hash[:16]}_{secure_filename(file.filename)}"
        saved_path = UPLOAD_FOLDER / saved_filename
        
        with open(saved_path, "wb") as f:
            f.write(file_bytes)

        user_category = request.form.get("category", "Miscellaneous")
        context = request.form.get("context", "Trip")
        attendees = request.form.get("attendees", "1")
        is_client_related = 1 if request.form.get("is_client_related") else 0
        client_name = request.form.get("client_name", "").strip() if is_client_related else None
        claimed_amount_raw = request.form.get("claimed_amount")
        claimed_invoice_no = request.form.get("claimed_invoice_no", "").strip() or None
        claimed_amount = None
        if claimed_amount_raw not in (None, ""):
            try:
                claimed_amount = float(claimed_amount_raw)
            except (ValueError, TypeError):
                claimed_amount = None

        db = get_db()

        # Step 1: AI extracts structured JSON (cached by file_hash)
        extracted = extract_receipt(file_bytes, file.filename, db=db)
        
        # Step 2: Plain Python rule engine evaluates policy decisions
        user_inputs = {
            "user_id": session["user_id"],
            "category": user_category,
            "claimed_amount": claimed_amount,
            "claimed_invoice_no": claimed_invoice_no,
            "context": context,
            "attendees": attendees,
            "is_client_related": is_client_related,
            "client_name": client_name
        }

        eval_res = evaluate_receipt_rules(
            extracted,
            user_inputs,
            db=db,
            is_seeded=False
        )

        source_type = "pdf" if ext == "pdf" else "image"
        status = eval_res["status"]
        flags_json = json.dumps(eval_res["flags"])
        missing_fields_json = json.dumps(eval_res["missing_fields"])
        items_json = json.dumps(extracted.get("items", []))
        conf_json = json.dumps(extracted.get("conf", {}))
        extraction_json = json.dumps(extracted)

        # Insert receipt record
        ocr_text = eval_res.get("ocr_text") or extracted.get("_ocr_text", "")
        ocr_amount = eval_res.get("ocr_amount")
        ocr_invoice_no = eval_res.get("ocr_invoice_no")

        cur = db.execute(
            """INSERT INTO receipts (
                user_id, file_path, file_hash, source_type, category_user, claimed_amount, claimed_invoice_no,
                category_model, context, attendees, is_client_related, client_name, merchant, city, country,
                tier, issue_date, unique_id, currency,
                amount_original, accepted_amount, amount_source,
                fx_rate, fx_rate_date, amount_base,
                subtotal, tax, discount, shipping, nights,
                ocr_text, ocr_amount, ocr_invoice_no,
                items_json, conf_json, extraction_json,
                status, flags_json, missing_fields_json, dup_group_id, uploaded_at, is_seeded
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?, datetime('now'), 0
            )""",
            (
                session["user_id"], str(saved_path), file_hash, source_type, user_category, claimed_amount, claimed_invoice_no,
                extracted.get("model_category"), context, int(attendees) if attendees and str(attendees).isdigit() else 1,
                is_client_related, client_name,
                eval_res.get("merchant") or extracted.get("merchant"), eval_res.get("city"), eval_res.get("country"),
                eval_res.get("tier"), extracted.get("issue_date"), extracted.get("unique_id"), eval_res.get("currency"),
                eval_res.get("amount_original"), eval_res.get("accepted_amount", eval_res.get("amount_original")), eval_res.get("amount_source", "model"),
                eval_res.get("fx_rate"), eval_res.get("fx_rate_date"), eval_res.get("amount_base"),
                extracted.get("subtotal"), extracted.get("tax"), extracted.get("discount"), extracted.get("shipping"),
                eval_res.get("nights"),
                ocr_text or None, ocr_amount, ocr_invoice_no,
                items_json, conf_json, extraction_json,
                status, flags_json, missing_fields_json, eval_res.get("dup_group_id")
            )
        )
        receipt_id = cur.lastrowid
        db.commit()
        db.close()

        return redirect(url_for("employee_receipt_result", receipt_id=receipt_id))

    return render_template("upload.html")

@app.route("/receipts/<int:receipt_id>/result")
@login_required
def employee_receipt_result(receipt_id):
    db = get_db()
    receipt = db.execute("SELECT * FROM receipts WHERE id = ?", (receipt_id,)).fetchone()
    db.close()

    if not receipt:
        abort(404)
    if session.get("role") != "admin" and receipt["user_id"] != session["user_id"]:
        abort(403)

    flags = json.loads(receipt["flags_json"]) if receipt["flags_json"] else []
    missing_fields = json.loads(receipt["missing_fields_json"]) if receipt["missing_fields_json"] else []
    
    rejection_reason = None
    if receipt["status"] == "rejected":
        # Extract rejection message from flags or rule explanation
        if flags:
            rejection_reason = flags[0]
        else:
            rejection_reason = {
                "code": "AUTO_REJECTED",
                "clause": "docs/POLICY.md",
                "message": "This receipt violated non-negotiable policy constraints (prohibited item or late submission)."
            }

    return render_template(
        "result.html",
        receipt=receipt,
        flags=flags,
        missing_fields=missing_fields,
        rejection_reason=rejection_reason
    )

@app.route("/receipts")
@login_required
def employee_receipts():
    db = get_db()
    receipts = db.execute(
        "SELECT * FROM receipts WHERE user_id = ? ORDER BY id DESC",
        (session["user_id"],)
    ).fetchall()
    db.close()
    return render_template("receipts.html", receipts=receipts)

@app.route("/receipts/<int:receipt_id>")
@login_required
def employee_receipt_detail(receipt_id):
    db = get_db()
    receipt = db.execute("SELECT * FROM receipts WHERE id = ?", (receipt_id,)).fetchone()
    if not receipt:
        db.close()
        abort(404)
    if session.get("role") != "admin" and receipt["user_id"] != session["user_id"]:
        db.close()
        abort(403)

    items = json.loads(receipt["items_json"]) if receipt["items_json"] else []
    flags = json.loads(receipt["flags_json"]) if receipt["flags_json"] else []
    missing_fields = json.loads(receipt["missing_fields_json"]) if receipt["missing_fields_json"] else []
    
    query = db.execute("SELECT * FROM queries WHERE receipt_id = ?", (receipt_id,)).fetchone()
    decisions = db.execute("SELECT * FROM decisions WHERE receipt_id = ? ORDER BY id DESC", (receipt_id,)).fetchall()
    db.close()

    return render_template(
        "receipt_detail.html",
        receipt=receipt,
        items=items,
        flags=flags,
        missing_fields=missing_fields,
        query=query,
        decisions=decisions
    )

@app.route("/receipts/<int:receipt_id>/complete_info", methods=["POST"])
@login_required
def employee_complete_info(receipt_id):
    db = get_db()
    receipt = db.execute("SELECT * FROM receipts WHERE id = ?", (receipt_id,)).fetchone()
    if not receipt:
        db.close()
        abort(404)
    if receipt["user_id"] != session["user_id"] and session.get("role") != "admin":
        db.close()
        abort(403)

    extraction = json.loads(receipt["extraction_json"]) if receipt["extraction_json"] else {}
    
    # Update provided missing fields
    if "issue_date" in request.form:
        extraction["issue_date"] = request.form.get("issue_date")
        extraction.setdefault("conf", {})["issue_date"] = 1.0

    if "total" in request.form:
        try:
            extraction["total"] = float(request.form.get("total"))
            extraction.setdefault("conf", {})["total"] = 1.0
        except ValueError:
            pass

    if "unique_id" in request.form:
        extraction["unique_id"] = request.form.get("unique_id")
        extraction.setdefault("conf", {})["unique_id"] = 1.0

    if "currency" in request.form:
        extraction["currency"] = request.form.get("currency")

    attendees = request.form.get("attendees", receipt["attendees"])
    try:
        attendees = int(attendees)
    except (ValueError, TypeError):
        attendees = 1

    client_name = request.form.get("client_name", receipt["client_name"])

    user_inputs = {
        "user_id": receipt["user_id"],
        "category": receipt["category_user"],
        "context": receipt["context"],
        "attendees": attendees,
        "is_client_related": receipt["is_client_related"],
        "client_name": client_name
    }

    # Re-evaluate rules on the updated data
    eval_res = evaluate_receipt_rules(
        extraction,
        user_inputs,
        db=db,
        is_seeded=bool(receipt["is_seeded"]),
        existing_receipt_id=receipt_id
    )

    db.execute(
        """UPDATE receipts SET
            issue_date = ?,
            unique_id = ?,
            amount_original = ?,
            accepted_amount = ?,
            amount_source = ?,
            claimed_amount = ?,
            currency = ?,
            fx_rate = ?,
            amount_base = ?,
            attendees = ?,
            client_name = ?,
            city = ?,
            tier = ?,
            extraction_json = ?,
            status = ?,
            flags_json = ?,
            missing_fields_json = ?
        WHERE id = ?""",
        (
            extraction.get("issue_date"),
            extraction.get("unique_id"),
            eval_res.get("amount_original"),
            eval_res.get("amount_original"),  # accepted_amount = model value after user correction
            "user",                            # amount_source = user (they corrected it)
            request.form.get("total") or receipt["claimed_amount"],  # preserve user's claimed amount
            eval_res.get("currency"),
            eval_res.get("fx_rate"),
            eval_res.get("amount_base"),
            attendees,
            client_name,
            eval_res.get("city"),
            eval_res.get("tier"),
            json.dumps(extraction),
            eval_res["status"],
            json.dumps(eval_res["flags"]),
            json.dumps(eval_res["missing_fields"]),
            receipt_id
        )
    )
    db.commit()
    db.close()

    flash("Information updated and policy rules re-evaluated.", "success")
    return redirect(url_for("employee_receipt_detail", receipt_id=receipt_id))

@app.route("/receipts/<int:receipt_id>/query", methods=["POST"])
@login_required
def employee_submit_query(receipt_id):
    message = request.form.get("message", "").strip()
    if not message:
        flash("Clarification message cannot be empty.", "warning")
        return redirect(url_for("employee_receipt_detail", receipt_id=receipt_id))

    db = get_db()
    receipt = db.execute("SELECT * FROM receipts WHERE id = ?", (receipt_id,)).fetchone()
    if not receipt or receipt["user_id"] != session["user_id"]:
        db.close()
        abort(403)

    if receipt["status"] != "rejected":
        db.close()
        flash("Clarification queries can only be submitted for auto-rejected receipts.", "warning")
        return redirect(url_for("employee_receipt_detail", receipt_id=receipt_id))

    # Check if a query already exists (single query policy)
    existing_q = db.execute("SELECT id FROM queries WHERE receipt_id = ?", (receipt_id,)).fetchone()
    if existing_q:
        db.close()
        flash("A clarification query has already been submitted for this receipt.", "warning")
        return redirect(url_for("employee_receipt_detail", receipt_id=receipt_id))

    db.execute(
        """INSERT INTO queries (receipt_id, user_id, message, status, created_at)
           VALUES (?, ?, ?, 'open', datetime('now'))""",
        (receipt_id, session["user_id"], message)
    )
    db.commit()
    db.close()

    flash("Your clarification query has been sent to the finance administrator.", "success")
    return redirect(url_for("employee_receipt_detail", receipt_id=receipt_id))

@app.route("/receipts/<int:receipt_id>/delete", methods=["POST"])
@login_required
def employee_delete_receipt(receipt_id):
    """Allows user (or admin) to delete an expense claim."""
    db = get_db()
    receipt = db.execute("SELECT * FROM receipts WHERE id = ?", (receipt_id,)).fetchone()
    if not receipt:
        db.close()
        abort(404)

    # Permission check: employee can delete their own receipts; admin can delete any receipt
    if receipt["user_id"] != session["user_id"] and session.get("role") != "admin":
        db.close()
        abort(403)

    # Delete physical file if it exists
    file_path = receipt["file_path"]
    if file_path:
        try:
            p = Path(file_path)
            if p.exists():
                p.unlink()
        except Exception as e:
            print(f"[Delete] file removal warning: {e}")

    # Remove all related records from DB
    db.execute("DELETE FROM queries WHERE receipt_id = ?", (receipt_id,))
    db.execute("DELETE FROM decisions WHERE receipt_id = ?", (receipt_id,))
    db.execute("DELETE FROM receipts WHERE id = ?", (receipt_id,))
    db.commit()
    db.close()

    flash(f"Claim #{receipt_id} has been permanently deleted.", "success")
    if session.get("role") == "admin":
        return redirect(url_for("admin_queue"))
    return redirect(url_for("employee_receipts"))

@app.route("/receipts/<int:receipt_id>/file")
@login_required
def view_receipt_file(receipt_id):
    """Secured file serving: accessible ONLY to the receipt owner and admin."""
    db = get_db()
    receipt = db.execute("SELECT * FROM receipts WHERE id = ?", (receipt_id,)).fetchone()
    db.close()

    if not receipt:
        abort(404)
    if session.get("role") != "admin" and receipt["user_id"] != session["user_id"]:
        abort(403)

    file_path = Path(receipt["file_path"])
    if not file_path.exists():
        abort(404)

    return send_file(str(file_path))

@app.route("/policy")
def policy_page():
    policy = get_policy_config()
    cities = get_cities_config()
    return render_template("policy.html", policy=policy, cities=cities)

@app.route("/help")
def help_page():
    helpline = get_setting("HELPLINE_NUMBER", "+91 800-422-4467")
    return render_template("help.html", helpline_number=helpline)

# -------------------------------------------------------------
# Admin Pages
# -------------------------------------------------------------
@app.route("/admin")
@admin_required
def admin_queue():
    view_mode = request.args.get("view", "category")
    db = get_db()

    # Queue of Verified and Flagged receipts ONLY
    query = """
        SELECT r.*, u.name as user_name, u.employee_code
        FROM receipts r
        JOIN users u ON r.user_id = u.id
        WHERE r.status IN ('verified', 'flagged')
        ORDER BY r.id ASC
    """
    rows = db.execute(query).fetchall()
    db.close()

    # Identify duplicate cluster originals: earliest ID in dup_group_id is labeled original
    cluster_earliest = {}
    for r in rows:
        grp = r["dup_group_id"]
        if grp:
            if grp not in cluster_earliest or r["id"] < cluster_earliest[grp]:
                cluster_earliest[grp] = r["id"]

    processed_rows = []
    for r in rows:
        row_dict = dict(r)
        flags = json.loads(r["flags_json"]) if r["flags_json"] else []
        row_dict["flag_count"] = len(flags)
        grp = r["dup_group_id"]
        row_dict["is_original"] = bool(grp and cluster_earliest.get(grp) == r["id"])
        processed_rows.append(row_dict)

    # Grouping
    groups = {}
    if view_mode == "employee":
        for r in processed_rows:
            key = f"{r['user_name']} ({r['employee_code']})"
            groups.setdefault(key, []).append(r)
    else:
        for r in processed_rows:
            key = r["category_user"]
            groups.setdefault(key, []).append(r)

    return render_template(
        "admin/queue.html",
        groups=groups,
        view_mode=view_mode
    )

@app.route("/admin/receipt/<int:receipt_id>")
@admin_required
def admin_receipt_detail(receipt_id):
    db = get_db()
    receipt = db.execute(
        """SELECT r.*, u.name as user_name, u.employee_code
           FROM receipts r
           JOIN users u ON r.user_id = u.id
           WHERE r.id = ?""",
        (receipt_id,)
    ).fetchone()

    if not receipt:
        db.close()
        abort(404)

    items = json.loads(receipt["items_json"]) if receipt["items_json"] else []
    flags = json.loads(receipt["flags_json"]) if receipt["flags_json"] else []
    decisions = db.execute(
        "SELECT * FROM decisions WHERE receipt_id = ? ORDER BY id DESC",
        (receipt_id,)
    ).fetchall()
    db.close()

    return render_template(
        "admin/receipt_detail.html",
        receipt=receipt,
        items=items,
        flags=flags,
        decisions=decisions
    )

@app.route("/admin/receipt/<int:receipt_id>/decide", methods=["POST"])
@admin_required
def admin_decide_receipt(receipt_id):
    decision = request.form.get("decision", "").strip().lower()
    if decision == "approve":
        decision = "approved"
    elif decision == "deny":
        decision = "denied"
    reason = request.form.get("reason", "").strip()
    category_override = request.form.get("category_override", "").strip()

    if decision not in ("approved", "denied"):
        flash("Invalid decision choice.", "danger")
        return redirect(url_for("admin_receipt_detail", receipt_id=receipt_id))

    if decision == "denied" and not reason:
        flash("A written reason is required when denying a receipt.", "danger")
        return redirect(url_for("admin_receipt_detail", receipt_id=receipt_id))

    db = get_db()
    receipt = db.execute("SELECT * FROM receipts WHERE id = ?", (receipt_id,)).fetchone()
    if not receipt:
        db.close()
        abort(404)

    # Optional category override
    new_category = category_override if category_override else receipt["category_user"]

    db.execute(
        """INSERT INTO decisions (receipt_id, admin_id, decision, reason, created_at)
           VALUES (?, ?, ?, ?, datetime('now'))""",
        (receipt_id, session["user_id"], decision, reason or None)
    )

    db.execute(
        "UPDATE receipts SET status = ?, category_user = ? WHERE id = ?",
        (decision, new_category, receipt_id)
    )
    db.commit()
    db.close()

    flash(f"Receipt #{receipt_id} has been {decision}.", "success")
    return redirect(url_for("admin_queue"))

@app.route("/admin/rejected")
@admin_required
def admin_rejected_queries():
    db = get_db()
    
    # Auto-rejected receipts
    rejected_receipts = db.execute(
        """SELECT r.*, u.name as user_name, u.employee_code, q.status as query_status
           FROM receipts r
           JOIN users u ON r.user_id = u.id
           LEFT JOIN queries q ON r.id = q.receipt_id
           WHERE r.status = 'rejected'
           ORDER BY r.id DESC"""
    ).fetchall()

    # All queries
    queries = db.execute(
        """SELECT q.*, r.merchant, r.issue_date, r.amount_base, r.category_user, r.status as receipt_status,
                  u.name as user_name, u.employee_code
           FROM queries q
           JOIN receipts r ON q.receipt_id = r.id
           JOIN users u ON q.user_id = u.id
           ORDER BY (CASE WHEN q.status = 'open' THEN 0 ELSE 1 END), q.id DESC"""
    ).fetchall()

    db.close()

    return render_template(
        "admin/rejected_queries.html",
        rejected_receipts=rejected_receipts,
        queries=queries
    )

@app.route("/admin/query/<int:query_id>/resolve", methods=["POST"])
@admin_required
def admin_resolve_query(query_id):
    resolution = request.form.get("resolution", "").strip().lower() # 'uphold' or 'overturn'
    reason = request.form.get("reason", "").strip() or request.form.get("resolution_reason", "").strip()

    if resolution not in ("uphold", "overturn"):
        flash("Invalid query resolution.", "danger")
        return redirect(url_for("admin_rejected_queries"))

    if resolution == "uphold" and not reason:
        flash("A written reason is required when upholding a rejection.", "danger")
        return redirect(url_for("admin_rejected_queries"))

    db = get_db()
    query_row = db.execute("SELECT * FROM queries WHERE id = ?", (query_id,)).fetchone()
    if not query_row:
        db.close()
        abort(404)

    receipt_id = query_row["receipt_id"]
    new_query_status = "overturned" if resolution == "overturn" else "upheld"

    # Update query
    db.execute(
        """UPDATE queries SET
            status = ?,
            admin_id = ?,
            admin_response = ?,
            resolution_reason = ?,
            resolved_at = datetime('now')
        WHERE id = ?""",
        (new_query_status, session["user_id"], reason, reason, query_id)
    )

    # If overturned, the receipt becomes 'approved'!
    if new_query_status == "overturned":
        db.execute("UPDATE receipts SET status = 'approved' WHERE id = ?", (receipt_id,))
        db.execute(
            """INSERT INTO decisions (receipt_id, admin_id, decision, reason, created_at)
               VALUES (?, ?, 'approved', ?, datetime('now'))""",
            (receipt_id, session["user_id"], f"Rejection overturned via query review: {reason}")
        )
        flash(f"Query #{query_id} overturned! Receipt #{receipt_id} has been marked as Approved.", "success")
    else:
        db.execute(
            """INSERT INTO decisions (receipt_id, admin_id, decision, reason, created_at)
               VALUES (?, ?, 'denied', ?, datetime('now'))""",
            (receipt_id, session["user_id"], f"Rejection upheld: {reason}")
        )
        flash(f"Query #{query_id} resolved as Upheld. Rejection confirmed.", "info")

    db.commit()
    db.close()

    return redirect(url_for("admin_rejected_queries"))

@app.route("/admin/settings")
@admin_required
def admin_settings():
    db = get_db()
    settings_rows = db.execute("SELECT key, value FROM settings").fetchall()
    settings_dict = {r["key"]: r["value"] for r in settings_rows}
    
    fx_rows = db.execute("SELECT * FROM fx_rates ORDER BY currency ASC").fetchall()
    db.close()

    today_str = datetime.date.today().isoformat()
    return render_template(
        "admin/settings.html",
        settings=settings_dict,
        fx_rates=fx_rows,
        today_str=today_str
    )

@app.route("/admin/settings/update", methods=["POST"])
@admin_required
def admin_update_settings():
    db = get_db()
    base_curr = request.form.get("base_currency", "INR").strip().upper()
    helpline = request.form.get("helpline_number", "+91 800-422-4467").strip()
    conf_thr = request.form.get("conf_threshold", "0.7").strip()
    banned_thr = request.form.get("banned_threshold", "0.7").strip()
    groq_mod = request.form.get("groq_model", "meta-llama/llama-4-scout-17b-16e-instruct").strip()
    groq_key = request.form.get("groq_api_key", "").strip()

    set_setting("BASE_CURRENCY", base_curr, db=db)
    set_setting("HELPLINE_NUMBER", helpline, db=db)
    set_setting("CONF_THRESHOLD", conf_thr, db=db)
    set_setting("BANNED_THRESHOLD", banned_thr, db=db)
    set_setting("GROQ_MODEL", groq_mod, db=db)
    if groq_key:
        set_setting("GROQ_API_KEY", groq_key, db=db)
        os.environ["GROQ_API_KEY"] = groq_key

    db.close()
    flash("System settings successfully updated.", "success")
    return redirect(url_for("admin_settings"))

@app.route("/admin/settings/fx", methods=["POST"])
@admin_required
def admin_update_fx_rate():
    currency = request.form.get("currency", "").strip().upper()
    rate = request.form.get("rate_to_base", "").strip() or request.form.get("rate_to_inr", "").strip()
    fetched_at = request.form.get("fetched_at", "").strip() or datetime.date.today().isoformat()
    base = request.form.get("base", "INR").strip().upper() or "INR"

    if not currency or not rate:
        flash("Currency and exchange rate are required.", "warning")
        return redirect(url_for("admin_settings"))

    try:
        rate_val = float(rate)
    except ValueError:
        flash("Exchange rate must be a valid number.", "danger")
        return redirect(url_for("admin_settings"))

    db = get_db()
    db.execute(
        """INSERT INTO fx_rates (currency, base, rate_to_base, fetched_at, source)
           VALUES (?, ?, ?, ?, 'admin')
           ON CONFLICT(currency, base) DO UPDATE SET
             rate_to_base = excluded.rate_to_base,
             fetched_at   = excluded.fetched_at,
             source       = 'admin'""",
        (currency, base, rate_val, fetched_at)
    )
    db.commit()
    db.close()

    flash(f"Exchange rate for {currency}/{base} updated to {rate_val}.", "success")
    return redirect(url_for("admin_settings"))

@app.route("/admin/settings/refresh_fx", methods=["POST"])
@admin_required
def admin_refresh_fx_rates():
    from services.currency import refresh_live_fx_rates
    db = get_db()
    base = get_setting("BASE_CURRENCY", "INR", db=db)
    count, err = refresh_live_fx_rates(base=base, db=db)
    db.close()
    if err:
        flash(f"Could not refresh live rates: {err}. Preserving last stored rates.", "warning")
    else:
        flash(f"Successfully refreshed {count} live exchange rates against {base} from Exchange Rate API.", "success")
    return redirect(url_for("admin_settings"))

@app.route("/admin/export/csv")
@admin_required
def admin_export_csv():
    """Generates and downloads a CSV export of all approved expenses."""
    db = get_db()
    rows = db.execute(
        """SELECT r.id, u.name as employee_name, u.employee_code, r.issue_date, r.merchant,
                  r.category_user as category, r.context, r.city, r.tier, r.currency,
                  r.amount_original, r.fx_rate, r.amount_base, r.uploaded_at
           FROM receipts r
           JOIN users u ON r.user_id = u.id
           WHERE r.status = 'approved'
           ORDER BY r.id ASC"""
    ).fetchall()
    db.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Receipt ID", "Employee Name", "Employee Code", "Issue Date", "Merchant",
        "Category", "Context", "City", "City Tier", "Original Currency",
        "Original Amount", "FX Rate", "Approved Base Amount (INR)", "Uploaded At"
    ])

    for r in rows:
        writer.writerow([
            r["id"], r["employee_name"], r["employee_code"], r["issue_date"], r["merchant"],
            r["category"], r["context"], r["city"], r["tier"], r["currency"],
            r["amount_original"], r["fx_rate"], r["amount_base"], r["uploaded_at"]
        ])

    csv_data = output.getvalue()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=approved_expenses_export.csv"}
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(debug=True, host="0.0.0.0", port=port)
