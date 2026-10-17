import os
import csv
import sqlite3
import pytest
from database.db import init_db
from services.extractor import extract_receipt
from services.rules import evaluate_receipt_rules

@pytest.fixture
def oracle_db():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    # Seed 4 demo employees
    conn.execute("INSERT INTO users (id, name, email, password_hash, role, employee_code) VALUES (1, 'E1 User', 'e1@company.com', 'hash', 'employee', 'E1')")
    conn.execute("INSERT INTO users (id, name, email, password_hash, role, employee_code) VALUES (2, 'E2 User', 'e2@company.com', 'hash', 'employee', 'E2')")
    conn.execute("INSERT INTO users (id, name, email, password_hash, role, employee_code) VALUES (3, 'E3 User', 'e3@company.com', 'hash', 'employee', 'E3')")
    conn.execute("INSERT INTO users (id, name, email, password_hash, role, employee_code) VALUES (4, 'E4 User', 'e4@company.com', 'hash', 'employee', 'E4')")
    conn.commit()
    return conn

def test_all_planted_cases_match_manifest(oracle_db):
    manifest_path = "sample_data/planted/manifest.csv"
    assert os.path.exists(manifest_path)

    with open(manifest_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    emp_id_map = {"E1": 1, "E2": 2, "E3": 3, "E4": 4}

    for row in rows:
        filename = row["file"]
        filepath = os.path.join("sample_data/planted", filename)
        assert os.path.exists(filepath), f"File {filepath} not found"

        with open(filepath, "rb") as fp:
            file_bytes = fp.read()

        extracted = extract_receipt(file_bytes, filename, db=oracle_db)
        
        emp_code = row["employee"]
        user_id = emp_id_map.get(emp_code, 1)
        user_category = row["user_category"]
        context = row["context"]
        attendees = row["attendees"] if row["attendees"] else None
        client = row["client"] if row["client"] else None

        inputs = {
            "user_id": user_id,
            "category": user_category,
            "context": context,
            "attendees": attendees,
            "client_name": client,
            "is_client_related": bool(client)
        }

        # For old_receipt.png: manifest specifies is_seeded=0 required to test rejection
        is_seeded = (filename != "old_receipt.png")

        res = evaluate_receipt_rules(
            extracted,
            inputs,
            db=oracle_db,
            is_seeded=is_seeded
        )

        expected = row["expected_outcome"].lower()
        actual_status = res["status"].lower()
        
        # Check matching expected outcome
        if "verified" in expected:
            assert actual_status == "verified", f"{filename}: expected {expected}, got {actual_status} (flags: {res['flags']})"
        elif "rejected" in expected:
            assert actual_status == "rejected", f"{filename}: expected {expected}, got {actual_status} (flags: {res['flags']})"
        elif "flagged" in expected:
            assert actual_status == "flagged", f"{filename}: expected {expected}, got {actual_status}"
        elif "info needed" in expected or "info_needed" in expected:
            assert actual_status == "info_needed", f"{filename}: expected {expected}, got {actual_status}"

        # Insert into oracle_db so subsequent duplicate or ledger checks can see earlier receipts
        oracle_db.execute(
            """INSERT INTO receipts (
                user_id, file_path, file_hash, source_type, category_user, category_model, context,
                attendees, issue_date, unique_id, currency, amount_original, amount_base, status,
                dup_group_id, uploaded_at, is_seeded
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)""",
            (
                user_id, filepath, extracted.get("file_hash", filename),
                "pdf" if filename.endswith(".pdf") else "image",
                user_category, extracted.get("model_category"), context,
                int(attendees) if attendees else 1,
                extracted.get("issue_date"), extracted.get("unique_id"),
                res.get("currency"), res.get("amount_original"), res.get("amount_base"),
                res["status"], res.get("dup_group_id"), 1 if is_seeded else 0
            )
        )
        oracle_db.commit()
