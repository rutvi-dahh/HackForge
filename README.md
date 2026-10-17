# Receipt Graveyard 🪦🧾
> **Deterministic Expense Reimbursement with AI Vision**  
> *Built for HackForge Hackathon | Team of 2 | Built in ~8 Hours*

---

## 🌟 Executive Summary

**Receipt Graveyard** is an expense-reimbursement web application built with a strict architectural boundary:
> **The AI model only READS receipts into structured JSON and classifies items. Plain Python rules DECIDE.**

The AI model never approves, rejects, or flags expenses autonomously. It extracts raw facts with confidence scores. Deterministic Python rules evaluate every claim against an immutable corporate policy. Unreadable or low-confidence data gracefully transitions to **"Info needed"** rather than hallucinating numbers.

Every policy decision records:
- `rule_id` (e.g., `LIMIT_DAILY`, `NON_REIMBURSABLE_ITEM`, `DUPLICATE_ID`)
- Exact policy clause cited
- Specific amount affected
- Plain-English justification including transparent math (e.g., `Daily Food limit for Tier A is ₹2,000 for 1 attendee. Total today is ₹2,800 (exceeded by ₹800)`)

---

## 🛠️ Technology Stack

| Layer | Technology | Rationale |
|---|---|---|
| **Backend** | Python 3.9+ / Flask | Lightweight, deterministic execution, zero runtime bloat |
| **Database** | SQLite3 (`WAL` mode) | Zero-setup, atomic ACID transactions, foreign keys enforced |
| **Frontend** | Jinja2 Server-Rendered HTML + Vanilla CSS | Zero build step (`npm`/Webpack free), instant hot-reloading |
| **Aesthetics** | Dark Glassmorphism Design System | Inter font, glowing state badges, dynamic side panels, mobile-ready |
| **AI / OCR** | Groq API (`llama-3.2-11b-vision-preview`) | High-speed structured JSON extraction with image resize & Pydantic validation |
| **PDF Extraction** | PyMuPDF (`fitz`) | Direct text-layer extraction for digital invoices; auto-renders scanned PDFs to image |
| **Offline Cache** | SQLite `extraction_cache` (SHA-256) | Instant offline demonstrations of all planted test receipts without API keys |

---

## 🚀 Quick Start Guide

### 1. Prerequisites
- Python 3.9 or higher
- Git

### 2. Clone & Setup Virtual Environment
```bash
git clone https://github.com/rutvi-dahh/HackForge.git
cd HackForge

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Environment Configuration
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
*(Optional: Add your `GROQ_API_KEY` from [console.groq.com](https://console.groq.com/keys) if you wish to run live vision extraction on new, unseeded receipts. All 13 planted hackathon receipts are pre-cached and run 100% offline without any API key!)*

### 4. Seed the Database
Populate demo accounts, company policy, exchange rates, and run the rule engine on all 13 planted test receipts:
```bash
python seed.py
```

### 5. Launch the Server
```bash
python app.py
```
Open your browser and navigate to:
👉 **`http://127.0.0.1:5000`**

---

## 👥 Seeded Demo Accounts

On the login page, you can use the **1-Click Demo Login** switcher or enter the credentials below:

| Name | Role | Email | Password | Employee Code | Notes |
|---|---|---|---|---|---|
| **Finance Admin** | `admin` | `admin@company.com` | `admin123` | `ADM01` | Review queue, audit trail, overturns, currency settings, CSV export |
| **Alex Rivera** | `employee` | `e1@company.com` | `emp123` | `E1` | Has verified trips, duplicate clusters, & monthly food limit tests |
| **Bhavna Patel** | `employee` | `e2@company.com` | `emp123` | `E2` | Has hotel office context, wrong category, & USD invoice |
| **Claire Zhang** | `employee` | `e3@company.com` | `emp123` | `E3` | Has alcohol auto-rejection & over-limit meal |
| **David Kumar** | `employee` | `e4@company.com` | `emp123` | `E4` | Has math mismatch, missing date/total, & blank invoice |

---

## 📋 The 13 Planted Hackathon Test Cases

Every scenario mandated in the hackathon specification is planted and pre-verified:

