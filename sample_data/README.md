# sample_data

Small test set for tuning extraction and testing rules. Do NOT put the full datasets here; the seed script reads those from disk.

## 1. planted/ (included, synthetic, all is_seeded=1)
13 files + manifest.csv. manifest.csv lists, per file: employee, user category, context, attendees, currency, expected outcome and what it tests. Use it as the unit/integration test oracle. Amounts are in the file's own currency; limits come from docs/POLICY.md.
Note: old_receipt.png only rejects on a LIVE upload (is_seeded=0); seeded rows are exempt from the 15-day rule.

## 2. real/ (you add these by hand, from your own datasets)
Copy exactly this mix:
- receipts_photos/ (Malaysian MYR photo set): 4 food/restaurant, 2 grocery/hardware, 2 stationery, 1 faded/low quality, 1 with a visible near-duplicate (pick two from one near-identical group). Total 10 images.
- invoices_pdf/ (Superstore USD set): 2 small orders (under $1,000), 1 large order (over $10,000), plus 1 of the blank templates. Total 4 PDFs.
Name files as found; add a line for each in real/notes.txt: file, what you expect (date, total, city), so you can check the model's extraction by eye.

## 3. gold_labels.csv (you fill in, 30 minutes)
For the 10 photos and 4 PDFs: file, true_date, true_total, true_merchant. Compare the Groq output against it in P2 to get an honest accuracy figure for the PPT.

## Seed employees (use in seed script)
E1..E4 = 4 employees (names from the Superstore customer names); admin = 1 account.
