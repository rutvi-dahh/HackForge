# Receipt Graveyard - Build Progress

| Phase | Description | Status | Notes |
|-------|-------------|--------|-------|
| **P1** | Schema, config loader, policy.json from POLICY.md | **DONE** | SQLite tables created (`users`, `receipts`, `decisions`, `queries`, `fx_rates`, `settings`, `extraction_cache`), configs load (`policy.json`, `cities.json`, `categories.json`), tested successfully. |
| **P2** | Extraction service & offline cache | In Progress | Groq vision/text pipeline with PyMuPDF text layer extraction, image resizing, Pydantic validation, card number masking, offline cache by file_hash. |
| **P3** | Rule engine, ledger, duplicates, currency, tests | Pending | Auto-rejects, Info needed, Flags, limits math, duplicate clustering, pytest coverage. |
| **P4** | Auth & employee pages | Pending | Login, upload, instant feedback screen, receipt history & edit for info_needed, query raising, help & policy pages. |
| **P5** | Admin pages | Pending | Queue (category/employee views, duplicate clusters), receipt detail & flags panel, approve/deny with mandatory reason, Rejected & Queries tab, FX/settings, CSV export. |
| **P6** | Seed script | Pending | Seed 1 admin + 4 employees, run pipeline on sample receipts/invoices, plant test cases matching manifest.csv. |
| **P7** | README & Polish | Pending | Run steps, assumptions, honest accuracy metrics & limitations. |
