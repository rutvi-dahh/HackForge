SPEC: Receipt Graveyard

Source of truth for the build. Policy values live in docs/POLICY.md. If this file conflicts with AGENTS.md or an older prompt, this file wins. If something is unspecified, choose the simplest option and log it in the README under ASSUMPTIONS.

1. Overview

Two-role web app. Employees upload receipts (JPG, PNG, PDF) and get instant policy feedback. An admin reviews a pre-checked queue, grouped by category or employee. A written reason is required only when the admin denies or upholds a rejection.

Principle: AI reads, code decides. The vision model only extracts fields and tags items. Python rules decide every status. Nothing is guessed: unreadable or low-confidence data becomes Info needed or a flag. Extraction accuracy on new, unseen receipts is the main technical risk, so the pipeline in section 7 uses two independent readers, a second model pass and a measurable eval script.

Stack: Flask, SQLite (SQLAlchemy or plain sqlite3), Jinja templates, plain CSS, small vanilla JS for popups only, no JS build step, PyMuPDF, Groq SDK, OpenCV (opencv-python-headless), pytesseract plus the Tesseract program (brew install tesseract on macOS), pytest. Base currency INR. Runs locally with python app.py. Do not target Vercel with SQLite (no persistent disk); Render or Railway only at the end.

Env vars: GROQ_API_KEY, GROQ_MODEL (vision), GROQ_MODEL_FALLBACK (optional second vision model), GROQ_TEXT_MODEL (city tiers), SECRET_KEY.

Data: self-created and public sample data, not organizer-provided. Receipt photos are Malaysian (MYR, 2016 to 2018). PDF invoices are US (USD, 2012 to 2013, sample "Superstore" data). Mark every seeded or planted row is_seeded=1.

2. Scope

MVP: login (2 roles), upload with category, claimed amount, invoice number, context, attendees, client; extraction; self-verification (user vs model vs second reader); rule engine with statuses and flags; duplicate groups; per-receipt, per-day, per-month limits with city tiers classified by the model and cached; admin queue (category view, employee toggle), receipt popup viewer, flag panel, reason required only when denying; Rejected & Queries tab (one query per receipt, admin uphold or overturn); Help page; admin currency settings and live FX rates for all supported currencies.

Stretch, in order (only after MVP works end to end): bulk approve in a category; employee edits extracted fields (original kept); admin category override; CSV export; Help chatbot answering from the manual only.

Out of scope: email or WhatsApp ingestion, payments, SSO, FX at the historical receipt date, partial approval of mixed receipts, approval thresholds, model training, mobile app.

3. Pages and flows

Seeded accounts only: 1 admin, 4 employees. Hashed passwords. Role checked server-side on every admin route (employee gets 403). Employees see only their own receipts.

