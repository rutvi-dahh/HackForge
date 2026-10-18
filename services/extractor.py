"""
services/extractor.py — SPEC s7: Extraction pipeline
Order: ingest → OCR reader 2 → Pass A (Groq) → Pass B (if needed) → OCR field fallback → vote → cache
"""
import os
import re
import json
import base64
import hashlib
from io import BytesIO
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path
from pydantic import BaseModel, Field
from PIL import Image, ExifTags
import fitz  # PyMuPDF

from database.db import get_db, get_setting

# ── Card masking ─────────────────────────────────────────────────────────────
CARD_REGEX = re.compile(r"\b(?:\d[ -]*?){13,19}\b")

def mask_credit_card(text: str) -> str:
    if not text:
        return text
    def _repl(match):
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if not (13 <= len(digits) <= 19):
            return raw
        masked = "*" * (len(digits) - 4) + digits[-4:]
        return "-".join(masked[i:i+4] for i in range(0, len(masked), 4))
    return CARD_REGEX.sub(_repl, text)

def mask_extracted_data(data):
    if isinstance(data, str):
        return mask_credit_card(data)
    if isinstance(data, list):
        return [mask_extracted_data(i) for i in data]
    if isinstance(data, dict):
        return {k: mask_extracted_data(v) for k, v in data.items()}
    return data

# ── Pydantic contract ────────────────────────────────────────────────────────
class ItemModel(BaseModel):
    name: str = ""
    qty: Optional[float] = 1.0
    amount: Optional[float] = 0.0
    banned_match: Optional[str] = None
    banned_conf: Optional[float] = 0.0

class ConfModel(BaseModel):
    issue_date: float = 0.0
    total: float = 0.0
    unique_id: float = 0.0
    city: float = 0.0
    items: float = 0.0

class ExtractionModel(BaseModel):
    merchant: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    issue_date: Optional[str] = None
    unique_id: Optional[str] = None
    currency: Optional[str] = "unknown"
    total: Optional[float] = 0.0
    subtotal: Optional[float] = 0.0
    tax: Optional[float] = 0.0
    discount: Optional[float] = 0.0
    shipping: Optional[float] = 0.0
    nights: Optional[int] = None
    items: List[ItemModel] = Field(default_factory=list)
    items_readable: bool = True
    model_category: Optional[str] = "Miscellaneous"
    alteration_notes: Optional[str] = None
    conf: ConfModel = Field(default_factory=ConfModel)
    image_quality: Optional[str] = "high"

EXTRACTION_SYSTEM_PROMPT = """You are an accurate, strict receipt and invoice information extractor.
Extract receipt fields into the exact JSON contract below.
RULES:
1. Return ONLY valid JSON. No markdown, no preamble.
2. Use null for anything not visible. Never infer or guess.
3. Dates: day-first by default (DD-MM-YYYY). If format is ambiguous and you are not confident, return null.
4. Currency: return the ISO 4217 code (e.g. INR, MYR, USD) or 'unknown'. If '$' appears on a Malaysian receipt, set unknown and fill country.
5. Categories: exactly one of ['Food','Travel','Accommodation','Equipment','Education','Miscellaneous'].
6. Banned items: alcohol, tobacco, fine/penalty, personal items, gifts, gambling. Set banned_match to the category string or null; banned_conf 0.0-1.0.
7. alteration_notes: non-null only if visible signs of alteration exist.
8. items_readable: false if only a grand total is shown or item lines cannot be read.
9. conf: per-field confidence 0.0-1.0 for issue_date, total, unique_id, city, items.
OCR HINT (labelled 'may contain errors'): trust the image over the hint for specific values.

JSON CONTRACT:
{
  "merchant": "string|null",
  "city": "string|null",
  "country": "string|null",
  "issue_date": "YYYY-MM-DD|null",
  "unique_id": "string|null",
  "currency": "any ISO 4217|unknown",
  "total": 0.0,
  "subtotal": 0.0, "tax": 0.0, "discount": 0.0, "shipping": 0.0,
  "nights": null,
  "items": [{"name":"","qty":1,"amount":0.0,"banned_match":null,"banned_conf":0.0}],
  "items_readable": true,
  "model_category": "Food|Travel|Accommodation|Equipment|Education|Miscellaneous",
  "alteration_notes": "string|null",
  "conf": {"issue_date":0.0,"total":0.0,"unique_id":0.0,"city":0.0,"items":0.0},
  "image_quality": "high|medium|low"
}"""

