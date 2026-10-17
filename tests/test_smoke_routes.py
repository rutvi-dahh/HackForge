"""
tests/test_smoke_routes.py

Smoke test: builds a fresh DB from schema, seeds users + one info_needed receipt,
then hits every significant route once and asserts no 500.
"""
import json
import tempfile
import os
import pytest


@pytest.fixture()
def client(tmp_path):
    db_path = str(tmp_path / "test_smoke.db")
    os.environ["DATABASE_PATH"] = db_path

    from database.db import init_db, get_db
    from services.auth import hash_password
    from services.rules import evaluate_receipt_rules
    import json as _json

    init_db(db_path)
    db = get_db(db_path)

    # Seed users
    db.execute("INSERT INTO users (name,email,password_hash,role,employee_code) VALUES (?,?,?,?,?)",
               ("Admin", "admin@test.com", hash_password("admin123"), "admin", "ADM"))
    db.execute("INSERT INTO users (name,email,password_hash,role,employee_code) VALUES (?,?,?,?,?)",
               ("Employee", "emp@test.com", hash_password("emp123"), "employee", "E1"))
    db.commit()

    emp_id = db.execute("SELECT id FROM users WHERE email='emp@test.com'").fetchone()["id"]

    # Seed fx rate
    db.execute("INSERT OR IGNORE INTO fx_rates (currency,rate_to_inr,set_date) VALUES ('INR',1.0,'2026-01-01')")
    db.commit()

    # One VERIFIED receipt
    extraction_ok = {
        "merchant": "Test Hotel", "city": "Bengaluru", "country": "India",
        "issue_date": "2026-01-10", "unique_id": "INV-001", "currency": "INR",
        "total": 3000.0, "subtotal": 3000.0, "tax": 0.0, "items": [{"name": "Room", "qty": 1, "amount": 3000.0}],
        "items_readable": True, "model_category": "Accommodation",
        "conf": {"issue_date": 0.95, "total": 0.95, "unique_id": 0.95, "city": 0.95, "items": 0.95}
    }
    user_inputs_ok = {"user_id": emp_id, "category": "Accommodation", "context": "Trip", "attendees": 1}
    ev = evaluate_receipt_rules(extraction_ok, user_inputs_ok, db=db, is_seeded=True)
    db.execute(
        """INSERT INTO receipts (user_id,file_path,file_hash,source_type,category_user,context,
           attendees,merchant,city,country,tier,issue_date,unique_id,currency,amount_original,
           fx_rate,amount_base,status,flags_json,missing_fields_json,uploaded_at,is_seeded)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'2026-01-10',1)""",
        (emp_id, "/tmp/ok.png", "hash_ok", "image", "Accommodation", "Trip", 1,
         "Test Hotel", "Bengaluru", "India", "A", "2026-01-10", "INV-001",
         "INR", 3000.0, 1.0, 3000.0, ev["status"],
         _json.dumps(ev["flags"]), _json.dumps(ev["missing_fields"]))
    )
    db.commit()
    ok_receipt_id = db.execute("SELECT id FROM receipts WHERE file_hash='hash_ok'").fetchone()["id"]

    # One INFO_NEEDED receipt (missing date + total)
    extraction_info = {
        "merchant": "Blur Cafe", "city": "Bengaluru", "country": "India",
        "issue_date": None, "unique_id": "BLUR-002", "currency": "INR",
        "total": None, "subtotal": None, "tax": 0.0, "items": [],
        "items_readable": False, "model_category": "Food",
        "conf": {"issue_date": 0.3, "total": 0.3, "unique_id": 0.9, "city": 0.9, "items": 0.3}
    }
    user_inputs_info = {"user_id": emp_id, "category": "Food", "context": "Trip", "attendees": 1}
    ev2 = evaluate_receipt_rules(extraction_info, user_inputs_info, db=db, is_seeded=True)
    db.execute(
        """INSERT INTO receipts (user_id,file_path,file_hash,source_type,category_user,context,
           attendees,merchant,city,country,tier,issue_date,unique_id,currency,amount_original,
           fx_rate,amount_base,status,flags_json,missing_fields_json,extraction_json,uploaded_at,is_seeded)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'2026-01-10',1)""",
        (emp_id, "/tmp/info.png", "hash_info", "image", "Food", "Trip", 1,
         "Blur Cafe", "Bengaluru", "India", "A", None, "BLUR-002",
         "INR", None, 1.0, None, ev2["status"],
         _json.dumps(ev2["flags"]), _json.dumps(ev2["missing_fields"]),
         _json.dumps(extraction_info))
    )
    db.commit()
    info_receipt_id = db.execute("SELECT id FROM receipts WHERE file_hash='hash_info'").fetchone()["id"]

    # One REJECTED receipt (for query submission)
    extraction_rej = {
        "merchant": "The Pour House", "city": "Bengaluru", "country": "India",
        "issue_date": "2026-01-10", "unique_id": "ALQ-003", "currency": "INR",
        "total": 800.0, "subtotal": 800.0, "tax": 0.0,
        "items": [{"name": "Alcohol", "qty": 2, "amount": 800.0, "is_banned": True, "ban_reason": "alcohol"}],
        "items_readable": True, "model_category": "Food",
        "conf": {"issue_date": 0.95, "total": 0.95, "unique_id": 0.95, "city": 0.95, "items": 0.95}
    }
    user_inputs_rej = {"user_id": emp_id, "category": "Food", "context": "Trip", "attendees": 1}
    ev3 = evaluate_receipt_rules(extraction_rej, user_inputs_rej, db=db, is_seeded=True)
    db.execute(
        """INSERT INTO receipts (user_id,file_path,file_hash,source_type,category_user,context,
           attendees,merchant,city,country,tier,issue_date,unique_id,currency,amount_original,
           fx_rate,amount_base,status,flags_json,missing_fields_json,extraction_json,uploaded_at,is_seeded)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'2026-01-10',1)""",
        (emp_id, "/tmp/rej.png", "hash_rej", "image", "Food", "Trip", 1,
         "The Pour House", "Bengaluru", "India", "A", "2026-01-10", "ALQ-003",
         "INR", 800.0, 1.0, 800.0, ev3["status"],
         _json.dumps(ev3["flags"]), _json.dumps(ev3["missing_fields"]),
         _json.dumps(extraction_rej))
    )
    db.commit()
    rej_receipt_id = db.execute("SELECT id FROM receipts WHERE file_hash='hash_rej'").fetchone()["id"]

    db.close()

    import app as flask_app
    flask_app.app.config["TESTING"] = True
    flask_app.app.config["DATABASE_PATH"] = db_path
    flask_app.app.config["WTF_CSRF_ENABLED"] = False

    with flask_app.app.test_client() as c:
        yield c, ok_receipt_id, info_receipt_id, rej_receipt_id

    os.environ.pop("DATABASE_PATH", None)


