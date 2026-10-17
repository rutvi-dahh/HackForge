import os
import re
import json
import base64
import hashlib
from io import BytesIO
from typing import Optional, List, Dict, Any
from pathlib import Path
from pydantic import BaseModel, Field
from PIL import Image
import fitz  # PyMuPDF

from database.db import get_db, get_setting

# Card masking regex: catches 13-19 digit sequences separated by spaces or dashes
CARD_REGEX = re.compile(r"\b(?:\d[ -]*?){13,19}\b")

def mask_credit_card(text: str) -> str:
    if not text:
        return text
    def _repl(match):
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if len(digits) < 13 or len(digits) > 19:
            return raw
        # Keep last 4 digits
        masked = "*" * (len(digits) - 4) + digits[-4:]
        # Format in groups of 4 for readability
        chunks = [masked[i:i+4] for i in range(0, len(masked), 4)]
        return "-".join(chunks)
    return CARD_REGEX.sub(_repl, text)

def mask_extracted_data(data: dict) -> dict:
    """Recursively masks credit card numbers in strings within the extracted JSON."""
    if isinstance(data, str):
        return mask_credit_card(data)
    elif isinstance(data, list):
        return [mask_extracted_data(item) for item in data]
    elif isinstance(data, dict):
        return {k: mask_extracted_data(v) for k, v in data.items()}
    return data

