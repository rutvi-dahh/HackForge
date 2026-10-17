import datetime
import pytest
from services.rules import evaluate_receipt_rules

def test_banned_item_rejection():
    receipt = {
        "merchant": "Pub",
        "city": "Bengaluru",
        "issue_date": "2026-10-10",
        "unique_id": "PUB-101",
        "currency": "INR",
        "total": 1200.0,
        "items": [
            {"name": "Burger", "qty": 1, "amount": 300.0, "banned_match": None, "banned_conf": 0.0},
            {"name": "Whisky", "qty": 1, "amount": 900.0, "banned_match": "alcohol", "banned_conf": 0.95}
        ],
        "conf": {"issue_date": 0.95, "total": 0.95, "unique_id": 0.95, "city": 0.9, "items": 0.9}
    }
    inputs = {"category": "Food", "context": "Trip", "attendees": 1}
    res = evaluate_receipt_rules(receipt, inputs, is_seeded=False)
    assert res["status"] == "rejected"
    assert res["rejection_reason"]["code"] == "NON_REIMBURSABLE"
    assert "alcohol" in res["rejection_reason"]["message"].lower()

def test_late_upload_15_day_boundary():
    # Today is 2026-10-18
    today = datetime.date(2026, 10, 18)
    
    # Day 15: 2026-10-03 -> difference = 15 days -> VALID (not rejected)
    receipt_day15 = {
        "merchant": "Cafe",
        "city": "Bengaluru",
        "issue_date": "2026-10-03",
        "unique_id": "C-15",
        "currency": "INR",
        "total": 300.0,
        "items": [{"name": "Coffee", "qty": 1, "amount": 300.0}],
        "conf": {"issue_date": 0.95, "total": 0.95, "unique_id": 0.95, "city": 0.9, "items": 0.9}
    }
    inputs = {"category": "Food", "context": "Office", "attendees": 1}
    res15 = evaluate_receipt_rules(receipt_day15, inputs, upload_date=today, is_seeded=False)
    assert res15["status"] != "rejected"

    # Day 16: 2026-10-02 -> difference = 16 days -> REJECTED
    receipt_day16 = dict(receipt_day15)
    receipt_day16["issue_date"] = "2026-10-02"
    receipt_day16["unique_id"] = "C-16"
    res16 = evaluate_receipt_rules(receipt_day16, inputs, upload_date=today, is_seeded=False)
    assert res16["status"] == "rejected"
    assert res16["rejection_reason"]["code"] == "LATE_UPLOAD"

    # Seeded exemption: day 16 with is_seeded=True -> NOT rejected
    res_seeded = evaluate_receipt_rules(receipt_day16, inputs, upload_date=today, is_seeded=True)
    assert res_seeded["status"] != "rejected"

def test_info_needed_missing_fields():
    # Missing date and total
    receipt = {
        "merchant": "Corner Cafe",
        "city": "Bengaluru",
        "issue_date": None,
        "unique_id": "U-1",
        "currency": "INR",
        "total": 0.0,
        "conf": {"issue_date": 0.0, "total": 0.0, "unique_id": 0.9, "city": 0.9, "items": 0.0}
    }
    inputs = {"category": "Food", "context": "Office", "attendees": 1}
    res = evaluate_receipt_rules(receipt, inputs)
    assert res["status"] == "info_needed"
    assert "issue_date" in res["missing_fields"]
    assert "total" in res["missing_fields"]

def test_food_attendee_validation():
    receipt = {
        "merchant": "Restaurant",
        "city": "Bengaluru",
        "issue_date": "2026-10-17",
        "unique_id": "REST-1",
        "currency": "INR",
        "total": 500.0,
        "items": [{"name": "Thali", "qty": 1, "amount": 500.0}],
        "conf": {"issue_date": 0.9, "total": 0.9, "unique_id": 0.9, "city": 0.9, "items": 0.9}
    }
    # No attendee count provided for Food
    inputs_no_att = {"category": "Food", "context": "Trip"}
    res_no_att = evaluate_receipt_rules(receipt, inputs_no_att)
    assert res_no_att["status"] == "info_needed"
    assert "attendees" in res_no_att["missing_fields"]

    # Attendee count < 1
    inputs_zero_att = {"category": "Food", "context": "Trip", "attendees": 0}
    res_zero = evaluate_receipt_rules(receipt, inputs_zero_att)
    assert res_zero["status"] == "info_needed"
    assert "attendees" in res_zero["missing_fields"]

def test_client_name_required_when_checked():
    receipt = {
        "merchant": "Bistro",
        "city": "Bengaluru",
        "issue_date": "2026-10-17",
        "unique_id": "B-1",
        "currency": "INR",
        "total": 500.0,
        "items": [{"name": "Lunch", "qty": 1, "amount": 500.0}],
        "conf": {"issue_date": 0.9, "total": 0.9, "unique_id": 0.9, "city": 0.9, "items": 0.9}
    }
    inputs = {"category": "Food", "context": "Trip", "attendees": 2, "is_client_related": True, "client_name": ""}
    res = evaluate_receipt_rules(receipt, inputs)
    assert res["status"] == "info_needed"
    assert "client_name" in res["missing_fields"]

