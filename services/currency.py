from database.db import get_fx_rate, get_setting

COUNTRY_CURRENCY_MAP = {
    "malaysia": "MYR",
    "my": "MYR",
    "united states": "USD",
    "usa": "USD",
    "us": "USD",
    "india": "INR",
    "in": "INR"
}

def resolve_currency(currency, country=None, merchant=None):
    """
    Resolves currency string. If ambiguous or unknown, infers from country or merchant context.
    Returns canonical currency code or 'UNKNOWN'.
    """
    c = (currency or "").strip().upper()
    
    # Handle common symbols
    if c in ["₹", "RS", "RS.", "INR"]:
        return "INR"
    if c in ["RM", "MYR"]:
        return "MYR"
    if c in ["USD", "US$"]:
        return "USD"
    if c in ["$", "UNKNOWN", ""]:
        # Disambiguate from country
        cntry = (country or "").strip().lower()
        if cntry in COUNTRY_CURRENCY_MAP:
            return COUNTRY_CURRENCY_MAP[cntry]
        
        # Check if country name is mentioned in merchant
        if merchant:
            m = merchant.lower()
            if "malaysia" in m or "kuala lumpur" in m or "selangor" in m:
                return "MYR"
            if "usa" in m or "united states" in m or "inc." in m:
                return "USD"
            if "india" in m or "ltd" in m or "pvt" in m:
                return "INR"
        return "UNKNOWN"
        
    return c

def convert_to_base(amount, currency, db=None):
    """
    Converts amount to base currency (INR by default).
    Returns: (amount_base, fx_rate_used, set_date, error_message)
    """
    if amount is None:
        return 0.0, 1.0, None, "Amount is missing"

    base_currency = get_setting("BASE_CURRENCY", "INR", db=db).upper()
    curr = (currency or "").upper().strip()

    if curr == "UNKNOWN" or not curr:
        return None, None, None, "Currency is unresolved or unknown"

    if curr == base_currency:
        return round(float(amount), 2), 1.0, None, None

    rate, set_date = get_fx_rate(curr, db=db)
    if rate is None:
        return None, None, None, f"No exchange rate configured for currency '{curr}'"

    amount_base = round(float(amount) * rate, 2)
    return amount_base, rate, set_date, None

def refresh_live_fx_rates(base="INR", db=None):
    """
    Fetches live exchange rates from https://open.er-api.com/v6/latest/{BASE}.
    Stores units of currency per 1 base, inverted as rate_to_base.
    Returns (count_updated, error_message).
    """
    import urllib.request
    import json
    import datetime

    base_upper = (base or "INR").upper()
    url = f"https://open.er-api.com/v6/latest/{base_upper}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ReceiptGraveyard/1.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("result") != "success":
                return 0, f"API returned non-success: {data.get('error-type')}"

            rates = data.get("rates", {})
            today_str = datetime.date.today().isoformat()

            close_db = False
            if db is None:
                from database.db import get_db
                db = get_db()
                close_db = True

            count = 0
            for curr, rate_val in rates.items():
                try:
                    r_val = float(rate_val)
                    if r_val <= 0:
                        continue
                    rate_to_base = 1.0 if curr == base_upper else round(1.0 / r_val, 6)
                    db.execute(
                        """INSERT INTO fx_rates (currency, base, rate_to_base, fetched_at, source)
                           VALUES (?, ?, ?, ?, 'api')
                           ON CONFLICT(currency, base) DO UPDATE SET
                               rate_to_base = excluded.rate_to_base,
                               fetched_at = excluded.fetched_at,
                               source = excluded.source""",
                        (curr.upper(), base_upper, rate_to_base, today_str)
                    )
                    count += 1
                except Exception:
                    continue

            db.commit()
            if close_db:
                db.close()
            return count, None
    except Exception as e:
        return 0, str(e)