Employee
/login, /logout
/upload: file; category (Food, Travel, Accommodation, Equipment, Education, Miscellaneous); claimed amount (required number, as printed on the receipt, in the receipt's currency); invoice number (optional); context (Trip or Office); attendee count (int >= 1, required for Food); "client-related" checkbox, which makes client name required. Submit runs the pipeline and shows the result page: status plus every reason in plain English.
/receipts: own receipts with status and reasons, ordered by issue date, newest first. Info needed rows have a form for exactly the missing fields only (date, total, unique ID, currency, items, attendees, client). Saving re-runs the rules on the same record.
/receipts/<id>: detail. If rejected and no query exists: one query form (single message). Shows the admin's response once given.
/help: helpline number (config HELPLINE_NUMBER, placeholder until supplied) and a static user manual with the claim steps. Linked in the footer of every user page.
Admin
/admin: queue of status verified and flagged only. Default view groups by category. ?view=employee groups by employee. Within every group, rows are ordered by issue date, newest first (null dates last; ties by upload time, newest first). Duplicate groups render together at their original's position, original first, labeled "original". Every row has a "Review" button; all Review buttons share one CSS class .btn-review with fixed width (120px) and height (36px). Row columns: employee name and code, issue date, amount in base currency, category, status, flag count.
/admin/receipt/<id>: employee name and code, issue date, original and base amount, user category and model category, claimed vs accepted amount, context, attendees, client, city and tier, items. A "View receipt" button opens the file in a popup (modal <dialog>, image or PDF embed), never a new tab. Files are served through an authorized route. Side panel lists every flag (code, policy clause, message). Approve needs no reason. Deny opens a popup asking for the reason of rejection; it cannot be submitted empty.
/admin/rejected: auto-rejected receipts and their queries. Per query: one response, then Overturn (no reason needed; receipt becomes approved) or Uphold (opens a popup; reason required).
/admin/settings: base currency (any supported currency), a "Refresh rates" button, a read-only list of all currencies with rate and fetched time, the attribution link "Rates By Exchange Rate API", city-tier overrides, and the editable thresholds.

Rejections are final: no resubmission (the same file is caught as a duplicate). One open query per receipt, one message, one reply, no thread.

4. Decision logic

Run after extraction and again after the employee completes missing info. Stop at the first auto-reject; otherwise collect all flags.

Reject (rejected, final):

NON_REIMBURSABLE: any item with banned_match set and banned_conf at or above the banned threshold. A mixed receipt is rejected whole.
LATE_UPLOAD: upload date minus issue date greater than 15 days. Day 15 is valid. Skipped for is_seeded.

Info needed (info_needed): issue date, total, or unique ID missing or below the confidence threshold; currency unresolved; claimed amount missing; Food without attendee count; client-related without client name. Items unreadable is a flag (below), not Info needed.

Flags (status flagged): collect all that apply.

Code	Trigger
ITEMS_UNCLEAR	items_readable false, or only a total shown
MATH_MISMATCH	items, minus discount, plus shipping and tax, differ from total by more than rounding tolerance (INR 1 or 1 percent, whichever is larger)
CATEGORY_MISMATCH	user category differs from the majority of model_category and the keyword read (see Self-verification)
AMOUNT_MISMATCH	user's claimed amount is the odd one out against two agreeing readings
INVOICE_ID_MISMATCH	user's invoice number is the odd one out against two agreeing readings
EXTRACTION_UNCERTAIN	no two readings of the amount or invoice number agree (store all values), or a value was supplied only by the employee because no reader found it
ACCOMMODATION_OFFICE	Accommodation with Office context
LIMIT_RECEIPT, LIMIT_DAY, LIMIT_MONTH	a limit in POLICY.md exceeded
CITY_UNCLEAR	city missing, classifier failed, or classifier confidence below CITY_CONF_THRESHOLD; Tier C limits applied
DUPLICATE	later upload matches an earlier one on unique ID, or merchant + issue date + amount (across image and PDF), or file hash. The earliest upload is not flagged for this, and later copies are still flagged even if the original was rejected or denied.
SUSPICIOUS_FUTURE_DATE, SUSPICIOUS_ROUND_TOTAL, SUSPICIOUS_ALTERATION	issue date after today; total a round multiple of 100 in base currency with no item that explains it; non-empty alteration_notes

Otherwise verified. Admin sets approved or denied on verified and flagged items. A reason is required only for denied.

Each flag stores code, clause (POLICY.md section), amount_affected, message. Limit messages show the math, e.g. "Food this month: 12,000 already claimed + 4,000 = 16,000, limit 15,000".

Self-verification (voting) (services/verify.py, runs after extraction and before limits)
Reader 2: PDFs use the PyMuPDF text layer. Photos use OpenCV preprocessing (grayscale, 2x upscale, denoise, adaptive threshold, deskew, try rotations 0/90/180/270) then pytesseract (psm 6). Returns amount and invoice number, or None.
Parsing: amount from lines with total, amount due, grand total, balance due; handle RM, $, and comma decimals. Invoice number from text after invoice, inv, receipt no, bill no, order id; normalize (uppercase, strip punctuation, treat O/0 and I/1 as equal).
Comparison: amounts in the receipt's currency within max(1 currency unit, 1 percent); invoice numbers by equality or edit distance of 1 or less.
Vote over (user, model, reader 2) for amount and invoice number. A value with at least 2 agreeing readings is accepted. The reimbursable amount is always the accepted receipt value, never the typed one.
User is the odd one out: flag AMOUNT_MISMATCH or INVOICE_ID_MISMATCH. No majority: flag EXTRACTION_UNCERTAIN. Reader 2 returns None: ignore it and never flag for that. If the invoice number was left blank, skip the user's vote and compare model against reader 2 only.
If neither the model nor reader 2 finds an amount (or ID), use the employee's typed value as provisional (amount_source=user) and flag EXTRACTION_UNCERTAIN. The admin checks it in the receipt popup.
Category vote over (user, model_category, keyword read from categories.json). Flag CATEGORY_MISMATCH only when the user's choice differs from the majority of the other two.
Flag messages show every value, e.g. "Typed 5,000; receipt shows 500.00". Mask card numbers in any stored OCR text.
Limit evaluation
Amounts are in base currency (INR) after conversion.
Per-person Food amount = receipt amount / attendee count.
Per receipt: compare to the receipt limit. Food: per-person amount vs per-person-per-meal limit.
Per day (same employee, same issue date, same category): Food sums per-person amounts; Miscellaneous sums amounts.
Per month (same employee, same category, same calendar month of issue date): sum of full amounts.
Counted receipts: approved, verified, flagged. Excluded: rejected, denied, info_needed.
Process receipts by issue date. The receipt that pushes a total over is flagged. Do not re-flag earlier decided receipts.
Accommodation per night: receipt amount divided by nights (null means 1, and the message says so).
Tier comes from the receipt's city (Trip Food and Accommodation only). Office Food and all other categories use flat limits.
Duplicates

Form a group by the three matching rules. The earliest upload (by upload time) is the original: it goes through the normal rules and becomes verified if nothing else fires. Every later upload in the group is flagged DUPLICATE. Each member is still decided separately. Store dup_group_id. Stretch shortcut: "approve original, deny the rest" (one deny-reason popup).

5. Config (no policy values in code)
policy.json: limits, banned item list, required fields, thresholds. Built from POLICY.md; if a value is missing there, stop and ask.
City tiers are not a hardcoded list and there is no cities.json. Table city_tiers caches model-classified tiers. On a cache miss, call Groq (GROQ_TEXT_MODEL, temperature 0, JSON mode) once with the rubric from POLICY.md section 8 and the city plus country; it returns {"canonical_city", "tier": "A|B|C", "confidence"}; store with source=model. The same city always returns the stored tier afterwards. Admin can override (source=admin). On API failure, missing city, or confidence below CITY_CONF_THRESHOLD: Tier C and CITY_UNCLEAR, and do not cache.
categories.json: six categories; sample-data mapping Furniture and Technology to Equipment, Office Supplies to Miscellaneous; keyword map for the category vote (restaurant, cafe, food to Food; hotel, lodge to Accommodation; petrol, airline, taxi, toll to Travel; electronics, furniture, hardware to Equipment; course, exam, certification, training, book to Education).
Settings table: BASE_CURRENCY (INR), HELPLINE_NUMBER, CONF_THRESHOLD (0.7), BANNED_THRESHOLD (0.7), CITY_CONF_THRESHOLD (0.6). Thresholds are admin-editable.
FX: live rates from https://open.er-api.com/v6/latest/{BASE} (no key, about 160 currencies, updated daily, caching permitted, rate-limited; verify it responds). Rates are units of X per 1 base; store the inverse as rate_to_base. Refresh on startup when older than 24 hours, plus the admin "Refresh rates" button. Each receipt stores original amount, currency, rate used, rate date and converted amount. Show the attribution link "Rates By Exchange Rate API" on the settings page. If the call fails, keep the last stored rates; on first run with no network, seed a fallback (1 MYR = 23.4 INR, 1 USD = 96.5 INR, as of 2026-10-17; confirm these when you build).

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
  "education": {"per_receipt": 25000},
  "monthly": {"food":15000,"accommodation":40000,"travel":30000,"equipment":25000,"misc":5000,"education":50000}
 },
 "banned": ["alcohol","tobacco","fine","personal","gift","gambling"],
 "required": {"food":["attendees"],"client_related":["client_name"]}
}
6. Data model
users: id, name, email, password_hash, role (employee|admin), employee_code
receipts: id, user_id, file_path, file_hash, source_type (image|pdf), category_user, category_model, claimed_amount, claimed_invoice_no, ocr_amount, ocr_invoice_no, ocr_text (masked), context, attendees, is_client_related, client_name, merchant, city, country, tier, issue_date, unique_id, currency, amount_original, accepted_amount, amount_source (model|ocr|user), fx_rate, fx_rate_date, amount_base, subtotal, tax, discount, shipping, nights, items_json, conf_json, extraction_json, status, flags_json, missing_fields_json, dup_group_id, uploaded_at, is_seeded
decisions: id, receipt_id, admin_id, decision (approved|denied), reason (required when denied, null when approved), created_at
queries: id, receipt_id, user_id, message, status (open|upheld|overturned), admin_id, admin_response, resolution_reason (required when upheld), created_at, resolved_at. Unique on receipt_id.
fx_rates: currency, rate_to_base, base, fetched_at, source
city_tiers: city_key (lowercase city + country, PK), canonical_city, country, tier (A|B|C), source (model|admin), created_at
settings: key, value
extraction_cache: file_hash (PK), model, json, created_at

