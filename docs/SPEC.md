SPEC: Receipt Graveyard

Source of truth for the build. Policy values live in docs/POLICY.md. If something is unspecified, choose the simplest option and log it in the README under ASSUMPTIONS.

1. Overview

Two-role web app. Employees upload receipts (JPG, PNG, PDF) and get instant policy feedback. An admin reviews a pre-checked queue, grouped by category or employee, and records a written reason for every decision.

Principle: AI reads, code decides. The vision model only extracts fields and tags items. Python rules decide every status. Nothing is guessed: unreadable or low-confidence data becomes Info needed or a flag.

Stack: Flask, SQLite (SQLAlchemy or plain sqlite3), Jinja templates, plain CSS, no JS build step, PyMuPDF, Groq SDK, pytest. Base currency INR. Runs locally with python app.py.

Data: self-created and public sample data, not organizer-provided. Receipt photos are Malaysian (MYR, 2016 to 2018). PDF invoices are US (USD, 2012 to 2013, sample "Superstore" data). Mark every seeded or planted row is_seeded=1.

2. Scope

MVP: login (2 roles), upload with category, context, attendees, client; extraction; rule engine with statuses and flags; duplicate groups; per-receipt, per-day, per-month limits with city tiers; admin queue (category view, employee toggle), receipt viewer, flag panel, mandatory decision reason; Rejected & Queries tab (one query per receipt, admin uphold or overturn); Help page; admin currency and FX settings.

Stretch, in order (only after MVP works end to end): bulk approve in a category; employee edits extracted fields (original kept); admin category override; CSV export; Help chatbot answering from the manual only.

Out of scope: email or WhatsApp ingestion, payments, SSO, live FX, partial approval of mixed receipts, approval thresholds, model training, mobile app.

3. Pages and flows

Seeded accounts only: 1 admin, 4 employees. Hashed passwords. Role checked server-side on every admin route (employee gets 403). Employees see only their own receipts.

Employee

/login, /logout
/upload: file; category (Food, Travel, Accommodation, Equipment, Miscellaneous); context (Trip or Office); attendee count (int >= 1, required for Food); "client-related" checkbox, which makes client name required. Submit runs the pipeline and shows the result page: status plus every reason in plain English.
/receipts: own receipts with status and reasons. Info needed rows have a form for the missing fields only. Saving re-runs the rules on the same record.
/receipts/<id>: detail. If rejected and no query exists: one query form (single message). Shows the admin's response once given.
/help: helpline number (config HELPLINE_NUMBER, placeholder until supplied) and a static user manual with the claim steps. Linked in the footer of every user page.

Admin

/admin: queue of status verified and flagged only. Default view groups by category. ?view=employee groups by employee. Duplicate groups render together, earliest upload labeled "original". Row columns: employee name and code, issue date, amount in base currency, category, status, flag count.
/admin/receipt/<id>: employee name and code, issue date, original and base amount, user category and model category, context, attendees, client, city and tier, items. A "View receipt" button opens the file. Side panel lists every flag (code, policy clause, message). Approve or Deny form; reason required, non-empty.
/admin/rejected: auto-rejected receipts and their queries. Per query: one response, resolution upheld or overturned, reason required. Overturned makes the receipt approved.
/admin/settings: base currency and FX table (currency, rate to INR, set date).

Rejections are final: no resubmission (the same file is caught as a duplicate). One open query per receipt, one message, one reply, no thread.

4. Decision logic

Run after extraction and again after the employee completes missing info. Stop at the first auto-reject; otherwise collect all flags.

Reject (rejected, final):
NON_REIMBURSABLE: any item with banned_match set and banned_conf at or above the banned threshold. A mixed receipt is rejected whole.
LATE_UPLOAD: upload date minus issue date greater than 15 days. Day 15 is valid. Skipped for is_seeded.
Info needed (info_needed): issue date, total, or unique ID missing or below the confidence threshold; Food without attendee count; client-related without client name. Items unreadable is a flag (below), not Info needed.
Flags (status flagged): collect all that apply.
Code	Trigger
ITEMS_UNCLEAR	items_readable false, or only a total shown
MATH_MISMATCH	items, minus discount, plus shipping and tax, differ from total by more than rounding tolerance (INR 1 or 1 percent, whichever is larger)
CATEGORY_MISMATCH	user category differs from model_category
ACCOMMODATION_OFFICE	Accommodation with Office context
LIMIT_RECEIPT, LIMIT_DAY, LIMIT_MONTH	a limit in POLICY.md exceeded
CITY_UNCLEAR	city not found in cities.json; Tier C limits applied
DUPLICATE	same unique ID, or same merchant + issue date + amount (across image and PDF), or same file hash
SUSPICIOUS_FUTURE_DATE, SUSPICIOUS_ROUND_TOTAL, SUSPICIOUS_ALTERATION	issue date after today; total a round multiple of 100 in base currency with no item that explains it; non-empty alteration_notes
Otherwise verified.
Admin sets approved or denied on verified and flagged items, with a reason.

