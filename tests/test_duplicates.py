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

def test_duplicate_detection_by_unique_id_and_grouping(test_db):
    # Insert first receipt (e.g. PDF invoice)
    test_db.execute(
        """INSERT INTO receipts (
            id, user_id, file_path, file_hash, source_type, category_user, context, attendees,
            issue_date, unique_id, currency, amount_original, amount_base, status, uploaded_at
        ) VALUES (
            1, 1, 'invoice.pdf', 'hash_pdf', 'pdf', 'Equipment', 'Office', 1,
            '2018-03-15', 'INV-2018-0457', 'INR', 9440.0, 9440.0, 'verified', '2018-03-15 10:00:00'
        )"""
    )
    test_db.commit()

    # Second receipt (e.g. photo of the same invoice)
    photo_receipt = {
        "file_hash": "hash_photo_different",
        "merchant": "Apex Office Supplies",
        "city": "Bengaluru",
        "issue_date": "2018-03-15",
        "unique_id": "INV-2018-0457", # Same unique ID!
        "currency": "INR",
        "total": 9440.0,
        "items": [
            {"name": "Ergonomic chair", "qty": 1, "amount": 6800.0},
            {"name": "Desk lamp", "qty": 1, "amount": 1200.0}
        ],
        "model_category": "Equipment",
        "conf": {"issue_date": 0.98, "total": 0.98, "unique_id": 0.98, "city": 0.95, "items": 0.95}
    }
    inputs = {"category": "Equipment", "context": "Office", "user_id": 1}
    res = evaluate_receipt_rules(photo_receipt, inputs, db=test_db, is_seeded=True)

    assert res["status"] == "flagged"
    assert any(f["code"] == "DUPLICATE" for f in res["flags"])
    assert res["dup_group_id"] is not None
    assert "dup_cluster_1" in res["dup_group_id"]