| File | Employee | Category | Expected Outcome | Actual Outcome | Rule / Test Verified |
|---|---|---|---|---|---|
| `hotel_ok.png` | E1 | Accommodation | **Verified** | `VERIFIED` | Clean hotel stay within Tier A limit (₹4,200 < ₹6,000/night) |
| `hotel_office_context.png` | E2 | Accommodation | **Flagged** | `FLAGGED` | `HOTEL_OFFICE_CONTEXT`: Hotel claimed with "Office" context |
| `travel_cab.png` | E1 | Travel | **Verified** | `VERIFIED` | Clean local taxi invoice (₹480 <= ₹800 daily limit) |
| `alcohol_bar.png` | E3 | Food | **Rejected** | `REJECTED` | `NON_REIMBURSABLE_ITEM`: Beer detected above confidence threshold |
| `overlimit_meal.png` | E3 | Food | **Flagged** | `FLAGGED` | `LIMIT_DAILY`: Lunch of ₹2,800 exceeds Tier A daily food limit of ₹2,000 |
| `math_mismatch.png` | E4 | Miscellaneous | **Flagged** | `FLAGGED` | `SUM_MISMATCH`: Stated total ₹1,450 != Item sum ₹1,200 |
| `wrong_category.png` | E2 | Food | **Flagged** | `FLAGGED` | `CATEGORY_MISMATCH`: Employee selected "Food", invoice is for stationary |
| `missing_date_total.png` | E4 | Food | **Info needed** | `INFO_NEEDED` | `MISSING_FIELD`: Date and Total are blurred/low confidence |
| `dup_invoice.pdf` | E1 | Equipment | **Verified** | `VERIFIED` | Original monitor invoice (`INV-2024-001`, ₹14,500) |
| `dup_invoice_photo.png` | E1 | Equipment | **Flagged** | `FLAGGED` | `DUPLICATE_ID`: Photo of same invoice; flagged & clustered with original |
| `old_receipt.png` | E1 | Food | **Rejected** | `REJECTED` | `LATE_UPLOAD`: Uploaded > 15 days after issue date (live uploads) |
| `blank_invoice.pdf` | E4 | Equipment | **Info needed** | `INFO_NEEDED` | `MISSING_FIELD`: Empty page, 0 items readable |
| `usd_invoice.pdf` | E2 | Equipment | **Verified** | `VERIFIED` | Foreign currency: $250.00 converted at ₹83.00 = ₹20,750 |

---

## 📐 Rule Engine Pipeline & Execution Hierarchy

The rule engine follows a strict short-circuit evaluation order:

1. **Auto-Reject (Halts immediately)**:
   - Non-reimbursable banned items (`alcohol`, `tobacco`, `fine`, `personal_entertainment`) with classification confidence $\ge 0.70$.
   - Live receipts uploaded more than 15 calendar days after issue date (`LATE_UPLOAD`). Mixed receipts are rejected in whole.
2. **Info Needed (Halts immediately)**:
   - Missing or unreadable core fields (confidence $< 0.70$ on `date`, `total`, `unique_id`, `items_readable == False`).
   - Missing required attendee count ($< 1$) or missing client name for client-related entertainment.
3. **Policy Flags (Accumulates all non-fatal warnings)**:
   - **Duplicate Detection**: Clustered by identical file SHA-256 hash, matching invoice/unique ID, or matching `(merchant, date, amount)`. The earliest upload is marked as the cluster original.
   - **Limit Checks**: Per-receipt, daily, and monthly cumulative thresholds computed by receipt issue date against approved + pending ledger claims.
   - **Math Verification**: Stated total vs itemized sum difference $> 1.00$ unit.
   - **Context Integrity**: `Accommodation` claimed with `Office` context.
   - **Category Discrepancy**: Employee classification differs from AI vision classification.
   - **Suspicious Indicators**: Future dates, round totals (multiples of 100), or suspected tampering tags.
4. **Verified**:
   - Clean receipt with full confidence, compliant with all corporate limits.

---

## 🔒 Security & Role-Based Access Control

- **Server-Side Enforcement**: `@login_required` and `@admin_required` decorators verify session token and role on every route.
- **Isolated File Storage**: Uploaded receipt media are stored in a dedicated `uploads/` directory located *outside* the public web root.
- **Access Control on Media**: The `/uploads/<filename>` route verifies that the requester is either the receipt's owner or an active administrator before streaming bytes.
- **Cardholder Masking**: All credit/debit card numbers detected in raw text or extracted JSON are masked (`****-****-****-1234`) prior to database storage.
- **Password Protection**: Passwords hashed using `pbkdf2:sha256` with unique salts.
- **Immutable Audit Trail**: Every admin approval, rejection, or category override records the administrator's ID, timestamp, and mandatory written rationale.

---

## 🧪 Comprehensive Unit & Integration Tests

