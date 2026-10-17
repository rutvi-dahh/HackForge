# Receipt Graveyard - Build Progress

| Phase | Description | Status | Notes |
|-------|-------------|--------|-------|
| **P1** | Schema, config loader, policy.json from POLICY.md | **DONE** | SQLite tables created (`users`, `receipts`, `decisions`, `queries`, `fx_rates`, `settings`, `extraction_cache`), configs load (`policy.json`, `cities.json`, `categories.json`), tested successfully. |
| **P2** | Extraction service & offline cache | **DONE** | Groq vision/text pipeline with PyMuPDF text layer extraction, image resizing, Pydantic validation, card number masking, offline cache by file_hash. Tested on 10 photos & 3 PDFs. |
| **P3** | Rule engine, ledger, duplicates, currency, tests | **DONE** | Complete policy decision logic: auto-rejections, missing info detection, flags, limits math, duplicate clustering. 16 pytest tests passing green including planted oracle tests. |
| **P4** | Auth & employee pages | **DONE** | Login, upload with instant feedback, claims history, missing fields resolver for info_needed claims, single query submission, help & policy pages. |
| **P5** | Admin pages | **DONE** | Review queue (category/employee views, duplicate clusters), receipt detail & flags panel, mandatory written decision reason, Rejected & Queries tab with uphold/overturn, settings, CSV export. |
| **P6** | Seed script | **DONE** | 17 receipts seeded across 5 users; all 13 planted oracle cases match expected outcomes. |
| **P7** | README & Polish | **DONE** | Setup instructions, ASSUMPTIONS section, honest limitations. |
| **Fix** | Schema column mismatch `total` | **DONE** | `app.py:329` UPDATE used non-existent `total` column; removed. Schema integrity check added to `init_db`. 27 tests passing (6 new smoke-route tests cover every route incl. `/receipts/<id>/complete_info`). |