# ── Helpers ──────────────────────────────────────────────────────────────────
def compute_file_hash(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()

def fix_exif_rotation(img: Image.Image) -> Image.Image:
    """Rotate image per EXIF orientation tag."""
    try:
        exif = img._getexif()
        if exif:
            for tag, val in exif.items():
                if ExifTags.TAGS.get(tag) == "Orientation":
                    rotations = {3: 180, 6: 270, 8: 90}
                    if val in rotations:
                        img = img.rotate(rotations[val], expand=True)
    except Exception:
        pass
    return img

def resize_image_for_model(image_bytes: bytes, max_dim: int = 1568) -> Tuple[str, bytes]:
    """Returns (base64_jpeg_str, jpeg_bytes)."""
    im = Image.open(BytesIO(image_bytes))
    im = fix_exif_rotation(im)
    if im.mode in ("RGBA", "P", "LA"):
        im = im.convert("RGB")
    w, h = im.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        im = im.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
    buf = BytesIO()
    im.save(buf, format="JPEG", quality=88)
    jpeg_bytes = buf.getvalue()
    return base64.b64encode(jpeg_bytes).decode("utf-8"), jpeg_bytes

def extract_pdf_content(pdf_bytes: bytes) -> Tuple[Optional[str], Optional[str], Optional[bytes]]:
    """Returns (text_layer, image_b64, image_bytes). text_layer=None means no usable text."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    full_text = "".join(page.get_text() for page in doc).strip()
    if len(full_text) > 30:
        return full_text, None, None
    if len(doc) > 0:
        pix = doc[0].get_pixmap(dpi=150)
        img_bytes = pix.tobytes("jpeg")
        b64, _ = resize_image_for_model(img_bytes)
        return None, b64, img_bytes
    return "", None, None

# ── OCR Reader 2 ─────────────────────────────────────────────────────────────
def ocr_preprocess_image(image_bytes: bytes) -> Optional[bytes]:
    """OpenCV preprocessing: grayscale, 2× upscale, denoise, adaptive threshold, deskew."""
    try:
        import cv2
        import numpy as np
        arr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return None
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # 2× upscale
        gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        # Denoise
        gray = cv2.fastNlMeansDenoising(gray, h=10)
        # Adaptive threshold
        gray = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                     cv2.THRESH_BINARY, 31, 11)
        _, encoded = cv2.imencode(".png", gray)
        return encoded.tobytes()
    except Exception as e:
        print(f"[OCR preprocess] {e}")
        return None

def run_ocr(image_bytes: bytes) -> Optional[str]:
    """Run pytesseract on image bytes. Returns raw text or None."""
    try:
        import pytesseract
        from PIL import Image as PILImage
        img = PILImage.open(BytesIO(image_bytes))
        text = pytesseract.image_to_string(img, config="--psm 6")
        return text.strip() if text.strip() else None
    except Exception as e:
        print(f"[OCR] pytesseract failed: {e}")
        return None

def ocr_reader2(file_bytes: bytes, is_pdf: bool, pdf_text: Optional[str] = None,
                pdf_img_bytes: Optional[bytes] = None) -> Optional[str]:
    """
    Reader 2: PDFs use text layer directly. Photos get OpenCV preprocess + pytesseract.
    Returns plain text or None.
    """
    if is_pdf:
        if pdf_text:
            return pdf_text  # already have the text layer
        if pdf_img_bytes:
            processed = ocr_preprocess_image(pdf_img_bytes)
            return run_ocr(processed or pdf_img_bytes)
        return None
    # Image
    processed = ocr_preprocess_image(file_bytes)
    text = run_ocr(processed or file_bytes)
    if not text:
        # Try rotations
        for angle in [90, 180, 270]:
            try:
                im = Image.open(BytesIO(processed or file_bytes)).rotate(angle, expand=True)
                buf = BytesIO()
                im.save(buf, format="PNG")
                text = run_ocr(buf.getvalue())
                if text:
                    break
            except Exception:
                pass
    return text

def parse_ocr_fields(ocr_text: str) -> dict:
    """
    Extract amount and invoice number from raw OCR text.
    Returns {"amount": float|None, "invoice_no": str|None}
    """
    if not ocr_text:
        return {"amount": None, "invoice_no": None}

    amount = None
    invoice_no = None

    # Amount: look for labelled lines
    amount_patterns = [
        r"(?:grand\s*total|total\s*amount\s*due|amount\s*due|balance\s*due|total)[:\s]*(?:RM|USD|\$|INR|₹|Rs\.?)?\s*([\d,]+\.?\d*)",
        r"(?:RM|USD|\$|INR|₹|Rs\.?)\s*([\d,]+\.?\d{2})\b",
    ]
    for pat in amount_patterns:
        m = re.search(pat, ocr_text, re.I)
        if m:
            try:
                amount = float(m.group(1).replace(",", ""))
                if amount > 0:
                    break
            except ValueError:
                pass

    # Invoice / Receipt number
    id_patterns = [
        r"(?:invoice|inv|receipt|bill|order\s*id|ref)[^\n#:]*[#:\s]*([A-Z0-9][A-Z0-9\-/]{2,20})",
    ]
    for pat in id_patterns:
        m = re.search(pat, ocr_text, re.I)
        if m:
            invoice_no = m.group(1).strip().upper()
            break

    return {"amount": amount, "invoice_no": invoice_no}

# ── Groq API call ─────────────────────────────────────────────────────────────
def call_groq_api(prompt: str, image_b64: Optional[str] = None,
                  text_content: Optional[str] = None,
                  ocr_hint: Optional[str] = None,
                  model_name: str = None,
                  db = None) -> Optional[dict]:
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key or api_key == "your_groq_api_key_here":
        try:
            api_key = (get_setting("GROQ_API_KEY", "", db=db) or "").strip()
        except Exception:
            api_key = ""
    if not api_key or api_key == "your_groq_api_key_here":
        return None
    try:
        from groq import Groq
        client = Groq(api_key=api_key)
    except Exception as e:
        print(f"[Groq] init error: {e}")
        return None

    model = model_name or os.environ.get("GROQ_MODEL") or get_setting("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct", db=db)

    # Build message content
    content = []
    if text_content:
        hint_section = f"\n\nOCR HINT (may contain errors):\n{ocr_hint}" if ocr_hint else ""
        content.append({"type": "text",
                        "text": f"{prompt}\n\nDOCUMENT TEXT:\n{text_content}{hint_section}"})
    else:
        hint_section = f"\n\nOCR HINT (may contain errors — trust the image over this):\n{ocr_hint}" if ocr_hint else ""
        content.append({"type": "text", "text": f"{prompt}{hint_section}"})

    if image_b64:
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}})

    for attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": content}],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            return json.loads(resp.choices[0].message.content)
        except Exception as e:
            print(f"[Groq] attempt {attempt+1} failed: {e}")
    return None

# ── Merge two extractions (keep higher confidence per field) ─────────────────
def _conf(ext, field):
    return (ext.get("conf") or {}).get(field, 0.0) or 0.0

def merge_extractions(primary: dict, secondary: dict, conf_threshold: float = 0.7) -> dict:
    """Merge Pass B into Pass A: keep the field with higher confidence."""
    merged = dict(primary)
    for field in ("merchant", "city", "country", "issue_date", "total", "unique_id", "currency", "items", "model_category", "nights"):
        pa_conf = _conf(primary, field)
        pb_conf = _conf(secondary, field)
        if (primary.get(field) is None or pa_conf < conf_threshold) and pb_conf > pa_conf:
            merged[field] = secondary.get(field)
            if isinstance(merged.get("conf"), dict):
                merged["conf"][field] = pb_conf
    return merged

# ── Heuristic text parser (works for OCR text & PDF text) ─────────────────────
def parse_text_heuristic(text: str) -> dict:
    """Extract all key details from OCR or PDF text with high accuracy."""
    lines = [l.strip() for l in (text or "").split("\n") if l.strip()]
    if not lines:
        return {
            "merchant": None, "city": None, "country": None,
            "issue_date": None, "unique_id": None, "currency": "unknown",
            "total": 0.0, "subtotal": 0.0, "tax": 0.0,
            "discount": 0.0, "shipping": 0.0, "nights": None,
            "items": [], "items_readable": False,
            "model_category": "Miscellaneous", "alteration_notes": None,
            "conf": {"issue_date": 0.0, "total": 0.0, "unique_id": 0.0, "city": 0.0, "items": 0.0},
            "image_quality": "low"
        }

    merchant = None
    for l in lines[:4]:
        clean = re.sub(r"^[\|\~\"\'\s]+|[\|\~\"\'\s]+$", "", l)
        if not re.search(r"\b(receipt|invoice|bill|date|tax|welcome|total|thank|road|street|nagar|floor|block|avenue)\b", clean, re.I) and len(clean) >= 3:
            merchant = clean
            break
    if not merchant and lines:
        merchant = lines[0]

    issue_date = unique_id = city = country = None
    currency = "unknown"
    total = subtotal = tax = 0.0
    nights = None
    items = []
    text_lower = text.lower()

    # Issue Date (day-first by default)
    dm = re.search(r"\b(?:Date[:\s]*)?(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b", text, re.I)
    if dm:
        issue_date = f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"
    else:
        dm = re.search(r"\b(?:Date[:\s]*)?(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b", text, re.I)
        if dm:
            p1, p2, p3 = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
            # Day first by default per SPEC s7 rule 3
            if p2 <= 12 and p1 <= 31:
                issue_date = f"{p3:04d}-{p2:02d}-{p1:02d}"
            elif p1 <= 12 and p2 <= 31:
                issue_date = f"{p3:04d}-{p1:02d}-{p2:02d}"
        else:
            dm = re.search(r"\b(\d{1,2})[\s\-]+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\s\-]+(\d{4})\b", text, re.I)
            if dm:
                months = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
                m_num = months.get(dm.group(2).lower()[:3], 1)
                issue_date = f"{int(dm.group(3)):04d}-{m_num:02d}-{int(dm.group(1)):02d}"

    # Unique ID / Invoice No
    im = re.search(r"\b(?:Invoice\s*No|Receipt\s*No|Bill\s*No|Order\s*ID|Ref)[^\n#:]*[#:\s]*([A-Za-z0-9\-_/]{2,30})\b", text, re.I)
    if im:
        unique_id = im.group(1).strip()

    # Currency
    if re.search(r"\b(INR|₹|Rs\.?)\b", text, re.I):
        currency = "INR"
    elif re.search(r"\b(MYR|RM)\b", text, re.I):
        currency = "MYR"
    elif re.search(r"\b(EUR|€)\b", text, re.I):
        currency = "EUR"
    elif re.search(r"\b(GBP|£)\b", text, re.I):
        currency = "GBP"
    elif re.search(r"\b(USD|\$)\b", text, re.I):
        currency = "USD"

    # Total Amount
    tm = re.search(r"(?:grand\s*total|total\s*amount\s*due|amount\s*due|balance\s*due|total\s*(?:inr|usd|myr|\$|rm|rs)?|total)[:\s]*(?:RM|USD|\$|INR|₹|Rs\.?)?\s*([\d,]+\.?\d*)", text, re.I)
    if tm:
        try:
            total = float(tm.group(1).replace(",", ""))
        except ValueError:
            pass

    # Tax
    txm = re.search(r"\b(?:tax|vat|gst|cgst|sgst)[:\s]*(?:RM|USD|\$|INR|₹|Rs\.?)?\s*([\d,]+\.?\d*)", text, re.I)
    if txm:
        try:
            tax = float(txm.group(1).replace(",", ""))
        except ValueError:
            pass

    # Subtotal
    sbm = re.search(r"\b(?:subtotal|sub-total|sub\s*total|net\s*amount)[:\s]*(?:RM|USD|\$|INR|₹|Rs\.?)?\s*([\d,]+\.?\d*)", text, re.I)
    if sbm:
        try:
            subtotal = float(sbm.group(1).replace(",", ""))
        except ValueError:
            pass

    # Nights
    nm = re.search(r"\b(\d+)\s*nights?\b", text, re.I)
    if nm:
        try:
            nights = int(nm.group(1))
        except ValueError:
            pass

    # City / Country
    if re.search(r"\b(Bengaluru|Bangalore|Koramangala|Whitefield|Indiranagar|MG Road)\b", text, re.I):
        city, country = "Bengaluru", "India"
    elif re.search(r"\b(Delhi|New Delhi|Connaught Place)\b", text, re.I):
        city, country = "Delhi", "India"
    elif re.search(r"\b(Gurgaon|Gurugram)\b", text, re.I):
        city, country = "Gurgaon", "India"
    elif re.search(r"\b(Mumbai|Bombay)\b", text, re.I):
        city, country = "Mumbai", "India"
    elif re.search(r"\b(Seattle)\b", text, re.I):
        city, country = "Seattle", "United States"
    elif re.search(r"\b(Kuala Lumpur|Johor Bahru|Penang)\b", text, re.I):
        city, country = "Kuala Lumpur", "Malaysia"
    elif re.search(r"\b(Singapore)\b", text, re.I):
        city, country = "Singapore", "Singapore"
    elif re.search(r"\b(London)\b", text, re.I):
        city, country = "London", "United Kingdom"

    # Line Items & Banned item detection
    banned_alcohol = re.compile(r"\b(beer|wine|vodka|whisky|whiskey|rum|gin|tequila|cocktail|liquor|alcohol|scotch|brandy|cider|bar|pint)\b", re.I)
    banned_tobacco = re.compile(r"\b(cigarette|cigar|vape|tobacco|smoke|nicotine)\b", re.I)
    banned_fine = re.compile(r"\b(fine|penalty|violation|late\s*fee|parking\s*ticket)\b", re.I)
    banned_gambling = re.compile(r"\b(casino|lottery|bet|gambling|poker)\b", re.I)

    for line in lines:
        if re.search(r"(total|subtotal|tax|date|receipt|invoice|thank|road|place|sector|m\.g\.|connaught)", line, re.I):
            continue
        m = re.search(r"^([A-Za-z0-9\s\-]+?)\s+(\d+(?:\.\d+)?)\s+([\d,]+\.?\d{2})$", line)
        if m:
            name = m.group(1).strip()
            qty = float(m.group(2))
            amt = float(m.group(3).replace(",", ""))
            b_match = None
            if banned_alcohol.search(name):
                b_match = "alcohol"
            elif banned_tobacco.search(name):
                b_match = "tobacco"
            elif banned_fine.search(name):
                b_match = "fine/penalty"
            elif banned_gambling.search(name):
                b_match = "gambling"
            items.append({
                "name": name,
                "qty": qty,
                "amount": amt,
                "banned_match": b_match,
                "banned_conf": 0.95 if b_match else 0.0
            })
        else:
            # Try single price line: "Stapler 180.00"
            m2 = re.search(r"^([A-Za-z0-9\s\-]{3,35})\s+([\d,]+\.?\d{2})$", line)
            if m2 and not re.search(r"(date|invoice|receipt|tax|vat|total)", m2.group(1), re.I):
                name = m2.group(1).strip()
                amt = float(m2.group(2).replace(",", ""))
                b_match = "alcohol" if banned_alcohol.search(name) else None
                items.append({
                    "name": name,
                    "qty": 1.0,
                    "amount": amt,
                    "banned_match": b_match,
                    "banned_conf": 0.95 if b_match else 0.0
                })

    # Category classification
    if any(k in text_lower for k in ("hotel", "room", "nights", "stay", "inn", "resort", "suites")):
        cat = "Accommodation"
    elif any(k in text_lower for k in ("restaurant", "cafe", "bar", "grill", "pub", "food", "meal", "wings", "whisky", "beer", "breakfast", "dining", "kitchen", "bakery", "coffee", "pizza", "burger")):
        cat = "Food"
    elif any(k in text_lower for k in ("cab", "taxi", "uber", "ola", "flight", "airline", "train", "metro", "fare", "toll", "parking")):
        cat = "Travel"
    elif any(k in text_lower for k in ("chair", "desk", "laptop", "monitor", "keyboard", "mouse", "usb", "stationer", "paper", "stapler", "hardware", "printer", "storage", "furniture")):
        cat = "Equipment"
    elif any(k in text_lower for k in ("course", "exam", "certification", "training", "book", "tuition", "workshop", "seminar")):
        cat = "Education"
    else:
        cat = "Miscellaneous"

    is_blank = (total == 0.0 and not unique_id and not items)

    return {
        "merchant": merchant if not is_blank else None,
        "city": city,
        "country": country,
        "issue_date": issue_date,
        "unique_id": unique_id,
        "currency": currency if not is_blank else "unknown",
        "total": total,
        "subtotal": subtotal,
        "tax": tax,
        "discount": 0.0,
        "shipping": 0.0,
        "nights": nights,
        "items": items,
        "items_readable": not is_blank and (len(items) > 0 or total > 0),
        "model_category": cat,
        "alteration_notes": None,
        "conf": {
            "issue_date": 0.95 if issue_date else 0.0,
            "total": 0.95 if total > 0 else 0.0,
            "unique_id": 0.95 if unique_id else 0.0,
            "city": 0.90 if city else 0.0,
            "items": 0.90 if items else (0.80 if total > 0 else 0.0)
        },
        "image_quality": "low" if is_blank else "high"
    }

parse_pdf_text_heuristic = parse_text_heuristic

# ── OCR field fallback (fill nulls from labelled OCR text) ───────────────────
def ocr_field_fallback(extraction: dict, ocr_text: Optional[str], conf_threshold: float = 0.7) -> dict:
    """
    If model returned null or low confidence for any critical fields, backfill
    from the structured OCR parser. SPEC s7: these values still go through vote.
    """
    if not ocr_text:
        return extraction
    parsed_ocr = parse_text_heuristic(ocr_text)
    sources = extraction.get("_field_sources", {})

    # Amount fallback
    if extraction.get("total") is None or float(extraction.get("total") or 0.0) == 0.0 or _conf(extraction, "total") < conf_threshold:
        if parsed_ocr.get("total") and parsed_ocr["total"] > 0:
            extraction["total"] = parsed_ocr["total"]
            extraction.setdefault("conf", {})["total"] = 0.90
            sources["total"] = "ocr"

    # Unique ID fallback
    if extraction.get("unique_id") is None or _conf(extraction, "unique_id") < conf_threshold:
        if parsed_ocr.get("unique_id"):
            extraction["unique_id"] = parsed_ocr["unique_id"]
            extraction.setdefault("conf", {})["unique_id"] = 0.90
            sources["unique_id"] = "ocr"

    # Date fallback
    if extraction.get("issue_date") is None or _conf(extraction, "issue_date") < conf_threshold:
        if parsed_ocr.get("issue_date"):
            extraction["issue_date"] = parsed_ocr["issue_date"]
            extraction.setdefault("conf", {})["issue_date"] = 0.90
            sources["issue_date"] = "ocr"

    # Merchant fallback
    if not extraction.get("merchant") and parsed_ocr.get("merchant"):
        extraction["merchant"] = parsed_ocr["merchant"]
        sources["merchant"] = "ocr"

    # City & Country fallback
    if not extraction.get("city") and parsed_ocr.get("city"):
        extraction["city"] = parsed_ocr["city"]
        extraction["country"] = parsed_ocr.get("country") or extraction.get("country")
        extraction.setdefault("conf", {})["city"] = 0.85
        sources["city"] = "ocr"

    # Currency fallback
    if (extraction.get("currency") in (None, "unknown")) and parsed_ocr.get("currency") != "unknown":
        extraction["currency"] = parsed_ocr["currency"]
        sources["currency"] = "ocr"

    # Nights fallback
    if extraction.get("nights") is None and parsed_ocr.get("nights") is not None:
        extraction["nights"] = parsed_ocr["nights"]

    # Tax & Subtotal fallback
    if not extraction.get("tax") and parsed_ocr.get("tax"):
        extraction["tax"] = parsed_ocr["tax"]
    if not extraction.get("subtotal") and parsed_ocr.get("subtotal"):
        extraction["subtotal"] = parsed_ocr["subtotal"]

    # Items fallback
    if not extraction.get("items") and parsed_ocr.get("items"):
        extraction["items"] = parsed_ocr["items"]
        extraction["items_readable"] = True
        extraction.setdefault("conf", {})["items"] = 0.90
        sources["items"] = "ocr"

    # Category fallback
    if extraction.get("model_category") in (None, "Miscellaneous") and parsed_ocr.get("model_category") != "Miscellaneous":
        extraction["model_category"] = parsed_ocr["model_category"]

    extraction["_field_sources"] = sources
    return extraction

# ── Cache ─────────────────────────────────────────────────────────────────────
def get_cached_extraction(file_hash: str, db=None) -> Optional[dict]:
    close_db = db is None
    if close_db:
        db = get_db()
    row = db.execute("SELECT json FROM extraction_cache WHERE file_hash = ?", (file_hash,)).fetchone()
    if close_db:
        db.close()
    return json.loads(row["json"]) if row else None

def save_cached_extraction(file_hash: str, model_name: str, extraction_dict: dict, db=None):
    close_db = db is None
    if close_db:
        db = get_db()
    db.execute(
        "INSERT INTO extraction_cache (file_hash, model, json, created_at) "
        "VALUES (?, ?, ?, datetime('now')) "
        "ON CONFLICT(file_hash) DO UPDATE SET model=excluded.model, json=excluded.json",
        (file_hash, model_name, json.dumps(extraction_dict))
    )
    db.commit()
    if close_db:
        db.close()

# ── Main entry point ──────────────────────────────────────────────────────────
def extract_receipt(file_bytes: bytes, file_name: str, db=None) -> dict:
    """
    SPEC s7 pipeline:
      ingest → OCR reader 2 → Pass A (Groq) → Pass B (if needed) → OCR field fallback → cache
    """
    file_hash = compute_file_hash(file_bytes)
    cached = get_cached_extraction(file_hash, db=db)
    if cached:
        return cached

    conf_threshold = float(get_setting("CONF_THRESHOLD", "0.7", db=db))
    model_a = os.environ.get("GROQ_MODEL") or get_setting("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct", db=db)
    model_b = os.environ.get("GROQ_MODEL_FALLBACK", "") or model_a
    is_pdf = file_name.lower().endswith(".pdf")

    # ── Ingest ──
    pdf_text = pdf_img_b64 = pdf_img_bytes = img_b64 = img_bytes = None
    if is_pdf:
        pdf_text, pdf_img_b64, pdf_img_bytes = extract_pdf_content(file_bytes)
    else:
        img_b64, img_bytes = resize_image_for_model(file_bytes)

    # ── Reader 2 (OCR) ──
    ocr_text_raw = ocr_reader2(file_bytes, is_pdf, pdf_text=pdf_text, pdf_img_bytes=pdf_img_bytes)
    ocr_text = mask_credit_card(ocr_text_raw or "")  # mask before storing

    # ── Pass A (Groq vision / text) ──
    extracted_raw = None
    if is_pdf and pdf_text:
        extracted_raw = call_groq_api(EXTRACTION_SYSTEM_PROMPT,
                                      text_content=pdf_text,
                                      ocr_hint=None,
                                      model_name=model_a,
                                      db=db)
        if not extracted_raw:
            extracted_raw = parse_text_heuristic(pdf_text)
    else:
        b64_to_use = pdf_img_b64 or img_b64
        extracted_raw = call_groq_api(EXTRACTION_SYSTEM_PROMPT,
                                      image_b64=b64_to_use,
                                      ocr_hint=ocr_text or None,
                                      model_name=model_a,
                                      db=db)

    # Offline / OCR fallback: extract all structured fields from OCR text or PDF text
    if not extracted_raw:
        extracted_raw = parse_text_heuristic(ocr_text or pdf_text or "")

    # ── Pass B (re-run if required field missing or low-conf) ──
    required_fields = ("issue_date", "total", "unique_id", "currency", "items")
    needs_pass_b = any(
        extracted_raw.get(f) is None or _conf(extracted_raw, f) < conf_threshold
        for f in required_fields
    )
    if needs_pass_b and (pdf_img_b64 or img_b64):
        # Use OpenCV-enhanced image for pass B
        if img_bytes:
            processed = ocr_preprocess_image(img_bytes)
            if processed:
                buf = BytesIO(processed)
                im = Image.open(buf)
                out = BytesIO()
                im.convert("RGB").save(out, format="JPEG", quality=88)
                b64_b = base64.b64encode(out.getvalue()).decode()
            else:
                b64_b = img_b64
        else:
            b64_b = pdf_img_b64

        extracted_b = call_groq_api(EXTRACTION_SYSTEM_PROMPT,
                                    image_b64=b64_b,
                                    ocr_hint=ocr_text or None,
                                    model_name=model_b)
        if extracted_b:
            extracted_raw = merge_extractions(extracted_raw, extracted_b, conf_threshold)

    # ── OCR field fallback ──
    extracted_raw = ocr_field_fallback(extracted_raw, ocr_text, conf_threshold)

    # ── Mask + validate ──
    extracted_masked = mask_extracted_data(extracted_raw)
    # Store ocr_text for caller (rules/verify will use it)
    extracted_masked["_ocr_text"] = ocr_text

    try:
        # Validate SPEC contract fields only (strip private _* keys before Pydantic)
        pydantic_input = {k: v for k, v in extracted_masked.items() if not k.startswith("_")}
        validated = ExtractionModel(**pydantic_input).model_dump()
        # Restore private fields
        for k, v in extracted_masked.items():
            if k.startswith("_"):
                validated[k] = v
    except Exception as e:
        print(f"[Pydantic] validation warning: {e}")
        validated = extracted_masked

    save_cached_extraction(file_hash, model_a, validated, db=db)
    return validated
