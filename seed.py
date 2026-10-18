import os
import shutil
import csv
import json
import sqlite3
import datetime
from pathlib import Path

from database.db import init_db, get_db
from services.auth import hash_password
from services.extractor import extract_receipt, compute_file_hash, save_cached_extraction
from services.rules import evaluate_receipt_rules

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

DEMO_USERS = [
    {"name": "Finance Admin", "email": "admin@company.com", "role": "admin", "code": "ADM01", "pwd": "admin123"},
    {"name": "Alex Rivera", "email": "e1@company.com", "role": "employee", "code": "E1", "pwd": "emp123"},
    {"name": "Bhavna Patel", "email": "e2@company.com", "role": "employee", "code": "E2", "pwd": "emp123"},
    {"name": "Claire Zhang", "email": "e3@company.com", "role": "employee", "code": "E3", "pwd": "emp123"},
    {"name": "David Kumar", "email": "e4@company.com", "role": "employee", "code": "E4", "pwd": "emp123"},
]

def seed_database():
    print("==================================================")
    print("      RECEIPT GRAVEYARD - SEED SCRIPT             ")
    print("==================================================")
    
    # 1. Initialize schema
    print("\n[1/5] Initializing database schema...")
    init_db()
    db = get_db()

    # Clear existing data for clean seed
    db.execute("DELETE FROM decisions")
    db.execute("DELETE FROM queries")
    db.execute("DELETE FROM receipts")
    db.execute("DELETE FROM users")
    db.commit()

    # 2. Seed Users
    print("\n[2/5] Creating demo user accounts...")
    user_id_map = {}
    for u in DEMO_USERS:
        db.execute(
            """INSERT INTO users (name, email, password_hash, role, employee_code)
               VALUES (?, ?, ?, ?, ?)""",
            (u["name"], u["email"], hash_password(u["pwd"]), u["role"], u["code"])
        )
        user_id = db.execute("SELECT id FROM users WHERE email = ?", (u["email"],)).fetchone()["id"]
        user_id_map[u["code"]] = user_id
        print(f"  ✓ {u['name']} ({u['code']}) - {u['email']} [{u['role']}]")
    db.commit()

    # 3. Process planted manifest
    print("\n[3/5] Running extraction & rule engine on planted cases...")
    manifest_path = BASE_DIR / "sample_data" / "planted" / "manifest.csv"
    if not manifest_path.exists():
        print(f"Error: {manifest_path} not found!")
        return

    with open(manifest_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        planted_rows = list(reader)

    planted_results = []
    
    for row in planted_rows:
        filename = row["file"]
        src_path = BASE_DIR / "sample_data" / "planted" / filename
        if not src_path.exists():
            print(f"  ✕ File not found: {filename}")
            continue

        with open(src_path, "rb") as fp:
            file_bytes = fp.read()

        file_hash = compute_file_hash(file_bytes)
        dest_filename = f"{file_hash[:16]}_{filename}"
        dest_path = UPLOAD_DIR / dest_filename
        shutil.copyfile(src_path, dest_path)

        # AI Extraction (from offline cache or model)
        extracted = extract_receipt(file_bytes, filename, db=db)

        emp_code = row["employee"]
        user_id = user_id_map.get(emp_code, user_id_map["E1"])
        user_category = row["user_category"]
        context = row["context"] or "Trip"
        attendees = int(row["attendees"]) if row.get("attendees") and row["attendees"].isdigit() else 1
        client = row.get("client") or None

        # For old_receipt.png: manifest specifies is_seeded=0 required to test LATE_UPLOAD rejection
        is_seeded = 0 if filename == "old_receipt.png" else 1

        user_inputs = {
            "user_id": user_id,
            "category": user_category,
            "context": context,
            "attendees": attendees,
            "client_name": client,
            "is_client_related": bool(client)
        }

        eval_res = evaluate_receipt_rules(
            extracted,
            user_inputs,
            db=db,
            is_seeded=bool(is_seeded)
        )

        source_type = "pdf" if filename.endswith(".pdf") else "image"
        flags_json = json.dumps(eval_res["flags"])
        missing_fields_json = json.dumps(eval_res["missing_fields"])
        items_json = json.dumps(extracted.get("items", []))
        conf_json = json.dumps(extracted.get("conf", {}))
        extraction_json = json.dumps(extracted)

        ocr_text = extracted.get("_ocr_text", None)
        ocr_amount = None
        ocr_invoice_no = None
        if ocr_text:
            from services.extractor import parse_ocr_fields
            ocr_fields = parse_ocr_fields(ocr_text)
            ocr_amount = ocr_fields.get("amount")
            ocr_invoice_no = ocr_fields.get("invoice_no")

        cur = db.execute(
            """INSERT INTO receipts (
                user_id, file_path, file_hash, source_type, category_user, category_model,
                context, attendees, is_client_related, client_name, merchant, city, country,
                tier, issue_date, unique_id, currency,
                amount_original, accepted_amount, amount_source,
                fx_rate, fx_rate_date, amount_base,
                subtotal, tax, discount, shipping, nights,
                ocr_text, ocr_amount, ocr_invoice_no,
                items_json, conf_json, extraction_json,
                status, flags_json, missing_fields_json, dup_group_id, uploaded_at, is_seeded
            ) VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?, datetime('now'), ?
            )""",
            (
                user_id, str(dest_path), file_hash, source_type, user_category, extracted.get("model_category"),
                context, attendees, 1 if client else 0, client,
                eval_res.get("merchant") or extracted.get("merchant"), eval_res.get("city"), eval_res.get("country"),
                eval_res.get("tier"), extracted.get("issue_date"), extracted.get("unique_id"), eval_res.get("currency"),
                eval_res.get("amount_original"), eval_res.get("amount_original"), "model",
                eval_res.get("fx_rate"), eval_res.get("fx_rate_date"), eval_res.get("amount_base"),
                extracted.get("subtotal"), extracted.get("tax"), extracted.get("discount"), extracted.get("shipping"),
                eval_res.get("nights"),
                ocr_text, ocr_amount, ocr_invoice_no,
                items_json, conf_json, extraction_json,
                eval_res["status"], flags_json, missing_fields_json, eval_res.get("dup_group_id"),
                is_seeded
            )
        )
        receipt_id = cur.lastrowid
        db.commit()

        planted_results.append({
            "id": receipt_id,
            "file": filename,
            "employee": emp_code,
            "category": user_category,
            "expected": row["expected_outcome"],
            "actual": eval_res["status"],
            "tests": row["tests"]
        })

    # 4. Plant cumulative monthly food receipts for E1
    print("\n[4/5] Planting cumulative monthly ledger sequence (E1: 4 receipts of 4,000 INR)...")
    for i in range(1, 4):
        # Days: March 2, 5, 8 (total 12,000 INR <= 15,000 monthly food limit)
        day_str = f"2018-03-{i*3:02d}"
        dummy_extraction = {
            "merchant": f"Team Lunch Caterers {i}",
            "city": "Bengaluru",
            "country": "India",
            "issue_date": day_str,
            "unique_id": f"CAT-2018-0{i}",
            "currency": "INR",
            "total": 4000.0,
            "subtotal": 3800.0,
            "tax": 200.0,
            "items": [{"name": f"Buffet Catering {i}", "qty": 1, "amount": 3800.0}],
            "items_readable": True,
            "model_category": "Food",
            "conf": {"issue_date": 0.95, "total": 0.95, "unique_id": 0.95, "city": 0.95, "items": 0.95}
        }
        user_inputs = {"user_id": user_id_map["E1"], "category": "Food", "context": "Trip", "attendees": 5}
        eval_res = evaluate_receipt_rules(dummy_extraction, user_inputs, db=db, is_seeded=True)
        
        db.execute(
            """INSERT INTO receipts (
                user_id, file_path, file_hash, source_type, category_user, category_model,
                context, attendees, merchant, city, country, tier, issue_date, unique_id,
                currency, amount_original, accepted_amount, amount_source,
                fx_rate, fx_rate_date, amount_base,
                status, flags_json, missing_fields_json, uploaded_at, is_seeded
            ) VALUES (?, ?, ?, 'image', 'Food', 'Food', 'Trip', 5, ?, 'Bengaluru', 'India', 'A',
                      ?, ?, 'INR', 4000.0, 4000.0, 'model', 1.0, NULL, 4000.0,
                      ?, ?, '[]', datetime('now'), 1)""",
            (
                user_id_map["E1"], str(UPLOAD_DIR / f"cater_{i}.png"), f"dummy_hash_{i}",
                f"Team Lunch Caterers {i}", day_str, f"CAT-2018-0{i}", eval_res["status"],
                json.dumps(eval_res["flags"])
            )
        )
    db.commit()

    # 4th receipt: March 12, 2018 -> 12,000 + 4,000 = 16,000 > 15,000 limit -> FLAGGED with LIMIT_MONTH!
    dummy_extraction_4th = {
        "merchant": "Team Lunch Caterers 4",
        "city": "Bengaluru",
        "country": "India",
        "issue_date": "2018-03-12",
        "unique_id": "CAT-2018-04",
        "currency": "INR",
        "total": 4000.0,
        "subtotal": 3800.0,
        "tax": 200.0,
        "items": [{"name": "Buffet Catering 4", "qty": 1, "amount": 3800.0}],
        "items_readable": True,
        "model_category": "Food",
        "conf": {"issue_date": 0.95, "total": 0.95, "unique_id": 0.95, "city": 0.95, "items": 0.95}
    }
    eval_res_4th = evaluate_receipt_rules(
        dummy_extraction_4th,
        {"user_id": user_id_map["E1"], "category": "Food", "context": "Trip", "attendees": 5},
        db=db,
        is_seeded=True
    )
    db.execute(
        """INSERT INTO receipts (
            user_id, file_path, file_hash, source_type, category_user, category_model,
            context, attendees, merchant, city, country, tier, issue_date, unique_id,
            currency, amount_original, fx_rate, amount_base, status, flags_json,
            missing_fields_json, uploaded_at, is_seeded
        ) VALUES (?, ?, 'dummy_hash_4', 'image', 'Food', 'Food', 'Trip', 5, 'Team Lunch Caterers 4',
                  'Bengaluru', 'India', 'A', '2018-03-12', 'CAT-2018-04', 'INR', 4000.0, 1.0, 4000.0,
                  ?, ?, '[]', datetime('now'), 1)""",
        (
            user_id_map["E1"], str(UPLOAD_DIR / "cater_4.png"), eval_res_4th["status"],
            json.dumps(eval_res_4th["flags"])
        )
    )
    db.commit()
    print("  ✓ 4 Food receipts planted for E1 (1st-3rd verified/flagged, 4th flagged for LIMIT_MONTH: 12,000 + 4,000 = 16,000 > 15,000)")

    # 5. Plant a query on one rejected receipt for immediate admin demo
    print("\n[5/5] Planting sample query on alcohol rejection for admin demo...")
    alc_receipt = db.execute("SELECT id, user_id FROM receipts WHERE merchant LIKE '%Pour House%'").fetchone()
    if alc_receipt:
        db.execute(
            """INSERT INTO queries (receipt_id, user_id, message, status, created_at)
               VALUES (?, ?, ?, 'open', datetime('now'))""",
            (
                alc_receipt["id"],
                alc_receipt["user_id"],
                "The alcoholic beverages on this invoice were ordered directly by the visiting client delegation. Requesting management exception."
            )
        )
        db.commit()
        print(f"  ✓ Open query planted on Receipt #{alc_receipt['id']}")

    db.close()

    # Print summary table
    print("\n==========================================================================================")
    print(f"{'FILE':<26} | {'EMP':<4} | {'CATEGORY':<14} | {'EXPECTED':<22} | {'ACTUAL STATUS':<14}")
    print("------------------------------------------------------------------------------------------")
    for r in planted_results:
        exp_clean = r["expected"].split(";")[0].split("(")[0].strip().lower().replace("_", " ")
        act_clean = r["actual"].lower().replace("_", " ")
        match_icon = "✓" if exp_clean in act_clean else "✕"
        print(f"{r['file']:<26} | {r['employee']:<4} | {r['category']:<14} | {r['expected']:<22} | {match_icon} {r['actual'].upper()}")
    print("==========================================================================================")
    print("Seed complete! Demo accounts ready for testing.")

if __name__ == "__main__":
    seed_database()
