# Receipt Graveyard - Build Progress

| Phase | Description | Status | Notes |
|-------|-------------|--------|-------|
| **P1** | Schema, config loader, policy.json from POLICY.md | **DONE** | SQLite tables created (`users`, `receipts`, `decisions`, `queries`, `fx_rates`, `settings`, `extraction_cache`), configs load (`policy.json`, `cities.json`, `categories.json`), tested successfully. |
| **P2** | Extraction service & offline cache | **DONE** | Groq vision/text pipeline with PyMuPDF text layer extraction, image resizing, Pydantic validation, card number masking, offline cache by file_hash. Tested on 10 photos & 3 PDFs. |
| **P3** | Rule engine, ledger, duplicates, currency, tests | **DONE** | Complete policy decision logic: auto-rejections, missing info detection, flags, limits math, duplicate clustering. 16 pytest tests passing green including planted oracle tests. |
| **P4** | Auth & employee pages | **DONE** | Login, upload with instant feedback, claims history, missing fields resolver for info_needed claims, single query submission, help & policy pages. |
| **P5** | Admin pages | **DONE** | Review queue (category/employee views, duplicate clusters), receipt detail & flags panel, mandatory written decision reason, Rejected & Queries tab with uphold/overturn, settings, CSV export. |
| **P6** | Seed script | In Progress | Seed 1 admin + 4 employees, run pipeline on sample receipts/invoices, plant test cases matching manifest.csv. |
| **P7** | README & Polish | Pending | Run steps, assumptions, honest accuracy metrics & limitations. |
