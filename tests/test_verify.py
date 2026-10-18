"""
tests/test_verify.py — Self-verification (voting) & City-tier unit tests (SPEC s4, s5, s9)
"""

import sqlite3
import pytest
from database.db import init_db
from services.verify import verify_receipt, mask_card_numbers, amounts_agree, invoice_ids_agree
from services.cities import resolve_city_tier

def test_verification_wrong_typed_amount_flagged():
    """SPEC s9: wrong typed amount is flagged; two readers agreeing against user."""
    user_inputs = {"claimed_amount": 5000.0, "category": "Food"}
    extracted = {"total": 500.0, "unique_id": "INV-1", "model_category": "Food"}
    ocr_parsed = {"amount": 500.0, "invoice_no": "INV-1"}
    
    res = verify_receipt(user_inputs, extracted, ocr_parsed)
    assert res["accepted_amount"] == 500.0
    assert res["amount_source"] == "model"
    assert any(f["code"] == "AMOUNT_MISMATCH" for f in res["flags"])
    mismatch_flag = next(f for f in res["flags"] if f["code"] == "AMOUNT_MISMATCH")
    assert "Typed 5,000.00; receipt shows 500.00" in mismatch_flag["message"]

def test_verification_reader_2_none_causes_no_flag():
    """SPEC s9: reader 2 None causes no flag."""
    user_inputs = {"claimed_amount": 500.0, "category": "Food"}
    extracted = {"total": 500.0, "unique_id": "INV-1", "model_category": "Food"}
    ocr_parsed = {"amount": None, "invoice_no": None}
    
    res = verify_receipt(user_inputs, extracted, ocr_parsed)
    assert res["accepted_amount"] == 500.0
    assert not any(f["code"] == "AMOUNT_MISMATCH" for f in res["flags"])
    assert not any(f["code"] == "EXTRACTION_UNCERTAIN" for f in res["flags"])

def test_verification_two_readers_agree_on_invoice_against_user():
    """SPEC s9: two readers agreeing against user on invoice number is flagged."""
    user_inputs = {"claimed_invoice_no": "WRONG-999"}
    extracted = {"total": 100.0, "unique_id": "INV-1001", "model_category": "Food"}
    ocr_parsed = {"amount": 100.0, "invoice_no": "INV-1001"}
    
    res = verify_receipt(user_inputs, extracted, ocr_parsed)
    assert res["accepted_invoice_no"] == "INV-1001"
    assert any(f["code"] == "INVOICE_ID_MISMATCH" for f in res["flags"])

def test_verification_no_majority_gives_extraction_uncertain():
    """SPEC s9: no majority gives EXTRACTION_UNCERTAIN."""
    user_inputs = {"claimed_amount": 100.0, "category": "Food"}
    extracted = {"total": 200.0, "unique_id": "INV-1", "model_category": "Food"}
    ocr_parsed = {"amount": 300.0, "invoice_no": "INV-1"}
    
    res = verify_receipt(user_inputs, extracted, ocr_parsed)
    assert any(f["code"] == "EXTRACTION_UNCERTAIN" for f in res["flags"])

def test_verification_accepted_amount_equals_receipt_value_in_every_case():
    """SPEC s9: accepted amount equals the receipt value in every case."""
    # User claimed 5000, receipt readers both say 500 -> accepted is 500
    user_inputs = {"claimed_amount": 5000.0, "category": "Food"}
    extracted = {"total": 500.0, "unique_id": "INV-1", "model_category": "Food"}
    ocr_parsed = {"amount": 500.0, "invoice_no": "INV-1"}
    res = verify_receipt(user_inputs, extracted, ocr_parsed)
    assert res["accepted_amount"] == 500.0

    # User claimed 500, model 500, ocr None -> accepted is 500
    user_inputs2 = {"claimed_amount": 500.0}
    extracted2 = {"total": 500.0}
    ocr_parsed2 = {"amount": None}
    res2 = verify_receipt(user_inputs2, extracted2, ocr_parsed2)
    assert res2["accepted_amount"] == 500.0