The test suite contains 21 unit and integration tests verifying all edge cases:

```bash
# Run the complete test suite
pytest tests/ -v
```

### Coverage Highlights:
- **`tests/test_rules.py`**: Just-under and just-over limit thresholds, 15-day late upload boundary at day 15 (allowed) vs day 16 (rejected), banned item confidence thresholding.
- **`tests/test_duplicates.py`**: Hash duplicates, unique ID matches, merchant+date+amount matches, and grouping into clusters with original identification.
- **`tests/test_ledger.py`**: Cumulative monthly limit math (verifying ₹12,000 passes and 4th receipt of ₹4,000 pushes to ₹16,000 > ₹15,000 limit, triggering `LIMIT_MONTH`).
- **`tests/test_currency.py`**: FX rate conversions, base currency recalculations, and rate overrides.
- **`tests/test_auth.py`**: Password hashing, session control, employee vs admin privilege boundaries.
- **`tests/test_flow.py`**: Complete lifecycle from employee upload, rule evaluation, single-query submission, to admin review and decision logging.
- **`tests/test_planted_oracle.py`**: Validates all 13 planted hackathon artifacts against their exact expected status.

---

## 📌 ASSUMPTIONS

As instructed by the hackathon specification, all implementation assumptions and defaults are listed below:

1. **Base Currency**: The system base currency defaults to Indian Rupee (`INR`) per the specification. Administrators can change the base currency (e.g., to `USD` or `MYR`) on the Admin Settings page.
2. **Static FX Rates**: In accordance with the "no real FX API" constraint, exchange rates are stored in a database table (`USD=83.0`, `MYR=18.5`) and can be updated by administrators. The rate and conversion date are stamped permanently on each converted receipt.
3. **15-Day Late Upload Window**: Calculated strictly as `(upload_date - issue_date).days > 15`. Seeded demo receipts are flagged with `is_seeded=1` and exempted so historic demo data remains intact, except for `old_receipt.png` which is seeded with `is_seeded=0` specifically to test this rule.
4. **Confidence Threshold**: Set to `0.70` (configurable in `config/policy.json`). Any core field extracted with confidence below `0.70` automatically classifies the claim as `INFO_NEEDED`.
5. **Attendee-Based Food Limits**: Daily and per-receipt food limits scale linearly with the number of reported attendees: `effective_limit = tier_limit * attendees`.
6. **Round Total Flag**: Totals that are exact integers divisible by 100 in base currency without itemized penny variance trigger `SUSPICIOUS_ROUND_TOTAL` as a soft flag for admin review, but never auto-reject.
7. **Single Query Policy**: Employees can submit exactly one query per rejected receipt. The administrator's response either upholds the rejection or overturns it to `VERIFIED`.
8. **Duplicate Grouping**: Earliest uploaded receipt with matching parameters is marked as the cluster original; subsequent uploads are flagged as `DUPLICATE_FILE`, `DUPLICATE_ID`, or `DUPLICATE_TRIPLET`.
9. **PIN / Postal Code Fallback**: Cities not recognized by name or alias are cross-referenced with `cities.json` PIN codes; otherwise mapped to `Tier C` with a `CITY_UNCLEAR` flag.

---

## ⚖️ Honest Limitations

1. **Offline Mode vs Live Extraction**: The offline cache stores deterministic extractions for the 13 planted files. If an un-cached image is uploaded without a valid `GROQ_API_KEY`, the application fails gracefully by marking the receipt as `INFO_NEEDED` with an explicit reason (`"Groq API key not configured or extraction failed"`).
2. **Scanned PDF Fallback**: Digital PDFs with embedded font layers are read directly via PyMuPDF. Scanned image PDFs without text layers are converted to 150 DPI PNGs in memory and processed by Groq Vision. Extremely degraded handwriting or water-damaged receipts may yield low item confidence (`items_readable: false`), correctly stopping at `INFO_NEEDED`.
3. **Static FX Rates**: Foreign currency conversion uses the static internal rates table rather than a live financial ticker.
4. **Scope Boundaries**: Per the prompt's out-of-scope directives, external payment disbursements, enterprise SSO / SAML, WhatsApp / email receipt forwarding, line-item partial approvals, and custom OCR model training are excluded.

---

## 👥 Hackathon Team & Credits
- **Team**: 2 Members (~8 hours build time)
- **Built for**: HackForge Hackathon
- **Repository**: [rutvi-dahh/HackForge](https://github.com/rutvi-dahh/HackForge)