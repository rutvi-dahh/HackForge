Expense Reimbursement Policy (v3)

Company policy for the Receipt Graveyard prototype. Self-created for this project. Every number below is complete and configurable; the build turns this file into policy.json. Items marked [TBD] are undecided and must not be invented.

1. Purpose and scope

Defines which business expenses are reimbursable, what information an employee must give, and how receipts are checked, flagged, rejected, or approved. The system recommends and explains. The admin owns every approval or denial.

2. Definitions
Issue date: the date printed on the receipt or invoice.
Unique ID: the receipt, invoice, or bill number printed on the document.
Base currency: INR. The admin can change it.
Context: Trip or Office, chosen by the employee for every receipt.
3. Categories
Category	Typical eligible examples
Food	Business meals, client meals, meals during official travel
Travel	Flights, trains, taxis, fuel, tolls, parking for business
Accommodation	Hotel or lodging during official travel
Equipment	Work-related tools, devices, furniture, supplies
Education	Courses, certifications, exam fees, work-related books and training
Miscellaneous	Other business expenses

The employee selects one category per receipt. The system verifies it against the items.

4. Submission rules
A receipt must be uploaded within 15 days of its issue date. Day 15 is valid. Later uploads are rejected automatically. The test dataset is exempt.
Accepted formats: JPG, PNG, PDF.
The same receipt must not be submitted twice, in any format or channel.
The document must be a genuine receipt or invoice for a payment the employee made.
5. Required information

From the receipt (extracted; if unreadable, the employee is asked, nothing is guessed): issue date, unique ID, total amount and currency. Merchant, city, and items are also extracted. If the item list is unreadable, or only a total is shown, the receipt is flagged Items unclear instead of rejected.

From the employee:

Category (always)
Amount claimed: the total printed on the receipt, in the receipt's currency (always)
Invoice number (optional)
Context: Trip or Office (always)
Attendee count, a whole number of at least 1 (every Food receipt)
Client name (only when the expense is marked client-related)
5.1 Verification of employee entries

Each entry is checked against the receipt by independent readings: the employee's entry, the model's reading, and a second reading (OCR on photos, the text layer on PDFs).

Amount and invoice number: a value that at least two readings agree on is accepted. If the employee's entry is the odd one out, the receipt is flagged. If no two readings agree, it is flagged as uncertain and all values are shown. A reading that returns nothing is ignored.
Category: the employee's choice is compared with the model's reading and a keyword reading. It is flagged only when it disagrees with the majority of the other two.
The reimbursable amount is always the amount on the receipt, never the amount typed.
If neither the model nor the second reading can find a value, the employee is asked to type it. That value is used provisionally and the receipt is flagged Extraction uncertain, so the admin checks it against the receipt image.
A mismatch flags the receipt for admin review and never rejects it.
6. Non-reimbursable items

A receipt containing any of these is rejected immediately, as a whole, naming the item. A mixed receipt is not split. Items the system cannot classify with confidence are flagged, not rejected.

Alcohol
Tobacco and related products
Fines, penalties, and late fees
Personal-use items (clothing, personal care, personal entertainment)
Gifts for personal purposes
Gambling
Any item with no business purpose [applied only when the classifier is confident; otherwise flagged]
7. Currency
Amounts are reported in the base currency, INR by default. The admin can change it to any supported currency.
Every currency offered by the rate source (about 160 ISO 4217 currencies) is supported on receipts. Rates come from a free daily exchange-rate source (open.er-api.com), are cached, and are refreshed on startup when older than 24 hours or when the admin presses Refresh. The page showing rates carries the source's required attribution link, "Rates By Exchange Rate API".
Store the original amount, original currency, rate used, rate date, and converted amount on every receipt. Round to two decimals.
If the rate source is unavailable, the last stored rates are used. A first-run fallback ships with the app (1 MYR = 23.4 INR, 1 USD = 96.5 INR, as of 2026-10-17).
If a currency symbol is ambiguous (for example $ on a Malaysian receipt), infer the currency from the merchant's country. If that fails, ask the employee.
A receipt in a currency the rate source does not support is marked Info needed.
8. City tiers

City tiers are not a hardcoded list. The extractor returns the city as text. The first time a city is seen, the model classifies it using the rubric below, and the result is stored so the same city always gets the same tier afterwards. The admin can override any stored tier. If classification fails, the city is missing, or classifier confidence is below 0.6, the receipt uses Tier C limits and is flagged City unclear.

Tier	Rubric
A	Major metropolitan and high-cost business hubs, in India or abroad (for example Mumbai, Delhi NCR, Bengaluru, Singapore, London, New York)
B	Other large or regional business cities and state or provincial capitals (for example Jaipur, Lucknow, Kochi, Los Angeles, Johor Bahru)
C	Every other city, town, or unknown location

The examples are guidance given to the classifier, not a lookup. International cities use the same limits as Indian cities of the same tier.

9. Spending limits (INR)

Exceeding any limit flags the receipt for the admin and never rejects it. The flag shows the arithmetic.

City-based limits (Trip context)

Limit	Tier A	Tier B	Tier C
Food, per person per meal	800	600	500
Food, per person per day	2,000	1,500	1,200
Accommodation, per night	7,000	4,500	3,000

Other limits

Limit	Amount
Food in Office context, per person per meal	400
Food in Office context, per person per day	800
Travel, per receipt	10,000
Equipment, per receipt	10,000
Miscellaneous, per receipt	2,000
Miscellaneous, per day	3,000
Education, per receipt	25,000