def test_verification_single_reader_category_disagreement_does_not_flag():
    """SPEC s9: a single-reader category disagreement does not flag."""
    # Case A: User says Food, model says Food, but keyword read suggests Travel
    user_inputs = {"category": "Food"}
    extracted = {
        "total": 100.0,
        "model_category": "Food",
        "merchant": "Airport Cafe",
        "items": [{"name": "Airport taxi fare and sandwich", "amount": 100.0}]
    }
    ocr_text = "Airport taxi fare 100"
    res = verify_receipt(user_inputs, extracted, ocr_parsed={}, ocr_text=ocr_text)
    assert not any(f["code"] == "CATEGORY_MISMATCH" for f in res["flags"])

    # Case B: User says Food, model hallucinated Equipment, but keyword read agrees with user on Food
    extracted_b = {
        "total": 100.0,
        "model_category": "Equipment",
        "merchant": "Tasty Food Restaurant",
        "items": [{"name": "Lunch buffet", "amount": 100.0}]
    }
    ocr_text_b = "Tasty Food Restaurant Lunch buffet dining meal"
    res_b = verify_receipt(user_inputs, extracted_b, ocr_parsed={}, ocr_text=ocr_text_b)
    assert not any(f["code"] == "CATEGORY_MISMATCH" for f in res_b["flags"])

def test_verification_no_reader_finds_amount_typed_used_and_flagged():
    """SPEC s9: when no reader finds an amount, typed value is used and EXTRACTION_UNCERTAIN is set."""
    user_inputs = {"claimed_amount": 750.0, "category": "Food"}
    extracted = {"total": None, "unique_id": None}
    ocr_parsed = {"amount": None, "invoice_no": None}
    
    res = verify_receipt(user_inputs, extracted, ocr_parsed)
    assert res["accepted_amount"] == 750.0
    assert res["amount_source"] == "user"
    assert any(f["code"] == "EXTRACTION_UNCERTAIN" for f in res["flags"])

def test_card_number_masking():
    """SPEC s4 & s7: card numbers masked keeping only the last 4 digits."""
    raw = "Paid via VISA 4111 2222 3333 4444 on terminal 1"
    masked = mask_card_numbers(raw)
    assert "4111" not in masked
    assert "2222" not in masked
    assert "3333" not in masked
    assert "4444" in masked
    assert "XXXX-XXXX-XXXX-4444" in masked

def test_city_tier_model_classification_and_cache(monkeypatch):
    """SPEC s9: city tier model called once per city; second lookup hits cache."""
    conn = sqlite3.connect(":memory:")
    init_db(conn)

    calls = []
    def mock_classify(city, country):
        calls.append(city)
        return {"canonical_city": city.title(), "tier": "B", "confidence": 0.85}

    import services.cities
    monkeypatch.setattr(services.cities, "classify_city_with_model", mock_classify)

    # First lookup: new city -> calls model
    c1, t1, u1 = resolve_city_tier("Reykjavik", "Iceland", db=conn)
    assert c1 == "Reykjavik"
    assert t1 == "B"
    assert not u1
    assert len(calls) == 1

    # Second lookup: same city -> hits cache, does not call model
    c2, t2, u2 = resolve_city_tier("reykjavik", "Iceland", db=conn)
    assert c2 == "Reykjavik"
    assert t2 == "B"
    assert not u2
    assert len(calls) == 1  # Still 1 call!

def test_city_tier_failure_or_low_confidence_gives_tier_c_and_city_unclear(monkeypatch):
    """SPEC s9: API failure or low confidence gives Tier C and CITY_UNCLEAR, without caching."""
    conn = sqlite3.connect(":memory:")
    init_db(conn)

    # Simulate low confidence
    def mock_low_conf(city, country):
        return {"canonical_city": city, "tier": "A", "confidence": 0.4}

    import services.cities
    monkeypatch.setattr(services.cities, "classify_city_with_model", mock_low_conf)

    c, t, is_unclear = resolve_city_tier("Atlantis", "Ocean", db=conn)
    assert t == "C"
    assert is_unclear is True

    # Verify not cached
    row = conn.execute("SELECT * FROM city_tiers WHERE canonical_city = 'Atlantis'").fetchone()
    assert row is None