def test_flags_items_unclear_and_math_mismatch():
    receipt = {
        "merchant": "Store",
        "city": "Delhi",
        "issue_date": "2026-10-17",
        "unique_id": "ST-99",
        "currency": "INR",
        "total": 980.0,
        "items": [
            {"name": "Paper", "qty": 1, "amount": 450.0},
            {"name": "Stapler", "qty": 1, "amount": 180.0}
        ], # Sum is 630.0 != 980.0
        "conf": {"issue_date": 0.9, "total": 0.9, "unique_id": 0.9, "city": 0.9, "items": 0.9}
    }
    inputs = {"category": "Miscellaneous", "context": "Office"}
    res = evaluate_receipt_rules(receipt, inputs)
    assert res["status"] == "flagged"
    flag_codes = [f["code"] for f in res["flags"]]
    assert "MATH_MISMATCH" in flag_codes

def test_accommodation_office_flag():
    receipt = {
        "merchant": "Hotel Lemon",
        "city": "Gurgaon",
        "issue_date": "2026-10-17",
        "unique_id": "HL-1",
        "currency": "INR",
        "total": 4000.0,
        "nights": 1,
        "items": [{"name": "Room", "qty": 1, "amount": 4000.0}],
        "conf": {"issue_date": 0.9, "total": 0.9, "unique_id": 0.9, "city": 0.9, "items": 0.9}
    }
    inputs = {"category": "Accommodation", "context": "Office"}
    res = evaluate_receipt_rules(receipt, inputs)
    assert res["status"] == "flagged"
    assert any(f["code"] == "ACCOMMODATION_OFFICE" for f in res["flags"])

def test_category_mismatch_flag():
    receipt = {
        "merchant": "Bookstore",
        "city": "Mumbai",
        "issue_date": "2026-10-17",
        "unique_id": "BK-1",
        "currency": "INR",
        "total": 500.0,
        "model_category": "Miscellaneous",
        "items": [{"name": "Pens", "qty": 1, "amount": 500.0}],
        "conf": {"issue_date": 0.9, "total": 0.9, "unique_id": 0.9, "city": 0.9, "items": 0.9}
    }
    inputs = {"category": "Food", "context": "Office", "attendees": 1}
    res = evaluate_receipt_rules(receipt, inputs)
    assert res["status"] == "flagged"
    assert any(f["code"] == "CATEGORY_MISMATCH" for f in res["flags"])

def test_receipt_limits_boundary():
    # Food Trip Tier A meal limit is 800 INR per person
    # Test 799 (just under) -> Verified
    r_under = {
        "merchant": "Diner",
        "city": "Mumbai", # Tier A
        "issue_date": "2026-10-17",
        "unique_id": "D-799",
        "currency": "INR",
        "total": 799.0,
        "items": [{"name": "Lunch", "qty": 1, "amount": 799.0}],
        "model_category": "Food",
        "conf": {"issue_date": 0.9, "total": 0.9, "unique_id": 0.9, "city": 0.9, "items": 0.9}
    }
    inputs = {"category": "Food", "context": "Trip", "attendees": 1}
    res_under = evaluate_receipt_rules(r_under, inputs)
    assert res_under["status"] == "verified"
    assert not any(f["code"] == "LIMIT_RECEIPT" for f in res_under["flags"])

    # Test 800 (exactly at) -> Verified
    r_exact = dict(r_under)
    r_exact["total"] = 800.0
    r_exact["unique_id"] = "D-800"
    r_exact["items"] = [{"name": "Lunch", "qty": 1, "amount": 800.0}]
    res_exact = evaluate_receipt_rules(r_exact, inputs)
    assert not any(f["code"] == "LIMIT_RECEIPT" for f in res_exact["flags"])

    # Test 801 (just over) -> Flagged with LIMIT_RECEIPT
    r_over = dict(r_under)
    r_over["total"] = 801.0
    r_over["unique_id"] = "D-801"
    r_over["items"] = [{"name": "Lunch", "qty": 1, "amount": 801.0}]
    res_over = evaluate_receipt_rules(r_over, inputs)
    assert res_over["status"] == "flagged"
    assert any(f["code"] == "LIMIT_RECEIPT" for f in res_over["flags"])

def test_suspicious_round_total():
    # 10,000 INR round total
    receipt = {
        "merchant": "Banquet",
        "city": "Mumbai",
        "issue_date": "2026-10-17",
        "unique_id": "BQ-10K",
        "currency": "INR",
        "total": 10000.0,
        "items": [{"name": "Buffet", "qty": 1, "amount": 10000.0}],
        "conf": {"issue_date": 0.9, "total": 0.9, "unique_id": 0.9, "city": 0.9, "items": 0.9}
    }
    inputs = {"category": "Food", "context": "Trip", "attendees": 1}
    res = evaluate_receipt_rules(receipt, inputs)
    assert any(f["code"] == "SUSPICIOUS_ROUND_TOTAL" for f in res["flags"])
