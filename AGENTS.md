PROJECT: Receipt Graveyard, hackathon expense-claim app. 2 devs, ~8h.
DOCS: docs/SPEC.md is the source of truth; docs/POLICY.md holds policy values. If anything here conflicts with SPEC, SPEC wins. Unspecified = simplest option, logged under ASSUMPTIONS in README. Never invent policy values; if one is missing, stop and ask.

STACK: Flask, SQLite, Jinja, plain CSS, small vanilla JS for popups only. Python deps: pymupdf, opencv-python-headless, pytesseract (+Tesseract binary), pillow, requests, groq, pydantic, pytest. Runs locally: `python app.py`. Do NOT target Vercel with SQLite. Env: GROQ_API_KEY, GROQ_MODEL, GROQ_MODEL_FALLBACK, GROQ_TEXT_MODEL, SECRET_KEY (.env.example).

CORE RULES:
- AI reads, code decides. Models extract fields and classify; Python rules set every status.
- Never guess. Unreadable/low-confidence => Info needed or a flag, never a silent accept.
- Every flag stores code, policy clause, amount affected, plain message (show the math for limits).
- Policy values come from policy.json (built from POLICY.md). No hardcoded limits, city lists or categories in code.

KEY BEHAVIOURS (details in SPEC):
- Extraction (s7): OCR reader always runs; Groq pass A (image + OCR hint); pass B on enhanced image if a required field is missing/low-confidence; OCR field fallback; typed value used only if no reader finds one, flagged EXTRACTION_UNCERTAIN. Groq takes images only; PDFs use the text layer. Cache by file_hash. Mask card numbers. Verify the Groq model ID exists before using it.
- Voting (s4): user vs model vs OCR for amount, invoice no, category. Reimbursable amount = accepted receipt value, never the typed one.
- Rules (s4): reject (NON_REIMBURSABLE, LATE_UPLOAD >15 days, seeded exempt) > Info needed > flags > verified. Limits flag, never reject. No approval threshold.
- Duplicates (s4): earliest upload = original, evaluated normally; later copies flagged DUPLICATE.
- City tier (s5): cached Groq text lookup, no cities.json; fail/low confidence => Tier C + CITY_UNCLEAR.
- FX (s5): all currencies from open.er-api.com (verify it responds), 24h refresh, admin refresh, stored rate + date per receipt, attribution link on settings.
- Admin UI (s3): sort by issue date newest first (nulls last) in every group; one `.btn-review` class (120x36px); receipt opens in a modal, never a new tab; Approve = no reason; Deny and Uphold = modal with required reason.
- Roles: server-side checks on every admin route; employees see only their own receipts.

WORKFLOW (token discipline):
- Phases P1-P7 per SPEC s10, one per turn; stop after each. Commit each; update PROGRESS.md (max 5 lines: done / next / decisions / bugs).
- Edit files in place. Don't re-read files you just wrote. Don't paste whole files in chat. Replies under 100 words.
- Run pytest after each phase. After P2 run scripts/eval_extraction.py on sample_data and report per-field accuracy.
- Cut line: if P1-P5 aren't working by ~hour 5.5, drop all stretch items. Never drop upload, extraction, rules, admin decision, role security.
- Out of scope: email/WhatsApp, payments, SSO, partial approval, model training.