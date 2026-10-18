"""
services/verify.py — Self-verification (voting) service (SPEC s4 & s9 P3)

Implements voting across (user, model, reader 2):
- Amount voting: max(1 currency unit, 1 percent).
- Invoice ID voting: normalized edit distance <= 1.
- Category voting: user vs majority of (model_category, keyword match).
- Card number masking for stored OCR text.
"""

import re
from typing import Optional, Dict, Any, List, Tuple
from database.db import get_categories_config

def mask_card_numbers(text: str) -> str:
    """Masks card numbers in text, keeping only the last 4 digits."""
    if not text:
        return ""
    # Matches patterns of 13 to 19 digits, possibly separated by spaces or dashes
    pattern = r"\b(?:\d[ -]*?){13,19}\b"
    
    def _repl(match):
        raw = match.group(0)
        digits = [c for c in raw if c.isdigit()]
        if len(digits) < 13 or len(digits) > 19:
            return raw
        last4 = "".join(digits[-4:])
        return f"XXXX-XXXX-XXXX-{last4}"
    
    return re.sub(pattern, _repl, text)

def amounts_agree(a1: Optional[float], a2: Optional[float]) -> bool:
    """Amounts in receipt's currency within max(1 currency unit, 1 percent)."""
    if a1 is None or a2 is None:
        return False
    try:
        f1, f2 = float(a1), float(a2)
    except (ValueError, TypeError):
        return False
    tol = max(1.0, 0.01 * max(abs(f1), abs(f2)))
    return abs(f1 - f2) <= tol

def normalize_invoice_id(val: Optional[str]) -> str:
    """Normalize: uppercase, strip punctuation, treat O/0 and I/1 as equal."""
    if not val:
        return ""
    s = str(val).upper()
    # strip punctuation/spaces
    s = re.sub(r"[^A-Z0-9]", "", s)
    # treat O/0 and I/1 as equal
    s = s.replace("O", "0").replace("I", "1")
    return s

def levenshtein_dist(s1: str, s2: str) -> int:
    """Standard Levenshtein distance."""
    if s1 == s2:
        return 0
    if not s1:
        return len(s2)
    if not s2:
        return len(s1)
    dp = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        new_dp = [i + 1] * (len(s2) + 1)
        for j, c2 in enumerate(s2):
            cost = 0 if c1 == c2 else 1
            new_dp[j + 1] = min(dp[j + 1] + 1, new_dp[j] + 1, dp[j] + cost)
        dp = new_dp
    return dp[len(s2)]

def invoice_ids_agree(id1: Optional[str], id2: Optional[str]) -> bool:
    """Invoice numbers agree by equality or edit distance of 1 or less."""
    n1 = normalize_invoice_id(id1)
    n2 = normalize_invoice_id(id2)
    if not n1 or not n2:
        return False
    return levenshtein_dist(n1, n2) <= 1