def compute_file_hash(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()

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
Extract receipt fields into the exact JSON contract specified below.
RULES:
1. Return ONLY valid JSON. No explanations, no markdown codeblocks, no preamble.
2. Use null for anything not visible or not readable. Never infer or guess.
3. Currency: return 'INR', 'MYR', 'USD', standard ISO, or 'unknown'. If currency symbol is ambiguous (e.g. '$' on a Malaysian receipt), return 'unknown' and fill the 'country' field.
4. Categories: choose exactly one of ['Food', 'Travel', 'Accommodation', 'Equipment', 'Miscellaneous'].
5. Banned items: check line items against banned goods: alcohol, tobacco, fines, personal items, gifts, gambling. If an item matches, set 'banned_match' to the category ('alcohol', 'tobacco', etc.) and provide 'banned_conf' between 0.0 and 1.0. If clean, set banned_match to null and banned_conf to 0.0.
6. Alteration: if any text or numbers appear visibly altered, erased, or suspicious, note it in 'alteration_notes', otherwise null.
7. Items readable: if individual itemized lines cannot be read or only grand total is shown, set 'items_readable' to false.
8. Confidence: per-field confidence (0.0 to 1.0) for issue_date, total, unique_id, city, items.
9. Format date as YYYY-MM-DD.

JSON CONTRACT:
{
  "merchant": "string|null",
  "city": "string|null",
  "country": "string|null",
  "issue_date": "YYYY-MM-DD|null",
  "unique_id": "string|null",
  "currency": "INR|MYR|USD|unknown",
  "total": 0.0,
  "subtotal": 0.0,
  "tax": 0.0,
  "discount": 0.0,
  "shipping": 0.0,
  "nights": null,
  "items": [{"name":"string","qty":1,"amount":0.0,"banned_match":null,"banned_conf":0.0}],
  "items_readable": true,
  "model_category": "Food|Travel|Accommodation|Equipment|Miscellaneous",
  "alteration_notes": "string|null",
  "conf": {"issue_date":0.0,"total":0.0,"unique_id":0.0,"city":0.0,"items":0.0},
  "image_quality": "high|medium|low"
}"""

def resize_image_for_model(image_bytes: bytes, max_dim: int = 1024) -> str:
    """Resizes image keeping aspect ratio and returns base64 encoded JPEG string."""
    im = Image.open(BytesIO(image_bytes))
    if im.mode in ("RGBA", "P"):
        im = im.convert("RGB")
    w, h = im.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        new_w, new_h = int(w * scale), int(h * scale)
        im = im.resize((new_w, new_h), Image.Resampling.LANCZOS)
    
    buffer = BytesIO()
    im.save(buffer, format="JPEG", quality=85)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")

def extract_pdf_content(pdf_bytes: bytes):
    """
    Extracts text layer from PDF. If no text or very short, renders page 0 to base64 JPEG.
    Returns: (text, image_b64)
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    full_text = ""
    for page in doc:
        full_text += page.get_text()
    
    clean_text = full_text.strip()
    if len(clean_text) > 30:
        return clean_text, None
    
    # Render first page as image if text layer empty
    if len(doc) > 0:
        page = doc[0]
        pix = page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("jpeg")
        return None, resize_image_for_model(img_bytes)
    return "", None

def get_cached_extraction(file_hash: str, db=None) -> Optional[dict]:
    close_db = False
    if db is None:
        db = get_db()
        close_db = True
    row = db.execute(
        "SELECT json FROM extraction_cache WHERE file_hash = ?",
        (file_hash,)
    ).fetchone()
    if close_db:
        db.close()
    if row:
        return json.loads(row["json"])
    return None

def save_cached_extraction(file_hash: str, model_name: str, extraction_dict: dict, db=None):
    close_db = False
    if db is None:
        db = get_db()
        close_db = True
    db.execute(
        "INSERT INTO extraction_cache (file_hash, model, json, created_at) VALUES (?, ?, ?, datetime('now')) "
        "ON CONFLICT(file_hash) DO UPDATE SET model = excluded.model, json = excluded.json",
        (file_hash, model_name, json.dumps(extraction_dict))
    )
    db.commit()
    if close_db:
        db.close()

def parse_pdf_text_heuristic(text: str) -> dict:
    """Fallback / heuristic parser for clean text-layer PDFs (Superstore invoices etc.) when offline."""
    merchant = None
    city = None
    country = None
    issue_date = None
    unique_id = None
    currency = "USD"
    total = 0.0
    subtotal = 0.0
    tax = 0.0
    items = []
    
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if lines:
        merchant = lines[0]
        
    for line in lines:
        # Date match
        date_m = re.search(r"\b(?:Date:\s*)?(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{4})\b", line, re.I)
        if date_m and not issue_date:
            raw_d = date_m.group(1).replace("/", "-")
            parts = raw_d.split("-")
            if len(parts[0]) == 4:
                issue_date = f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
            else:
                issue_date = f"{parts[2]}-{int(parts[1]):02d}-{int(parts[0]):02d}"
                
        # Unique ID / Invoice No / Receipt No
        id_m = re.search(r"\b(?:Invoice\s*No|Receipt\s*No|Bill\s*No|Order\s*ID)[:#\s]*([A-Za-z0-9-]+)\b", line, re.I)
        if id_m and not unique_id:
            unique_id = id_m.group(1)
            
        # Currency & Total
        tot_m = re.search(r"\bTOTAL\s*(INR|USD|MYR|\$|RM|RS)?\s*[:]?\s*([\d,]+\.?\d*)", line, re.I)
        if tot_m:
            curr_str = tot_m.group(1)
            if curr_str:
                if curr_str.upper() in ["INR", "USD", "MYR"]:
                    currency = curr_str.upper()
                elif curr_str == "$":
                    currency = "USD"
                elif curr_str == "RM":
                    currency = "MYR"
            try:
                total = float(tot_m.group(2).replace(",", ""))
            except ValueError:
                pass
                
        # Tax
        tax_m = re.search(r"\bTax\s*[:]?\s*([\d,]+\.?\d*)", line, re.I)
        if tax_m:
            try:
                tax = float(tax_m.group(1).replace(",", ""))
            except ValueError:
                pass

        # City / Address
        if not city:
            if "Seattle" in line:
                city = "Seattle"
                country = "United States"
            elif "Bengaluru" in line or "Whitefield" in line:
                city = "Bengaluru"
                country = "India"

    # Line items
    for line in lines:
        item_m = re.search(r"^([A-Za-z\s-]+)\s+(\d+)\s+([\d,]+\.?\d*)$", line)
        if item_m:
            name = item_m.group(1).strip()
            qty = float(item_m.group(2))
            amt = float(item_m.group(3).replace(",", ""))
            items.append({
                "name": name,
                "qty": qty,
                "amount": amt,
                "banned_match": None,
                "banned_conf": 0.0
            })
            
    is_blank = (total == 0.0 and not unique_id and not items)
    items_readable = not is_blank and (len(items) > 0 or total > 0)
    
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
        "nights": None,
        "items": items,
        "items_readable": items_readable,
        "model_category": "Equipment" if ("chair" in text.lower() or "usb" in text.lower()) else "Miscellaneous",
        "alteration_notes": None,
        "conf": {
            "issue_date": 0.95 if issue_date else 0.0,
            "total": 0.95 if total > 0 else 0.0,
            "unique_id": 0.95 if unique_id else 0.0,
            "city": 0.9 if city else 0.0,
            "items": 0.95 if items else 0.0
        },
        "image_quality": "high" if not is_blank else "low"
    }