def login(c, email, password):
    return c.post("/login", data={"email": email, "password": password}, follow_redirects=True)


def test_employee_routes_no_500(client):
    c, ok_id, info_id, rej_id = client
    login(c, "emp@test.com", "emp123")

    routes = [
        ("GET", "/"),
        ("GET", "/receipts"),
        ("GET", "/upload"),
        ("GET", "/policy"),
        ("GET", "/help"),
        ("GET", f"/receipts/{ok_id}"),
        ("GET", f"/receipts/{info_id}"),
        ("GET", f"/receipts/{rej_id}"),
    ]
    for method, path in routes:
        r = c.get(path) if method == "GET" else c.post(path)
        assert r.status_code != 500, f"500 on {method} {path}: {r.data[:300]}"


def test_complete_info_route_no_500(client):
    """The originally failing route: POST /receipts/<info_id>/complete_info"""
    c, ok_id, info_id, rej_id = client
    login(c, "emp@test.com", "emp123")
    r = c.post(f"/receipts/{info_id}/complete_info", data={
        "total": "500.00",
        "issue_date": "2026-01-10",
        "unique_id": "BLUR-002",
        "attendees": "1",
    }, follow_redirects=True)
    assert r.status_code != 500, f"500 on complete_info: {r.data[:400]}"


def test_query_submission_no_500(client):
    c, ok_id, info_id, rej_id = client
    login(c, "emp@test.com", "emp123")
    r = c.post(f"/receipts/{rej_id}/query",
               data={"message": "This was a client order."}, follow_redirects=True)
    assert r.status_code != 500, f"500 on query submit: {r.data[:300]}"


def test_admin_routes_no_500(client):
    c, ok_id, info_id, rej_id = client
    login(c, "admin@test.com", "admin123")

    routes = [
        "/admin",
        "/admin?view=employee",
        "/admin?view=category",
        "/admin/rejected",
        "/admin/settings",
        f"/admin/receipt/{ok_id}",
        f"/admin/receipt/{info_id}",
        f"/admin/receipt/{rej_id}",
        "/admin/export/csv",
    ]
    for path in routes:
        r = c.get(path)
        assert r.status_code != 500, f"500 on GET {path}: {r.data[:300]}"


def test_admin_decision_no_500(client):
    c, ok_id, info_id, rej_id = client
    login(c, "admin@test.com", "admin123")
    r = c.post(f"/admin/receipt/{ok_id}/decide",
               data={"decision": "approve", "reason": "Looks good"}, follow_redirects=True)
    assert r.status_code != 500, f"500 on admin decide: {r.data[:300]}"


def test_admin_settings_update_no_500(client):
    c, ok_id, info_id, rej_id = client
    login(c, "admin@test.com", "admin123")
    r = c.post("/admin/settings",
               data={"currency": "MYR", "rate_to_inr": "18.5", "set_date": "2026-01-01"},
               follow_redirects=True)
    assert r.status_code != 500, f"500 on settings update: {r.data[:300]}"
