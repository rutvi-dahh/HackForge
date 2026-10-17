import pytest
from database.db import init_db, get_db
from services.rules import evaluate_receipt_rules

import sqlite3

@pytest.fixture
def test_db():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    conn.execute(
        "INSERT INTO users (id, name, email, password_hash, role, employee_code) VALUES (1, 'Alice Test', 'alice@test.com', 'hash', 'employee', 'E1')"
    )
    conn.commit()
    return conn

def test_cumulative_monthly_limit_math(test_db):
    # Food monthly limit is 15,000 INR
    # Insert 3 receipts of 4,000 INR (total 12,000 INR) in March 2018
    for i in range(1, 4):
        test_db.execute(
            """INSERT INTO receipts (
                user_id, file_path, file_hash, source_type, category_user, context, attendees,
                issue_date, unique_id, currency, amount_original, amount_base, status, uploaded_at
            ) VALUES (
                1, 'path.jpg', ?, 'image', 'Food', 'Trip', 1,
                '2018-03-10', ?, 'INR', 4000.0, 4000.0, 'approved', '2018-03-10'
            )""",
            (f"hash_{i}", f"INV-{i}")
        )
    test_db.commit()

    # Now evaluate 4th receipt of 4,000 INR (12,000 + 4,000 = 16,000 > 15,000 limit)
    receipt_4th = {
        "merchant": "Cafe Four",
        "city": "Bengaluru",
        "issue_date": "2018-03-25",
        "unique_id": "INV-4",
        "currency": "INR",
        "total": 4000.0,
        "items": [{"name": "Catering", "qty": 1, "amount": 4000.0}],
        "model_category": "Food",
        "conf": {"issue_date": 0.95, "total": 0.95, "unique_id": 0.95, "city": 0.95, "items": 0.95}
    }
    inputs = {"category": "Food", "context": "Trip", "attendees": 10, "user_id": 1} # 4000/10 = 400 per person (well within per-meal limit)
    res = evaluate_receipt_rules(receipt_4th, inputs, db=test_db, is_seeded=True)
    
    assert res["status"] == "flagged"
    flag_codes = [f["code"] for f in res["flags"]]
    assert "LIMIT_MONTH" in flag_codes
    month_flag = next(f for f in res["flags"] if f["code"] == "LIMIT_MONTH")
    # Verify math in message: 12,000 + 4,000 = 16,000
    assert "12,000" in month_flag["message"] or "12000" in month_flag["message"]
    assert "16,000" in month_flag["message"] or "16000" in month_flag["message"]
    assert "15,000" in month_flag["message"] or "15000" in month_flag["message"]

def test_excluded_statuses_do_not_count_in_ledger(test_db):
    # Insert a rejected and a denied receipt of 10,000 INR
    test_db.execute(
        """INSERT INTO receipts (
            user_id, file_path, file_hash, source_type, category_user, context, attendees,
            issue_date, unique_id, currency, amount_original, amount_base, status, uploaded_at
        ) VALUES (
            1, 'path.jpg', 'hash_rej', 'image', 'Food', 'Trip', 1,
            '2018-03-10', 'REJ-1', 'INR', 10000.0, 10000.0, 'rejected', '2018-03-10'
        )"""
    )
    test_db.execute(
        """INSERT INTO receipts (
            user_id, file_path, file_hash, source_type, category_user, context, attendees,
            issue_date, unique_id, currency, amount_original, amount_base, status, uploaded_at
        ) VALUES (
            1, 'path.jpg', 'hash_den', 'image', 'Food', 'Trip', 1,
            '2018-03-11', 'DEN-1', 'INR', 10000.0, 10000.0, 'denied', '2018-03-11'
        )"""
    )
    test_db.commit()

    # Now evaluate a new receipt of 4,000 INR -> should NOT trigger LIMIT_MONTH because 10k receipts are rejected/denied
    receipt = {
        "merchant": "Cafe Ok",
        "city": "Bengaluru",
        "issue_date": "2018-03-15",
        "unique_id": "OK-1",
        "currency": "INR",
        "total": 4000.0,
        "items": [{"name": "Food", "qty": 1, "amount": 4000.0}],
        "model_category": "Food",
        "conf": {"issue_date": 0.95, "total": 0.95, "unique_id": 0.95, "city": 0.95, "items": 0.95}
    }
    inputs = {"category": "Food", "context": "Trip", "attendees": 10, "user_id": 1}
    res = evaluate_receipt_rules(receipt, inputs, db=test_db, is_seeded=True)
    flag_codes = [f["code"] for f in res["flags"]]
    assert "LIMIT_MONTH" not in flag_codes