Each flag stores code, clause (POLICY.md section), amount_affected, message. Limit messages show the math, e.g. "Food this month: 12,000 already claimed + 4,000 = 16,000, limit 15,000".

Limit evaluation

Amounts are in base currency (INR) after conversion.
Per-person Food amount = receipt amount / attendee count.
Per receipt: compare to the receipt limit. Food: per-person amount vs per-person-per-meal limit.
Per day (same employee, same issue date, same category): Food sums per-person amounts; Miscellaneous sums amounts.
Per month (same employee, same category, same calendar month of issue date): sum of full amounts.
Counted receipts: approved, verified, flagged. Excluded: rejected, denied, info_needed.
Process receipts by issue date. The receipt that pushes a total over is flagged. Do not re-flag earlier decided receipts.
Accommodation per night: receipt amount divided by nights (null means 1).
Tier comes from the receipt's city (Trip Food and Accommodation only). Office Food and all other categories use flat limits.

Duplicates: form a group by the three matching rules. Earliest upload is the original. Each member is flagged and decided separately. Store dup_group_id.

5. Config (no policy values in code)
policy.json: limits, banned item list, required fields, thresholds. Built from POLICY.md.
cities.json: city to tier, with aliases (Bombay to Mumbai, Bangalore to Bengaluru, Gurgaon to Gurugram). Optional postal-code fallback is stretch.
categories.json: five categories; sample-data mapping Furniture and Technology to Equipment, Office Supplies to Miscellaneous.
Settings table: BASE_CURRENCY (INR), HELPLINE_NUMBER, CONF_THRESHOLD (0.7), BANNED_THRESHOLD (0.7). FX table: INR 1, MYR 23.4, USD 96.5, set date 2026-10-17. Both thresholds and the rates are admin-editable.

Example policy.json shape:

json
{
 "upload_window_days": 15,
 "limits": {
  "food_trip": {"per_person_meal": {"A":800,"B":600,"C":500}, "per_person_day": {"A":2000,"B":1500,"C":1200}},
  "food_office": {"per_person_meal": 400, "per_person_day": 800},
  "accommodation": {"per_night": {"A":7000,"B":4500,"C":3000}},
  "travel": {"per_receipt": 10000},
  "equipment": {"per_receipt": 10000},
  "misc": {"per_receipt": 2000, "per_day": 3000},
  "monthly": {"food":15000,"accommodation":40000,"travel":30000,"equipment":25000,"misc":5000}
 },
 "banned": ["alcohol","tobacco","fine","personal","gift","gambling"],
 "required": {"food":["attendees"],"client_related":["client_name"]}
}
6. Data model
users: id, name, email, password_hash, role (employee|admin), employee_code
receipts: id, user_id, file_path, file_hash, source_type (image|pdf), category_user, category_model, context, attendees, is_client_related, client_name, merchant, city, country, tier, issue_date, unique_id, currency, amount_original, fx_rate, amount_base, subtotal, tax, discount, shipping, nights, items_json, conf_json, extraction_json, status, flags_json, missing_fields_json, dup_group_id, uploaded_at, is_seeded
decisions: id, receipt_id, admin_id, decision (approved|denied), reason, created_at
queries: id, receipt_id, user_id, message, status (open|upheld|overturned), admin_id, admin_response, resolution_reason, created_at, resolved_at. Unique on receipt_id.
fx_rates: currency, rate_to_inr, set_date
settings: key, value
extraction_cache: file_hash (PK), model, json, created_at

Status values: info_needed, rejected, verified, flagged, approved, denied.

7. Extraction JSON contract

Groq takes images only. Images: resize, then send as base64. PDFs: extract the text layer with PyMuPDF and send the text; if there is none, render page 1 to an image. JSON mode, temperature 0. Validate with Pydantic. On invalid JSON retry once, then Info needed. Cache by file_hash. Mask card numbers (keep last 4) before storing text. Read the model ID from GROQ_MODEL. Check Groq's current vision model list and its image-count and size limits before choosing a default (at the time of writing, qwen/qwen3.8-27b: JSON mode, 3 images per request, 20 MB image-URL limit).