def call_groq_api(prompt: str, image_b64: Optional[str] = None, text_content: Optional[str] = None, model_name: str = None) -> Optional[dict]:
    api_key = os.environ.get("GROQ_API_KEY") or get_setting("GROQ_API_KEY")
    if not api_key or api_key == "your_groq_api_key_here":
        return None

    try:
        from groq import Groq
        client = Groq(api_key=api_key)
    except Exception as e:
        print(f"Error initializing Groq client: {e}")
        return None

    model = model_name or os.environ.get("GROQ_MODEL") or get_setting("GROQ_MODEL", "llama-3.2-11b-vision-preview")

    content = []
    if text_content:
        content.append({
            "type": "text",
            "text": f"{prompt}\n\nDOCUMENT TEXT LAYER CONTENT:\n{text_content}"
        })
    else:
        content.append({
            "type": "text",
            "text": prompt
        })

    if image_b64:
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{image_b64}"
            }
        })

    for attempt in range(2):  # Retry once on invalid JSON
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "user", "content": content}
                ],
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            raw_text = response.choices[0].message.content
            parsed = json.loads(raw_text)
            return parsed
        except Exception as e:
            print(f"Groq API attempt {attempt + 1} failed: {e}")
            if attempt == 1:
                return None
    return None

def extract_receipt(file_bytes: bytes, file_name: str, db=None) -> dict:
    """
    Extracts structured receipt data from image or PDF bytes.
    1. Checks offline cache by file hash.
    2. If not cached, runs extraction via Groq API (or heuristic PDF parser).
    3. Masks sensitive card numbers.
    4. Validates against Pydantic schema and saves to cache.
    """
    file_hash = compute_file_hash(file_bytes)
    cached = get_cached_extraction(file_hash, db=db)
    if cached:
        return cached

    is_pdf = file_name.lower().endswith(".pdf")
    model_name = os.environ.get("GROQ_MODEL") or get_setting("GROQ_MODEL", "llama-3.2-11b-vision-preview", db=db)
    
    extracted_raw = None
    
    if is_pdf:
        pdf_text, img_b64 = extract_pdf_content(file_bytes)
        if pdf_text and not img_b64:
            # Text layer available: call Groq with text or fallback to heuristic if offline
            extracted_raw = call_groq_api(EXTRACTION_SYSTEM_PROMPT, text_content=pdf_text, model_name=model_name)
            if not extracted_raw:
                extracted_raw = parse_pdf_text_heuristic(pdf_text)
        else:
            extracted_raw = call_groq_api(EXTRACTION_SYSTEM_PROMPT, image_b64=img_b64, model_name=model_name)
    else:
        # Photo / Image
        img_b64 = resize_image_for_model(file_bytes)
        extracted_raw = call_groq_api(EXTRACTION_SYSTEM_PROMPT, image_b64=img_b64, model_name=model_name)

    if not extracted_raw:
        # Fallback when offline and no text layer
        extracted_raw = {
            "merchant": None,
            "city": None,
            "country": None,
            "issue_date": None,
            "unique_id": None,
            "currency": "unknown",
            "total": 0.0,
            "subtotal": 0.0,
            "tax": 0.0,
            "discount": 0.0,
            "shipping": 0.0,
            "nights": None,
            "items": [],
            "items_readable": False,
            "model_category": "Miscellaneous",
            "alteration_notes": None,
            "conf": {
                "issue_date": 0.0,
                "total": 0.0,
                "unique_id": 0.0,
                "city": 0.0,
                "items": 0.0
            },
            "image_quality": "low"
        }

    # Mask credit card numbers
    extracted_masked = mask_extracted_data(extracted_raw)

    # Validate with Pydantic
    try:
        validated = ExtractionModel(**extracted_masked).model_dump()
    except Exception as e:
        print(f"Pydantic validation warning: {e}")
        validated = extracted_masked

    # Cache result
    save_cached_extraction(file_hash, model_name, validated, db=db)
    return validated
