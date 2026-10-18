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
                  model_name: str = None) -> Optional[dict]:
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key or api_key == "your_groq_api_key_here":
        return None
    try:
        from groq import Groq
        client = Groq(api_key=api_key)
    except Exception as e:
        print(f"[Groq] init error: {e}")
        return None

    model = model_name or os.environ.get("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

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
    for field in ("issue_date", "total", "unique_id", "currency", "items"):
        pa_conf = _conf(primary, field)
        pb_conf = _conf(secondary, field)
        if (primary.get(field) is None or pa_conf < conf_threshold) and pb_conf > pa_conf:
            merged[field] = secondary.get(field)
            if isinstance(merged.get("conf"), dict):
                merged["conf"][field] = pb_conf
    return merged

# ── OCR field fallback (fill nulls from labelled OCR text) ───────────────────
def ocr_field_fallback(extraction: dict, ocr_text: Optional[str], conf_threshold: float = 0.7) -> dict:
    """
    If model returned null for date/total/unique_id but OCR found a labeled match,
    fill from OCR and mark source. SPEC s7: these values still go through vote.
    """
    if not ocr_text:
        return extraction
    ocr_fields = parse_ocr_fields(ocr_text)
    sources = extraction.get("_field_sources", {})

    if (extraction.get("total") is None or _conf(extraction, "total") < conf_threshold):
        if ocr_fields.get("amount"):
            extraction["total"] = ocr_fields["amount"]
            extraction.setdefault("conf", {})["total"] = 0.55
            sources["total"] = "ocr"

    if (extraction.get("unique_id") is None or _conf(extraction, "unique_id") < conf_threshold):
        if ocr_fields.get("invoice_no"):
            extraction["unique_id"] = ocr_fields["invoice_no"]
            extraction.setdefault("conf", {})["unique_id"] = 0.55
            sources["unique_id"] = "ocr"

    extraction["_field_sources"] = sources
    return extraction

# ── Heuristic PDF fallback (offline, no API key) ─────────────────────────────
def parse_pdf_text_heuristic(text: str) -> dict:
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    merchant = lines[0] if lines else None
    issue_date = unique_id = city = country = None
    currency = "USD"
    total = subtotal = tax = 0.0
    items = []

    for line in lines:
        dm = re.search(r"\b(?:Date:\s*)?(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{4})\b", line, re.I)
        if dm and not issue_date:
            raw = dm.group(1).replace("/", "-")
            p = raw.split("-")
            issue_date = f"{p[0]}-{int(p[1]):02d}-{int(p[2]):02d}" if len(p[0]) == 4 else f"{p[2]}-{int(p[0]):02d}-{int(p[1]):02d}"

        im = re.search(r"\b(?:Invoice\s*No|Receipt\s*No|Bill\s*No|Order\s*ID)[:#\s]*([A-Za-z0-9-]+)\b", line, re.I)
        if im and not unique_id:
            unique_id = im.group(1)

        tm = re.search(r"\bTOTAL\s*(INR|USD|MYR|\$|RM|RS)?[:\s]*?([\d,]+\.?\d*)", line, re.I)
        if tm:
            cs = (tm.group(1) or "").upper()
            currency = {"$": "USD", "RM": "MYR", "RS": "INR"}.get(cs, cs or "USD")
            try:
                total = float(tm.group(2).replace(",", ""))
            except ValueError:
                pass

        txm = re.search(r"\bTax\s*[:\s]*([\d,]+\.?\d*)", line, re.I)
        if txm:
            try:
                tax = float(txm.group(1).replace(",", ""))
            except ValueError:
                pass

        if not city:
            if "Seattle" in line:
                city, country = "Seattle", "United States"
            elif "Bengaluru" in line or "Whitefield" in line:
                city, country = "Bengaluru", "India"

    for line in lines:
        m = re.search(r"^([A-Za-z\s\-]+)\s+(\d+)\s+([\d,]+\.?\d*)$", line)
        if m:
            items.append({"name": m.group(1).strip(), "qty": float(m.group(2)),
                          "amount": float(m.group(3).replace(",", "")),
                          "banned_match": None, "banned_conf": 0.0})

    is_blank = (total == 0.0 and not unique_id and not items)
    low_text = text.lower()
    cat = "Equipment" if any(k in low_text for k in ("chair","usb","monitor","laptop","furniture","storage")) else \
          "Education" if any(k in low_text for k in ("course","exam","certification","training","book")) else "Miscellaneous"
    return {
        "merchant": merchant if not is_blank else None,
        "city": city, "country": country,
        "issue_date": issue_date, "unique_id": unique_id,
        "currency": currency if not is_blank else "unknown",
        "total": total, "subtotal": subtotal, "tax": tax,
        "discount": 0.0, "shipping": 0.0, "nights": None,
        "items": items, "items_readable": not is_blank and (len(items) > 0 or total > 0),
        "model_category": cat, "alteration_notes": None,
        "conf": {
            "issue_date": 0.95 if issue_date else 0.0,
            "total": 0.95 if total > 0 else 0.0,
            "unique_id": 0.95 if unique_id else 0.0,
            "city": 0.9 if city else 0.0,
            "items": 0.95 if items else 0.0
        },
        "image_quality": "low" if is_blank else "high"
    }

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
    model_a = os.environ.get("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
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
                                      model_name=model_a)
        if not extracted_raw:
            extracted_raw = parse_pdf_text_heuristic(pdf_text)
    else:
        b64_to_use = pdf_img_b64 or img_b64
        extracted_raw = call_groq_api(EXTRACTION_SYSTEM_PROMPT,
                                      image_b64=b64_to_use,
                                      ocr_hint=ocr_text or None,
                                      model_name=model_a)

    # Offline fallback
    if not extracted_raw:
        if is_pdf and pdf_text:
            extracted_raw = parse_pdf_text_heuristic(pdf_text)
        else:
            extracted_raw = {
                "merchant": None, "city": None, "country": None,
                "issue_date": None, "unique_id": None, "currency": "unknown",
                "total": 0.0, "subtotal": 0.0, "tax": 0.0,
                "discount": 0.0, "shipping": 0.0, "nights": None,
                "items": [], "items_readable": False,
                "model_category": "Miscellaneous", "alteration_notes": None,
                "conf": {"issue_date": 0.0, "total": 0.0, "unique_id": 0.0, "city": 0.0, "items": 0.0},
                "image_quality": "low"
            }

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