def determine_keyword_category(
    text: str,
    items: List[Dict[str, Any]],
    merchant: Optional[str] = None,
    categories_cfg: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """Matches text and line items against categories.json keyword map."""
    cfg = categories_cfg or get_categories_config()
    keyword_map = cfg.get("keyword_map", {})
    
    # Aggregate text pool
    pool = f"{merchant or ''} {text or ''} "
    if items:
        for it in items:
            pool += f" {it.get('name', '')}"
    pool_lower = pool.lower()
    
    cat_scores = {}
    for cat, keywords in keyword_map.items():
        if not keywords:
            continue
        count = 0
        for kw in keywords:
            if re.search(r"\b" + re.escape(kw.lower()) + r"\b", pool_lower):
                count += 1
        if count > 0:
            cat_scores[cat] = count
            
    if not cat_scores:
        return None
    # Return category with highest match count
    return max(cat_scores, key=cat_scores.get)

def verify_receipt(
    user_inputs: Dict[str, Any],
    extracted: Dict[str, Any],
    ocr_parsed: Optional[Dict[str, Any]] = None,
    ocr_text: str = "",
    categories_cfg: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Executes voting over (user, model, reader 2).
    Returns dict with:
        accepted_amount: float | None
        amount_source: 'model' | 'ocr' | 'user'
        accepted_invoice_no: str | None
        flags: list of flag dicts
        masked_ocr_text: str
    """
    flags: List[Dict[str, Any]] = []
    ocr_parsed = ocr_parsed or {}
    
    # User inputs
    user_amount_raw = user_inputs.get("claimed_amount")
    user_amount: Optional[float] = None
    if user_amount_raw not in (None, ""):
        try:
            user_amount = float(user_amount_raw)
        except (ValueError, TypeError):
            user_amount = None

    user_inv_raw = user_inputs.get("claimed_invoice_no")
    user_inv: Optional[str] = str(user_inv_raw).strip() if user_inv_raw not in (None, "") else None
    user_category = user_inputs.get("category")

    # Model readings
    model_amount: Optional[float] = extracted.get("total")
    if model_amount is not None:
        try:
            model_amount = float(model_amount)
        except (ValueError, TypeError):
            model_amount = None
    model_inv: Optional[str] = str(extracted.get("unique_id")).strip() if extracted.get("unique_id") else None
    model_category = extracted.get("model_category")

    # Reader 2 readings
    ocr_amount: Optional[float] = ocr_parsed.get("amount")
    if ocr_amount is not None:
        try:
            ocr_amount = float(ocr_amount)
        except (ValueError, TypeError):
            ocr_amount = None
    ocr_inv: Optional[str] = str(ocr_parsed.get("invoice_no")).strip() if ocr_parsed.get("invoice_no") else None

    # Mask card numbers in OCR text
    masked_text = mask_card_numbers(ocr_text)

    # ─────────────────────────────────────────────────────────────────────────
    # 1. AMOUNT VOTING
    # ─────────────────────────────────────────────────────────────────────────
    accepted_amount: Optional[float] = None
    amount_source = "model"

    if model_amount is None and ocr_amount is None:
        # Neither reader found an amount
        if user_amount is not None:
            accepted_amount = user_amount
            amount_source = "user"
            flags.append({
                "code": "EXTRACTION_UNCERTAIN",
                "clause": "docs/POLICY.md Section 10",
                "amount_affected": user_amount,
                "message": f"No receipt reader found an amount; provisional typed value ({user_amount:,.2f}) used for review."
            })
        else:
            accepted_amount = None
            amount_source = "model"
    elif ocr_amount is None:
        # Reader 2 is None: ignore reader 2 and never flag for that
        accepted_amount = model_amount
        amount_source = "model"
    else:
        # Both model_amount and ocr_amount exist (or reader 2 found an amount)
        if model_amount is not None:
            readers_agree = amounts_agree(model_amount, ocr_amount)
            if readers_agree:
                # Two readers agree -> receipt value is accepted
                accepted_amount = model_amount
                amount_source = "model"
                if user_amount is not None and not amounts_agree(user_amount, model_amount):
                    # User is the odd one out
                    flags.append({
                        "code": "AMOUNT_MISMATCH",
                        "clause": "docs/POLICY.md Section 10",
                        "amount_affected": round(abs(user_amount - model_amount), 2),
                        "message": f"Typed {user_amount:,.2f}; receipt shows {model_amount:,.2f}."
                    })
            else:
                # Model and OCR disagree
                if user_amount is not None and amounts_agree(user_amount, model_amount):
                    accepted_amount = model_amount
                    amount_source = "model"
                elif user_amount is not None and amounts_agree(user_amount, ocr_amount):
                    accepted_amount = ocr_amount
                    amount_source = "ocr"
                else:
                    # No two agree
                    accepted_amount = model_amount
                    amount_source = "model"
                    user_str = f"{user_amount:,.2f}" if user_amount is not None else "None"
                    flags.append({
                        "code": "EXTRACTION_UNCERTAIN",
                        "clause": "docs/POLICY.md Section 10",
                        "amount_affected": model_amount or 0.0,
                        "message": f"No majority on amount: user typed {user_str}, vision model read {model_amount:,.2f}, OCR read {ocr_amount:,.2f}."
                    })
        else:
            # Model is None, but OCR has amount
            if user_amount is not None and amounts_agree(user_amount, ocr_amount):
                accepted_amount = ocr_amount
                amount_source = "ocr"
            elif user_amount is not None:
                accepted_amount = ocr_amount
                amount_source = "ocr"
                flags.append({
                    "code": "AMOUNT_MISMATCH",
                    "clause": "docs/POLICY.md Section 10",
                    "amount_affected": round(abs(user_amount - ocr_amount), 2),
                    "message": f"Typed {user_amount:,.2f}; receipt shows {ocr_amount:,.2f}."
                })
            else:
                accepted_amount = ocr_amount
                amount_source = "ocr"

    # ─────────────────────────────────────────────────────────────────────────
    # 2. INVOICE ID VOTING
    # ─────────────────────────────────────────────────────────────────────────
    accepted_inv: Optional[str] = model_inv

    if not model_inv and not ocr_inv:
        if user_inv:
            accepted_inv = user_inv
            flags.append({
                "code": "EXTRACTION_UNCERTAIN",
                "clause": "docs/POLICY.md Section 10",
                "amount_affected": 0.0,
                "message": f"No receipt reader found an invoice ID; provisional typed value '{user_inv}' used."
            })
    elif not ocr_inv:
        # Reader 2 returns None: ignore it and never flag for that
        accepted_inv = model_inv
    elif not user_inv:
        # User left invoice number blank: compare model against reader 2 only
        if model_inv and ocr_inv:
            if invoice_ids_agree(model_inv, ocr_inv):
                accepted_inv = model_inv
            else:
                accepted_inv = model_inv
                flags.append({
                    "code": "EXTRACTION_UNCERTAIN",
                    "clause": "docs/POLICY.md Section 10",
                    "amount_affected": 0.0,
                    "message": f"Readers disagree on invoice number: model read '{model_inv}', OCR read '{ocr_inv}'."
                })
        else:
            accepted_inv = model_inv or ocr_inv
    else:
        # All three available
        if model_inv:
            readers_agree = invoice_ids_agree(model_inv, ocr_inv)
            if readers_agree:
                accepted_inv = model_inv
                if not invoice_ids_agree(user_inv, model_inv):
                    flags.append({
                        "code": "INVOICE_ID_MISMATCH",
                        "clause": "docs/POLICY.md Section 10",
                        "amount_affected": 0.0,
                        "message": f"Typed '{user_inv}'; receipt shows '{model_inv}'."
                    })
            else:
                if invoice_ids_agree(user_inv, model_inv):
                    accepted_inv = model_inv
                elif invoice_ids_agree(user_inv, ocr_inv):
                    accepted_inv = ocr_inv
                else:
                    accepted_inv = model_inv
                    flags.append({
                        "code": "EXTRACTION_UNCERTAIN",
                        "clause": "docs/POLICY.md Section 10",
                        "amount_affected": 0.0,
                        "message": f"No majority on invoice ID: typed '{user_inv}', model read '{model_inv}', OCR read '{ocr_inv}'."
                    })
        else:
            accepted_inv = ocr_inv
            if not invoice_ids_agree(user_inv, ocr_inv):
                flags.append({
                    "code": "INVOICE_ID_MISMATCH",
                    "clause": "docs/POLICY.md Section 10",
                    "amount_affected": 0.0,
                    "message": f"Typed '{user_inv}'; receipt shows '{ocr_inv}'."
                })

    # ─────────────────────────────────────────────────────────────────────────
    # 3. CATEGORY VOTING
    # ─────────────────────────────────────────────────────────────────────────
    keyword_cat = determine_keyword_category(
        ocr_text,
        extracted.get("items", []),
        extracted.get("merchant"),
        categories_cfg
    )

    if user_category:
        u_norm = user_category.strip().title()
        m_norm = model_category.strip().title() if model_category else None
        k_norm = keyword_cat.strip().title() if keyword_cat else None

        # A single-reader disagreement does not flag (if user agrees with either model or keyword, no flag)
        # Flag CATEGORY_MISMATCH if user's category differs from model, and user has no keyword agreement
        if m_norm and u_norm != m_norm:
            if k_norm is None or u_norm != k_norm:
                flags.append({
                    "code": "CATEGORY_MISMATCH",
                    "clause": "docs/POLICY.md Section 3 & 10",
                    "amount_affected": accepted_amount or 0.0,
                    "message": f"User categorized expense as '{u_norm}', but reader classified items as '{m_norm}'."
                })
        elif not m_norm and k_norm and u_norm != k_norm:
            # Model didn't return category, but keyword strongly indicates another
            flags.append({
                "code": "CATEGORY_MISMATCH",
                "clause": "docs/POLICY.md Section 3 & 10",
                "amount_affected": accepted_amount or 0.0,
                "message": f"User categorized expense as '{u_norm}', but receipt text indicates '{k_norm}'."
            })

    return {
        "accepted_amount": accepted_amount,
        "amount_source": amount_source,
        "accepted_invoice_no": accepted_inv,
        "flags": flags,
        "masked_ocr_text": masked_text,
        "keyword_category": keyword_cat
    }