Monthly limits (cumulative, per employee)

Category	Monthly total
Food	15,000
Accommodation	40,000
Travel	30,000
Equipment	25,000
Miscellaneous	5,000
Education	50,000

How limits apply

Per person: receipt amount divided by attendee count.
Per day: the employee's receipts in that category with the same issue date.
Per month: the employee's receipts in that category within the same calendar month of the issue date. Count approved and pending (verified, flagged) receipts. Exclude rejected, denied, and info-needed ones.
Order: evaluate by issue date. The receipt that pushes a total over is flagged. Earlier decided receipts are not re-flagged.
Example reason: "Food this month: 12,000 already claimed + 4,000 = 16,000, limit 15,000."
A daily limit uses the city tier of the receipt being checked.
Accommodation per night is the receipt amount divided by nights; if nights are not stated, 1 night is assumed and the flag says so.
Accommodation with Office context is flagged.
10. Validation outcomes
Check	Result
Issue date, total, or unique ID unreadable or missing	Info needed
Required employee input missing	Info needed
Submitted more than 15 days after issue date (live uploads)	Rejected
Contains a non-reimbursable item	Rejected
Item list unreadable or only a total shown	Flagged: Items unclear
Items, discount, shipping, and tax do not match the total beyond rounding	Flagged
Employee category disagrees with the majority of the model and keyword readings	Flagged
Employee's amount or invoice number differs from what two readings agree on	Flagged: Amount mismatch or Invoice ID mismatch
No two readings of the amount or invoice number agree	Flagged: Extraction uncertain
No reader could find a value and the employee typed it	Flagged: Extraction uncertain (typed value used provisionally)
Accommodation with Office context	Flagged
Any limit in Section 9 exceeded	Flagged, math shown
City not found	Flagged: City unclear, Tier C limits
Later copy of an earlier upload (Section 11)	Flagged, grouped. The earliest upload is not flagged for this.
Suspicious indicator (Section 12)	Flagged
All checks pass	Verified

Rounding tolerance for the math check: 1 INR or 1 percent of the total, whichever is larger.

11. Duplicates

A receipt is a possible duplicate if any holds:

Same unique ID as an earlier submission.
Same merchant, issue date, and amount as an earlier submission, even in a different format (photo versus PDF).
Byte-identical file.

Duplicates are grouped together in the admin's queue. The earliest upload is the original: it is checked normally and is Verified if nothing else fires. Every later copy is Flagged as a duplicate, even if the original was rejected or denied. Each receipt in a group still gets its own decision.

12. Suspicious indicators

These flag, never reject.

Issue date in the future
A perfectly round total (a multiple of 100 in INR) with no item that explains it
Visible signs of alteration or inconsistent formatting noted by the reader
13. Decision statuses
Status	Meaning	Who acts
Info needed	Required data missing	Employee completes it on the same record
Rejected	Hard rule violated	Final. One query allowed.
Verified	All checks passed	Admin approves or denies
Flagged	One or more review reasons	Admin approves or denies
Approved / Denied	Admin decision	Admin. A written reason is required only for Denied.
14. Rejections, queries, and help
A rejected receipt cannot be resubmitted.
The employee may raise one query per rejected receipt: one message, no thread.
The admin reviews each query personally, gives one response, and records uphold (a written reason is required) or overturn (no reason needed). An overturned receipt becomes Approved.
Rejected receipts and queries appear in a separate admin tab.
A Help page, linked at the bottom of every user page, shows the helpline number [TBD] and a user manual with the steps to claim reimbursement on the website. [Stretch] An AI chatbot answering from that manual only.
15. Admin responsibilities
A denial, and an upheld rejection, needs a written reason. Approvals and overturns need none. Every decision records the admin and a timestamp.
The admin sees all flag reasons beside the receipt, and opens the receipt in a popup on the same page.
Views: by category (default) or by employee. Within each view, receipts are ordered by issue date, newest first.
The admin sets the base currency and can refresh the exchange rates.
16. Data handling
Mask card numbers (keep the last 4 digits) in stored and displayed text.
Keep the original model extraction alongside any employee correction.
[TBD] Retention period.
17. Exclusions and open items
No approval threshold. Every verified or flagged receipt goes to the admin regardless of amount.
Partial reimbursement of mixed receipts is out of scope.
[TBD] Helpline number to display.
Education limits (25,000 per receipt, 50,000 per month) are suggestions pending confirmation.
[TBD] Payout process, outside this system.
18. Assumptions
The 15-day rule is a hard rejection for live uploads, and seeded data is exempt.
Attendee count is required on every Food receipt; client name only when marked client-related.
A mixed receipt is rejected as a whole.
One open query per receipt; an overturned rejection becomes Approved.
Exceeding a limit flags and never rejects.
Exchange rates come from a free daily source and are cached. If the source is unavailable, the last stored rates are used.
International cities use the tier limits of the same tier.
Sample data mapping: Furniture and Technology invoices map to Equipment; Office Supplies map to Miscellaneous.
The limit values in Section 9 are suggested starting points, not benchmarked figures.
The reimbursable amount is always the receipt's value, never the typed one, except when no reader finds a value (then the typed value is provisional and flagged).
Invoice number entry is optional. If blank, only the model and the second reading are compared.
City tiers are classified once by the model, then cached. The admin can override any stored tier.