import datetime
import math
from typing import Dict, Any, List, Tuple, Optional
from database.db import get_policy_config, get_setting
from services.cities import resolve_city_tier
from services.currency import resolve_currency, convert_to_base

def parse_date(date_str: Optional[str]) -> Optional[datetime.date]:
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    return None

def evaluate_receipt_rules(
    receipt_data: Dict[str, Any],
    user_inputs: Dict[str, Any],
    db=None,
    upload_date: Optional[datetime.date] = None,
    is_seeded: bool = False,
    existing_receipt_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Evaluates policy rules on extracted receipt data and employee inputs.
    Returns:
    {
        "status": "rejected" | "info_needed" | "flagged" | "verified",
        "flags": List[dict],
        "missing_fields": List[str],
        "rejection_reason": Optional[dict],
        "city": str,
        "country": str,
        "tier": str,
        "currency": str,
        "amount_original": float,
        "fx_rate": float,
        "amount_base": float,
        "nights": int,
        "dup_group_id": Optional[str],
        "is_original_dup": bool
    }
    """
    policy = get_policy_config()
    conf_threshold = float(get_setting("CONF_THRESHOLD", policy.get("confidence_threshold", 0.7), db=db))
    banned_threshold = float(get_setting("BANNED_THRESHOLD", policy.get("banned_confidence_threshold", 0.7), db=db))
    upload_window_days = int(policy.get("upload_window_days", 15))
    limits_cfg = policy.get("limits", {})
    banned_list = [b.lower() for b in policy.get("banned", [])]
    
    if upload_date is None:
        upload_date = datetime.date.today()

    conf = receipt_data.get("conf") or {}
    items = receipt_data.get("items") or []
    
    # 0. Currency and City / Tier resolution
    raw_currency = receipt_data.get("currency")
    country = receipt_data.get("country")
    merchant = receipt_data.get("merchant")
    currency = resolve_currency(raw_currency, country=country, merchant=merchant)
    
    amount_original = receipt_data.get("total")
    try:
        amount_original = float(amount_original) if amount_original is not None else 0.0
    except (ValueError, TypeError):
        amount_original = 0.0

    amount_base, fx_rate, set_date, curr_err = convert_to_base(amount_original, currency, db=db)
    
    raw_city = receipt_data.get("city")
    full_text = f"{merchant or ''} {raw_city or ''}"
    canonical_city, tier, is_city_unclear = resolve_city_tier(raw_city, full_text=full_text)

    # -------------------------------------------------------------
    # 1. AUTO-REJECT (Stop at first auto-reject)
    # -------------------------------------------------------------
    # A) NON_REIMBURSABLE banned items
    for itm in items:
        banned_match = (itm.get("banned_match") or itm.get("banned_category") or "").strip().lower()
        banned_conf = float(itm.get("banned_conf") or itm.get("banned_confidence") or 0.0)
        itm_name = itm.get("name") or "Unknown item"
        
        # Check explicit banned match
        if banned_match and banned_match in banned_list and banned_conf >= banned_threshold:
            return {
                "status": "rejected",
                "rejection_reason": {
                    "code": "NON_REIMBURSABLE",
                    "clause": "docs/POLICY.md Section 6",
                    "amount_affected": itm.get("amount", amount_original),
                    "message": f"Non-reimbursable item detected: '{itm_name}' classified as {banned_match} (confidence: {banned_conf:.2f}). Policy rejects mixed receipts in their entirety."
                },
                "flags": [],
                "missing_fields": [],
                "city": canonical_city,
                "country": country,
                "tier": tier,
                "currency": currency,
                "amount_original": amount_original,
                "fx_rate": fx_rate or 1.0,
                "amount_base": amount_base or 0.0,
                "nights": receipt_data.get("nights"),
                "dup_group_id": None,
                "is_original_dup": False
            }

    # B) LATE_UPLOAD (live uploads > 15 days, seeded receipts exempt)
    issue_date_str = receipt_data.get("issue_date")
    issue_date_obj = parse_date(issue_date_str)
    
    if not is_seeded and issue_date_obj:
        days_diff = (upload_date - issue_date_obj).days
        if days_diff > upload_window_days:
            return {
                "status": "rejected",
                "rejection_reason": {
                    "code": "LATE_UPLOAD",
                    "clause": "docs/POLICY.md Section 4",
                    "amount_affected": amount_original,
                    "message": f"Receipt submitted {days_diff} days after issue date ({issue_date_str}). Policy limit is {upload_window_days} days."
                },
                "flags": [],
                "missing_fields": [],
                "city": canonical_city,
                "country": country,
                "tier": tier,
                "currency": currency,
                "amount_original": amount_original,
                "fx_rate": fx_rate or 1.0,
                "amount_base": amount_base or 0.0,
                "nights": receipt_data.get("nights"),
                "dup_group_id": None,
                "is_original_dup": False
            }

    # -------------------------------------------------------------
    # 2. INFO NEEDED
    # -------------------------------------------------------------
    missing_fields = []
    
    # Missing / low-confidence issue_date
    if not issue_date_obj or float(conf.get("issue_date", 0.0)) < conf_threshold:
        missing_fields.append("issue_date")

    # Missing / low-confidence total
    if amount_original <= 0.0 or float(conf.get("total", 0.0)) < conf_threshold:
        missing_fields.append("total")

    # Missing / low-confidence unique_id
    unique_id = receipt_data.get("unique_id")
    if not unique_id or float(conf.get("unique_id", 0.0)) < conf_threshold:
        missing_fields.append("unique_id")

    # Food attendee count required and must be integer >= 1
    user_category = user_inputs.get("category", "")
    attendees = user_inputs.get("attendees")
    if user_category == "Food":
        try:
            attendees_int = int(attendees)
            if attendees_int < 1:
                missing_fields.append("attendees")
            else:
                attendees = attendees_int
        except (ValueError, TypeError):
            missing_fields.append("attendees")
    else:
        attendees = int(attendees) if attendees else 1

    # Client-related without client name
    is_client_related = bool(user_inputs.get("is_client_related"))
    client_name = user_inputs.get("client_name")
    if is_client_related and (not client_name or not str(client_name).strip()):
        missing_fields.append("client_name")

    # Unresolved currency
    if currency == "UNKNOWN" or curr_err:
        missing_fields.append("currency")

    if missing_fields:
        return {
            "status": "info_needed",
            "rejection_reason": None,
            "flags": [],
            "missing_fields": missing_fields,
            "city": canonical_city,
            "country": country,
            "tier": tier,
            "currency": currency,
            "amount_original": amount_original,
            "fx_rate": fx_rate or 1.0,
            "amount_base": amount_base or 0.0,
            "nights": receipt_data.get("nights"),
            "dup_group_id": None,
            "is_original_dup": False
        }

    # -------------------------------------------------------------
    # 3. FLAGS (Collect all that apply)
    # -------------------------------------------------------------
    flags = []

    # A) ITEMS_UNCLEAR: items_readable false, or no line items
    items_readable = receipt_data.get("items_readable", True)
    if not items_readable or len(items) == 0:
        flags.append({
            "code": "ITEMS_UNCLEAR",
            "clause": "docs/POLICY.md Section 5 & 10",
            "amount_affected": amount_base,
            "message": "Itemized details are unreadable or only a summary total was provided."
        })

    # B) MATH_MISMATCH: items sum vs stated total
    # items, minus discount, plus shipping and tax
    if items and len(items) > 0:
        item_sum = sum(float(i.get("amount", 0.0)) for i in items)
        subtotal = float(receipt_data.get("subtotal") or 0.0)
        tax = float(receipt_data.get("tax") or 0.0)
        discount = float(receipt_data.get("discount") or 0.0)
        shipping = float(receipt_data.get("shipping") or 0.0)
        
        # Stated items sum comparison:
        # If tax/discount/shipping are explicit, computed_total = item_sum - discount + shipping + tax
        # If no explicit tax/discount but items sum differs from total:
        computed_total = item_sum - discount + shipping + (tax if tax > 0 and item_sum + tax <= amount_original + 1 else 0)
        if tax > 0 and abs(item_sum + tax - amount_original) < abs(item_sum - amount_original):
            diff = abs(item_sum + tax - amount_original)
        else:
            diff = abs(item_sum - amount_original)

        rounding_tol = max(
            policy.get("rounding_tolerance", {}).get("min_inr", 1.0),
            policy.get("rounding_tolerance", {}).get("percent", 0.01) * amount_original
        )
        if diff > rounding_tol:
            flags.append({
                "code": "MATH_MISMATCH",
                "clause": "docs/POLICY.md Section 10",
                "amount_affected": round(diff * (fx_rate or 1.0), 2),
                "message": f"Line items sum ({item_sum:.2f}) differs from stated total ({amount_original:.2f}) by {diff:.2f} (exceeds rounding tolerance {rounding_tol:.2f})."
            })

    # C) CATEGORY_MISMATCH: user vs model category
    model_category = receipt_data.get("model_category")
    if model_category and user_category and model_category.lower() != user_category.lower():
        flags.append({
            "code": "CATEGORY_MISMATCH",
            "clause": "docs/POLICY.md Section 3 & 10",
            "amount_affected": amount_base,
            "message": f"User categorized expense as '{user_category}', but reader classified items as '{model_category}'."
        })

    # D) ACCOMMODATION_OFFICE: Accommodation with Office context
    context = user_inputs.get("context", "Trip")
    if user_category == "Accommodation" and context.lower() == "office":
        flags.append({
            "code": "ACCOMMODATION_OFFICE",
            "clause": "docs/POLICY.md Section 9 & 10",
            "amount_affected": amount_base,
            "message": "Accommodation claim submitted with Office context (eligible only for official Trip)."
        })

    # E) CITY_UNCLEAR: city not found in cities.json
    if is_city_unclear:
        flags.append({
            "code": "CITY_UNCLEAR",
            "clause": "docs/POLICY.md Section 8 & 10",
            "amount_affected": amount_base,
            "message": f"City '{raw_city or 'Unspecified'}' not recognized in city registry; Tier C spending limits applied."
        })

    # F) SUSPICIOUS SIGNALS
    # 1. Future date
    if issue_date_obj and issue_date_obj > upload_date:
        flags.append({
            "code": "SUSPICIOUS_FUTURE_DATE",
            "clause": "docs/POLICY.md Section 12",
            "amount_affected": amount_base,
            "message": f"Issue date ({issue_date_str}) is in the future relative to submission date ({upload_date})."
        })

    # 2. Perfectly round total (multiple of 100 in base currency) without an item explaining it
    if amount_base and amount_base >= 100.0 and (amount_base % 100.0 == 0.0):
        # Check if an individual item explains the exact round amount
        has_matching_item = any(
            abs(float(i.get("amount", 0.0)) * (fx_rate or 1.0) - amount_base) < 0.01
            for i in items
        )
        if not has_matching_item or len(items) <= 1:
            flags.append({
                "code": "SUSPICIOUS_ROUND_TOTAL",
                "clause": "docs/POLICY.md Section 12",
                "amount_affected": amount_base,
                "message": f"Total amount (₹{amount_base:,.2f}) is a perfectly round multiple of 100 with no item breakdown explaining it."
            })

    # 3. Alteration notes
    alteration_notes = receipt_data.get("alteration_notes")
    if alteration_notes and str(alteration_notes).strip():
        flags.append({
            "code": "SUSPICIOUS_ALTERATION",
            "clause": "docs/POLICY.md Section 12",
            "amount_affected": amount_base,
            "message": f"Reader detected potential document alteration: {alteration_notes}"
        })

    # G) DUPLICATE CHECK
    dup_group_id = None
    is_original_dup = False
    if db is not None:
        file_hash = receipt_data.get("file_hash")
        dup_query = """
            SELECT id, user_id, file_hash, unique_id, merchant, issue_date, amount_original, dup_group_id, uploaded_at
            FROM receipts
            WHERE status NOT IN ('rejected', 'denied')
        """
        params = []
        if existing_receipt_id:
            dup_query += " AND id != ?"
            params.append(existing_receipt_id)

        rows = db.execute(dup_query, params).fetchall()
        matching_dups = []
        for r in rows:
            is_dup = False
            # Criteria 1: same file hash
            if file_hash and r["file_hash"] == file_hash:
                is_dup = True
            # Criteria 2: same unique_id
            elif unique_id and r["unique_id"] and str(unique_id).strip().lower() == str(r["unique_id"]).strip().lower():
                is_dup = True
            # Criteria 3: same merchant + issue_date + amount
            elif issue_date_str and r["issue_date"] == issue_date_str and abs(float(r["amount_original"] or 0) - amount_original) < 0.05:
                m1 = (merchant or "").strip().lower()
                m2 = (r["merchant"] or "").strip().lower()
                if m1 and m2 and (m1 in m2 or m2 in m1):
                    is_dup = True
            
            if is_dup:
                matching_dups.append(r)

        if matching_dups:
            # Group into cluster; earliest upload is original
            earliest_id = min(r["id"] for r in matching_dups)
            dup_group_id = matching_dups[0]["dup_group_id"] or f"dup_cluster_{earliest_id}"
            flags.append({
                "code": "DUPLICATE",
                "clause": "docs/POLICY.md Section 11",
                "amount_affected": amount_base,
                "message": f"Potential duplicate submission detected (matches Receipt #{earliest_id} on unique ID / merchant / file hash). Cluster: {dup_group_id}."
            })

    # H) SPENDING LIMITS (Section 9) - ALWAYS FLAG, NEVER REJECT
    # 1. Per-receipt Limit
    nights = receipt_data.get("nights") or 1
    if user_category == "Food":
        per_person_amt = amount_base / float(attendees)
        if context.lower() == "trip":
            meal_limit = float(limits_cfg.get("food_trip", {}).get("per_person_meal", {}).get(tier, 500))
        else:
            meal_limit = float(limits_cfg.get("food_office", {}).get("per_person_meal", 400))
        
        if per_person_amt > meal_limit:
            flags.append({
                "code": "LIMIT_RECEIPT",
                "clause": "docs/POLICY.md Section 9",
                "amount_affected": round((per_person_amt - meal_limit) * float(attendees), 2),
                "message": f"Food per-person meal: ₹{per_person_amt:,.2f} exceeds limit of ₹{meal_limit:,.2f} (Context: {context}, Tier {tier}, Attendees: {attendees})."
            })
    elif user_category == "Accommodation":
        night_limit = float(limits_cfg.get("accommodation", {}).get("per_night", {}).get(tier, 3000))
        amt_per_night = amount_base / float(nights)
        if amt_per_night > night_limit:
            flags.append({
                "code": "LIMIT_RECEIPT",
                "clause": "docs/POLICY.md Section 9",
                "amount_affected": round((amt_per_night - night_limit) * float(nights), 2),
                "message": f"Accommodation per-night amount: ₹{amt_per_night:,.2f} exceeds limit of ₹{night_limit:,.2f} (Tier {tier}, Nights: {nights})."
            })
    elif user_category == "Travel":
        travel_limit = float(limits_cfg.get("travel", {}).get("per_receipt", 10000))
        if amount_base > travel_limit:
            flags.append({
                "code": "LIMIT_RECEIPT",
                "clause": "docs/POLICY.md Section 9",
                "amount_affected": round(amount_base - travel_limit, 2),
                "message": f"Travel receipt amount: ₹{amount_base:,.2f} exceeds per-receipt limit of ₹{travel_limit:,.2f}."
            })
    elif user_category == "Equipment":
        eq_limit = float(limits_cfg.get("equipment", {}).get("per_receipt", 10000))
        if amount_base > eq_limit:
            flags.append({
                "code": "LIMIT_RECEIPT",
                "clause": "docs/POLICY.md Section 9",
                "amount_affected": round(amount_base - eq_limit, 2),
                "message": f"Equipment receipt amount: ₹{amount_base:,.2f} exceeds per-receipt limit of ₹{eq_limit:,.2f}."
            })
    elif user_category == "Miscellaneous":
        misc_limit = float(limits_cfg.get("misc", {}).get("per_receipt", 2000))
        if amount_base > misc_limit:
            flags.append({
                "code": "LIMIT_RECEIPT",
                "clause": "docs/POLICY.md Section 9",
                "amount_affected": round(amount_base - misc_limit, 2),
                "message": f"Miscellaneous receipt amount: ₹{amount_base:,.2f} exceeds per-receipt limit of ₹{misc_limit:,.2f}."
            })

    # 2. Per-day & Cumulative-monthly limits evaluated against database ledger
    user_id = user_inputs.get("user_id")
    if db is not None and user_id is not None and issue_date_str:
        # Fetch earlier approved/verified/flagged receipts for this employee, category, and date/month
        month_prefix = issue_date_str[:7] # YYYY-MM
        
        ledger_query = """
            SELECT id, issue_date, amount_base, attendees, context, tier
            FROM receipts
            WHERE user_id = ?
              AND category_user = ?
              AND status IN ('approved', 'verified', 'flagged')
              AND issue_date LIKE ?
        """
        params = [user_id, user_category, f"{month_prefix}%"]
        if existing_receipt_id:
            ledger_query += " AND id != ?"
            params.append(existing_receipt_id)

        rows = db.execute(ledger_query, params).fetchall()

        # Day limit
        if user_category in ("Food", "Miscellaneous"):
            same_day_rows = [r for r in rows if r["issue_date"] == issue_date_str]
            if user_category == "Food":
                prior_day_pp = sum(
                    float(r["amount_base"] or 0) / max(int(r["attendees"] or 1), 1)
                    for r in same_day_rows
                )
                curr_pp = amount_base / float(attendees)
                day_limit = float(
                    limits_cfg.get("food_trip", {}).get("per_person_day", {}).get(tier, 1200)
                    if context.lower() == "trip"
                    else limits_cfg.get("food_office", {}).get("per_person_day", 800)
                )
                if prior_day_pp + curr_pp > day_limit:
                    flags.append({
                        "code": "LIMIT_DAY",
                        "clause": "docs/POLICY.md Section 9",
                        "amount_affected": round((prior_day_pp + curr_pp - day_limit) * float(attendees), 2),
                        "message": f"Food daily per-person limit for {issue_date_str}: ₹{prior_day_pp:,.2f} already claimed + ₹{curr_pp:,.2f} = ₹{prior_day_pp + curr_pp:,.2f}, exceeds daily limit of ₹{day_limit:,.2f}."
                    })
            elif user_category == "Miscellaneous":
                prior_day_amt = sum(float(r["amount_base"] or 0) for r in same_day_rows)
                misc_day_limit = float(limits_cfg.get("misc", {}).get("per_day", 3000))
                if prior_day_amt + amount_base > misc_day_limit:
                    flags.append({
                        "code": "LIMIT_DAY",
                        "clause": "docs/POLICY.md Section 9",
                        "amount_affected": round(prior_day_amt + amount_base - misc_day_limit, 2),
                        "message": f"Miscellaneous daily limit for {issue_date_str}: ₹{prior_day_amt:,.2f} already claimed + ₹{amount_base:,.2f} = ₹{prior_day_amt + amount_base:,.2f}, exceeds limit of ₹{misc_day_limit:,.2f}."
                    })

        # Monthly limit
        monthly_limits = limits_cfg.get("monthly", {})
        monthly_cat_limit = float(monthly_limits.get(user_category, 0))
        if monthly_cat_limit > 0:
            prior_month_total = sum(float(r["amount_base"] or 0) for r in rows)
            if prior_month_total + amount_base > monthly_cat_limit:
                flags.append({
                    "code": "LIMIT_MONTH",
                    "clause": "docs/POLICY.md Section 9",
                    "amount_affected": round(prior_month_total + amount_base - monthly_cat_limit, 2),
                    "message": f"{user_category} this month ({month_prefix}): ₹{prior_month_total:,.2f} already claimed + ₹{amount_base:,.2f} = ₹{prior_month_total + amount_base:,.2f}, limit ₹{monthly_cat_limit:,.2f}."
                })

    final_status = "flagged" if len(flags) > 0 else "verified"

    return {
        "status": final_status,
        "rejection_reason": None,
        "flags": flags,
        "missing_fields": [],
        "city": canonical_city,
        "country": country,
        "tier": tier,
        "currency": currency,
        "amount_original": amount_original,
        "fx_rate": fx_rate or 1.0,
        "amount_base": amount_base or 0.0,
        "nights": receipt_data.get("nights"),
        "dup_group_id": dup_group_id,
        "is_original_dup": is_original_dup
    }
