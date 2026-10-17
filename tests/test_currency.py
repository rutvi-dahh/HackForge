import pytest
from database.db import init_db, get_db
from services.currency import resolve_currency, convert_to_base

import sqlite3

@pytest.fixture
def test_db():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    return conn

def test_resolve_currency_symbols_and_inference():
    assert resolve_currency("INR") == "INR"
    assert resolve_currency("₹") == "INR"
    assert resolve_currency("RM") == "MYR"
    assert resolve_currency("MYR") == "MYR"
    assert resolve_currency("USD") == "USD"
    
    # Ambiguous $ on Malaysian receipt inferred from country
    assert resolve_currency("$", country="Malaysia") == "MYR"
    assert resolve_currency("unknown", country="United States") == "USD"
    assert resolve_currency("", merchant="Kuala Lumpur Mart") == "MYR"

def test_currency_conversion(test_db):
    # INR to INR (rate 1.0)
    amt, rate, set_date, err = convert_to_base(100.0, "INR", db=test_db)
    assert amt == 100.0
    assert rate == 1.0
    assert err is None

    # MYR to INR (rate 23.4): 10.0 MYR = 234.0 INR
    amt_myr, rate_myr, set_date_myr, err = convert_to_base(10.0, "MYR", db=test_db)
    assert amt_myr == 234.0
    assert rate_myr == 23.4
    assert set_date_myr == "2026-10-17"

    # USD to INR (rate 96.5): 10.0 USD = 965.0 INR
    amt_usd, rate_usd, set_date_usd, err = convert_to_base(10.0, "USD", db=test_db)
    assert amt_usd == 965.0
    assert rate_usd == 96.5
    assert set_date_usd == "2026-10-17"
