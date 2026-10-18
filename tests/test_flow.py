import io
import pytest
import sqlite3
from app import app
from database.db import init_db
from services.auth import hash_password

@pytest.fixture
def client(tmp_path):
    test_db_path = tmp_path / "test_flow.db"
    init_db(str(test_db_path))

    conn = sqlite3.connect(str(test_db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """INSERT INTO users (id, name, email, password_hash, role, employee_code) VALUES
        (1, 'Admin User', 'admin@company.com', ?, 'admin', 'ADM01'),
        (2, 'Alex Rivera', 'e1@company.com', ?, 'employee', 'E1')""",
        (hash_password("admin123"), hash_password("emp123"))
    )
    conn.commit()
    conn.close()

    app.config["TESTING"] = True
    app.config["DATABASE_PATH"] = str(test_db_path)
    
    with app.test_client() as client:
        yield client

def test_full_upload_and_admin_approval_flow(client):
    # 1. Employee logs in
    client.post("/login", data={"email": "e1@company.com", "password": "emp123"}, follow_redirects=True)

    # 2. Upload a valid receipt dated within the 15-day window
    import datetime
    from services.extractor import compute_file_hash, save_cached_extraction
    
    recent_date = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()
    test_bytes = b"MOCK_RECENT_RECEIPT_CONTENT"
    test_hash = compute_file_hash(test_bytes)
    
    mock_extraction = {
        "merchant": "Hotel Sunrise Residency",
        "city": "Bengaluru",
        "country": "India",
        "issue_date": recent_date,
        "unique_id": "HSR-LIVE-01",
        "currency": "INR",
        "total": 4890.0,
        "subtotal": 4400.0,
        "tax": 490.0,
        "nights": 1,
        "items": [{"name": "Room 1 night", "qty": 1, "amount": 4400.0, "banned_match": None, "banned_conf": 0.0}],
        "items_readable": True,
        "model_category": "Accommodation",
        "alteration_notes": None,
        "conf": {"issue_date": 0.98, "total": 0.98, "unique_id": 0.98, "city": 0.95, "items": 0.95},
        "image_quality": "high"
    }
    
    # Save into DB cache
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    save_cached_extraction(test_hash, "test_model", mock_extraction, db=conn)
    conn.close()

    upload_data = {
        "receipt_file": (io.BytesIO(test_bytes), "recent_hotel.png"),
        "category": "Accommodation",
        "context": "Trip",
        "attendees": "1"
    }
    res_up = client.post("/upload", data=upload_data, content_type="multipart/form-data", follow_redirects=True)
    assert res_up.status_code == 200
    assert b"Policy Verification Outcome" in res_up.data
    assert b"Verified" in res_up.data

    # 3. Admin logs in
    client.get("/logout")
    client.post("/login", data={"email": "admin@company.com", "password": "admin123"}, follow_redirects=True)

    # 4. Check admin queue
    res_q = client.get("/admin")
    assert res_q.status_code == 200
    assert b"Hotel Sunrise Residency" in res_q.data or b"#1" in res_q.data

    # 5. Admin approves with non-empty reason
    res_dec = client.post("/admin/receipt/1/decide", data={
        "decision": "approved",
        "reason": "Business trip accommodation verified within limits."
    }, follow_redirects=True)
    assert res_dec.status_code == 200
    assert b"Receipt #1 has been approved" in res_dec.data

    # 6. Verify CSV export includes approved receipt
    res_csv = client.get("/admin/export/csv")
    assert res_csv.status_code == 200
    assert res_csv.mimetype == "text/csv"
    assert b"Hotel Sunrise Residency" in res_csv.data or b"8968.0" in res_csv.data

def test_rejection_query_and_overturn_flow(client):
    # 1. Employee uploads alcohol_bar.png
    client.post("/login", data={"email": "e1@company.com", "password": "emp123"}, follow_redirects=True)

    with open("sample_data/planted/alcohol_bar.png", "rb") as fp:
        img_bytes = fp.read()

    upload_data = {
        "receipt_file": (io.BytesIO(img_bytes), "alcohol_bar.png"),
        "category": "Food",
        "context": "Trip",
        "attendees": "3"
    }
    res_up = client.post("/upload", data=upload_data, content_type="multipart/form-data", follow_redirects=True)
    assert res_up.status_code == 200
    assert b"Auto-Rejected" in res_up.data

    # 2. Employee submits query
    res_q = client.post("/receipts/1/query", data={
        "message": "The whisky was ordered by a client; please review."
    }, follow_redirects=True)
    assert res_q.status_code == 200
    assert b"Your clarification query has been sent" in res_q.data

    # 3. Admin logs in and reviews query
    client.get("/logout")
    client.post("/login", data={"email": "admin@company.com", "password": "admin123"}, follow_redirects=True)

    res_rej = client.get("/admin/rejected")
    assert res_rej.status_code == 200
    assert b"The whisky was ordered by a client" in res_rej.data

    # 4. Admin overturns query with reason
    res_res = client.post("/admin/query/1/resolve", data={
        "resolution": "overturn",
        "resolution_reason": "Exception granted for official client entertainment."
    }, follow_redirects=True)
    assert res_res.status_code == 200
    assert b"overturned" in res_res.data.lower()

    # Verify receipt is now approved
    res_csv = client.get("/admin/export/csv")
    assert b"2420.0" in res_csv.data

def test_decision_reason_rules(client):
    """SPEC s9: approve works with no reason; deny and uphold are rejected without a reason."""
    # 1. Setup receipt awaiting admin review
    client.post("/login", data={"email": "e1@company.com", "password": "emp123"}, follow_redirects=True)
    with open("sample_data/planted/hotel_ok.png", "rb") as fp:
        img_bytes = fp.read()
    client.post("/upload", data={
        "receipt_file": (io.BytesIO(img_bytes), "hotel_ok.png"),
        "category": "Accommodation",
        "context": "Trip"
    }, follow_redirects=True)

    # 2. Login as admin
    client.get("/logout")
    client.post("/login", data={"email": "admin@company.com", "password": "admin123"}, follow_redirects=True)

    # Deny without reason -> rejected / flashes error
    res_deny_no_reason = client.post("/admin/receipt/1/decide", data={
        "decision": "denied",
        "reason": ""
    }, follow_redirects=True)
    assert b"written reason is required when denying" in res_deny_no_reason.data

    # Approve without reason -> succeeds!
    res_approve_no_reason = client.post("/admin/receipt/1/decide", data={
        "decision": "approved",
        "reason": ""
    }, follow_redirects=True)
    assert res_approve_no_reason.status_code == 200
    assert b"approved" in res_approve_no_reason.data.lower()