Status values: info_needed, rejected, verified, flagged, approved, denied.

7. Extraction pipeline and JSON contract

Groq takes images only. Run in order, cache the final result by file_hash.

Ingest: validate type and size; fix EXIF rotation; compute file_hash.
Reader 2 (OCR), always runs. Photos: OpenCV preprocess (crop to the receipt, fix rotation, deskew, denoise, boost contrast), Tesseract for text. PDFs: PyMuPDF text layer; if none, render page 1 to an image and OCR it.
Pass A (Groq vision, GROQ_MODEL). Send the cleaned image (resized under Groq's size limit) and the OCR text as a hint labelled "may contain errors", base64. PDFs: send the text layer. JSON mode, temperature 0, validate with Pydantic.
Pass B, only if any required field (issue date, total, unique ID, currency, items) is null or below CONF_THRESHOLD. Re-run on the OpenCV-enhanced image (rotated per Tesseract OSD) with GROQ_MODEL_FALLBACK if set, else GROQ_MODEL. Merge per field, keeping the higher-confidence value.
OCR field fallback: if the model still returns null for date, total, or unique ID but the OCR text has a labeled match (Date, Total, Invoice No, Bill No), fill it from OCR and record source: ocr per field in extraction_json. These values still go through the vote. Log which source supplied each field so gaps can be measured.
Invalid JSON: retry once, then treat fields as null. Mask card numbers (keep last 4) before storing text.
Still missing required fields: Info needed, with the exact fields listed (section 3). Typed values are provisional (section 4 voting) and flagged EXTRACTION_UNCERTAIN.

Read the model ID from GROQ_MODEL. Before choosing a default, check Groq's current vision model list and its image-count and size limits (the previous draft named qwen/qwen3.8-27b with JSON mode, 3 images per request, 20 MB image-URL limit; that was not verified, so confirm it exists).

json
{
 "merchant": "string|null",
 "city": "string|null",
 "country": "string|null",
 "issue_date": "YYYY-MM-DD|null",
 "unique_id": "string|null",
 "currency": "any ISO 4217 code|unknown",
 "total": 0.0,
 "subtotal": 0.0, "tax": 0.0, "discount": 0.0, "shipping": 0.0,
 "nights": null,
 "items": [{"name":"","qty":1,"amount":0.0,"banned_match":null,"banned_conf":0.0}],
 "items_readable": true,
 "model_category": "Food|Travel|Accommodation|Equipment|Education|Miscellaneous",
 "alteration_notes": "string|null",
 "conf": {"issue_date":0.0,"total":0.0,"unique_id":0.0,"city":0.0,"items":0.0}
}

Prompt rules: return only JSON; null for anything not visible; never infer; banned_match is one of the banned list values or null; if the currency symbol is ambiguous (for example $ on a Malaysian receipt), set unknown and fill country. Code resolves unknown from the country's official ISO 4217 currency; if still unresolved, Info needed. Dates: day-first by default; low confidence on an ambiguous format means Info needed.

City: extraction returns city text and country only. The tier comes from the cached classifier in section 5, never from the extraction call.

8. Seed and planted data

Dataset layout (read by the seed script from disk, never pasted into chat): data/receipts/{train,val,test}/images/.jpg with annotations/.json (Tesseract OCR). data/invoices/*.pdf. Ignore Data Mapping.png. Keep sample_data/ small (10 photos, 3 PDFs, 1 blank, plus planted/ with manifest.csv) for tuning extraction. For seeded rows, claimed_amount is set to the true printed total.

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
Invoice PDF also uploaded as a photo	original verified (if nothing else fires); later copy flagged, DUPLICATE, one group
Education receipt within limit	verified
Two uploads from a city never seen before	model classifies once, second reuses the cached tier
Wrong category	flagged, CATEGORY_MISMATCH
Items that do not sum to the total	flagged, MATH_MISMATCH
Receipt with no address	flagged, CITY_UNCLEAR
Blank invoice	info_needed

At the end the seed script prints each planted case with its actual status.

9. Tests
pytest per rule: just under, exactly at, and just over each limit.
Ledger: out-of-order issue dates, and excluded statuses.
Duplicates: across image and PDF; earliest upload is not flagged DUPLICATE, the later one is, even if the original was rejected.
Currency: MYR and USD conversion, stored rate. FX: refresh stores rates for all currencies; failure keeps the last rates; any supported ISO currency converts.
Role: employee gets 403 on admin routes.
Ordering: admin and employee lists are newest issue date first, null dates last.
Decisions: approve works with no reason; deny and uphold are rejected without a reason.
City tier: the model is called once per city; the second lookup hits the cache; API failure or low confidence gives Tier C and CITY_UNCLEAR.
Verification: wrong typed amount is flagged; reader 2 None causes no flag; two readers agreeing against the user is flagged; no majority gives EXTRACTION_UNCERTAIN; accepted amount equals the receipt value in every case; a single-reader category disagreement does not flag; when no reader finds an amount, the typed value is used and EXTRACTION_UNCERTAIN is set.
Extraction eval (scripts/eval_extraction.py): run the full pipeline on sample_data and compare with gold_labels.csv and planted/manifest.csv; print per-field accuracy (date, total, unique ID, currency, city, category) and list failures. Invoice PDFs have a text layer, so compare model output with parsed text for invoice number, date, total, and city. Hand-label 30 to 40 receipt photos (date, total, merchant) and report accuracy. Re-run after any extraction change.
10. Build phases (stop after each for review; commit; update PROGRESS.md)
Phase	Work	Owner	Done when
P1	Schema, config loader, policy.json from POLICY.md	Backend	tables created, configs load
P2	Extraction pipeline (OCR reader, pass A, pass B, cache) and eval script	Backend	10 photos and 3 PDFs parse; eval report
P3	Rule engine, ledger, duplicates, live FX, city tier classifier, self-verification, tests	Backend	all pytest green
P4	Auth and employee pages	Frontend	upload shows status and reasons
P5	Admin pages	Frontend	queue (newest first), receipt popup, deny-reason popup, Rejected & Queries, settings work
P6	Seed script	Backend	planted-case table matches
P7	README, ASSUMPTIONS, known limitations	Both	runs from a clean clone

Cut line: if P1 to P5 are not working end to end by about hour 5.5, drop every stretch item. Never drop upload, extraction, rules, admin decision, or role security.

11. Assumptions already made (log others in the README)
Overturned rejection becomes approved.
Accommodation limit is per night; nights null means 1.
Per-person daily Food limit sums each receipt's per-person amount for that employee and date.
FX rates are live (cached 24h); the rate used and its date are stored on each converted receipt.
International cities use the same tier limits as Indian cities of the same tier.
The reimbursable amount is always the receipt's value, never the typed one, except when no reader finds a value (then the typed value is provisional and flagged).
Invoice number entry is optional; if blank, only the model and reader 2 are compared.
Education limits (25,000 per receipt, 50,000 per month) are suggestions pending confirmation.
Approvals need no reason; the audit record still stores the admin and the time.
City tier is classified once by the model, then cached; confidence threshold 0.6.