json
{
 "merchant": "string|null",
 "city": "string|null",
 "country": "string|null",
 "issue_date": "YYYY-MM-DD|null",
 "unique_id": "string|null",
 "currency": "INR|MYR|USD|other ISO|unknown",
 "total": 0.0,
 "subtotal": 0.0, "tax": 0.0, "discount": 0.0, "shipping": 0.0,
 "nights": null,
 "items": [{"name":"","qty":1,"amount":0.0,"banned_match":null,"banned_conf":0.0}],
 "items_readable": true,
 "model_category": "Food|Travel|Accommodation|Equipment|Miscellaneous",
 "alteration_notes": "string|null",
 "conf": {"issue_date":0.0,"total":0.0,"unique_id":0.0,"city":0.0,"items":0.0}
}

Rules for the extraction prompt: return only JSON; use null for anything not visible; never infer; banned_match is one of the banned list values or null; if the currency symbol is ambiguous (for example $ on a Malaysian receipt), set unknown and fill country. Code resolves unknown from country (Malaysia to MYR, United States to USD); if still unresolved, Info needed.

City: the model returns city text only. Code maps it to a tier via cities.json. Never ask the model for a tier.

8. Seed and planted data

Dataset layout (read by the seed script from disk, never pasted into chat): data/receipts/{train,val,test}/images/*.jpg with annotations/*.json (Tesseract OCR). data/invoices/*.pdf. Ignore Data Mapping.png. Keep sample_data/ small (10 photos, 3 PDFs, 1 blank) for tuning extraction.

Accounts: 1 admin, 4 employees named from invoice customer names. Demo passwords documented in README.
Photos: about 80 with issue dates in March 2018. Use the OCR annotation text only to pre-select by date; never as extraction input. Extraction is by the model.
PDFs: about 40 smallest non-blank invoices (parse the total from the text layer), a few above the Equipment limit, and 3 blank invoices.
Run extraction once, store in extraction_cache, commit the cache so the demo runs offline.
Make about 10 percent of employee-chosen categories wrong on purpose. Generate context, attendee counts, client names.
Generate synthetic receipts (Pillow) for the planted cases.
Planted case	Expected result
Receipt with alcohol line	rejected, NON_REIMBURSABLE
Live upload dated 20 days before upload (not seeded)	rejected, LATE_UPLOAD
Hotel receipt, Trip, Tier A, within limit	verified
Same hotel receipt with Office context	flagged, ACCOMMODATION_OFFICE
Travel receipt under limit	verified
Food receipt over the per-person limit	flagged, LIMIT_RECEIPT
One employee, 4 Food receipts of 4,000 INR (5 attendees) on different days	4th flagged, LIMIT_MONTH
Invoice PDF also uploaded as a photo	both flagged, DUPLICATE, one group
Wrong category	flagged, CATEGORY_MISMATCH
Items that do not sum to the total	flagged, MATH_MISMATCH
Receipt with no address	flagged, CITY_UNCLEAR
Blank invoice	info_needed

At the end the seed script prints each planted case with its actual status.

9. Tests
pytest per rule: just under, exactly at, and just over each limit.
Ledger test: out-of-order issue dates, and excluded statuses.
Duplicate test across image and PDF.
Currency test: MYR and USD conversion, stored rate.
Role test: employee gets 403 on admin routes.
Extraction check: invoice PDFs have a text layer, so compare model output with parsed text for invoice number, date, total, and city. Hand-label 30 to 40 receipt photos (date, total, merchant) and report accuracy.
10. Build phases (stop after each for review; commit; update PROGRESS.md)
Phase	Work	Owner	Done when
P1	Schema, config loader, policy.json from POLICY.md	Backend	tables created, configs load
P2	Extraction service and cache	Backend	10 photos and 3 PDFs parse; report results
P3	Rule engine, ledger, duplicates, currency, tests	Backend	all pytest green
P4	Auth and employee pages	Frontend	upload shows status and reasons
P5	Admin pages	Frontend	queue, detail, decision, Rejected & Queries, settings work
P6	Seed script	Backend	planted-case table matches
P7	README, ASSUMPTIONS, known limitations	Both	runs from a clean clone

Cut line: if P1 to P5 are not working end to end by about hour 5.5, drop every stretch item. Never drop upload, extraction, rules, admin decision with reason, or role security.

11. Assumptions already made (log others in the README)
Overturned rejection becomes approved.
Accommodation limit is per night; nights null means 1.
Per-person daily Food limit sums each receipt's per-person amount for that employee and date.
FX rates are static, set date shown on each converted receipt.
International cities use the same tier limits as Indian cities of the same tier.