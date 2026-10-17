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
Context: Trip or Office (always)
Attendee count, a whole number of at least 1 (every Food receipt)
Client name (only when the expense is marked client-related)
6. Non-reimbursable items

A receipt containing any of these is rejected immediately, as a whole, naming the item. A mixed receipt is not split.

Alcohol
Tobacco and related products
Fines, penalties, and late fees
Personal-use items (clothing, personal care, personal entertainment)
Gifts for personal purposes
Gambling
Any item with no business purpose
7. Currency
Amounts are reported in INR.
Other currencies convert with a static, admin-editable rate table. Store the original amount, original currency, rate used, and converted amount. Round to two decimals.
Currency	Rate to INR	Set date
INR	1	2026-10-17
MYR	23.4	2026-10-17
USD	96.5	2026-10-17

Rates are mid-market figures from July and August 2026 sources. Confirm before the demo.

If a currency symbol is ambiguous (for example $ on a Malaysian receipt), infer the currency from the merchant's country. If that fails, ask the employee.
8. City tiers

The extractor returns the city as text. Code maps it to a tier through a lookup with aliases. Unknown cities use Tier C limits, are flagged City unclear, and are added to a review list so the table can grow from real data.

Tier	Indian cities	International cities
A	Mumbai, Delhi NCR (Delhi, Gurugram, Noida), Bengaluru, Chennai, Kolkata, Hyderabad, Pune, Ahmedabad	Singapore, Dubai, London, New York, San Francisco, Tokyo, Kuala Lumpur
B	Jaipur, Lucknow, Chandigarh, Kochi, Indore, Nagpur, Surat, Bhopal, Coimbatore, Visakhapatnam, Vadodara, Patna, Bhubaneswar, Guwahati, Thiruvananthapuram, Mysuru, Nashik, Ludhiana, Raipur, Ranchi, Dehradun	Los Angeles, Seattle, Chicago, Houston, Sydney, Bangkok, Johor Bahru, George Town
C	Every other location	Every other location
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

Monthly limits (cumulative, per employee)

Category	Monthly total
Food	15,000
Accommodation	40,000
Travel	30,000
Equipment	25,000
Miscellaneous	5,000

How limits apply

Per person: receipt amount divided by attendee count.
Per day: the employee's receipts in that category with the same issue date.
Per month: the employee's receipts in that category within the same calendar month of the issue date. Count approved and pending (verified, flagged) receipts. Exclude rejected, denied, and info-needed ones.
Order: evaluate by issue date. The receipt that pushes a total over is flagged. Earlier decided receipts are not re-flagged.
Example reason: "Food this month: 12,000 already claimed + 4,000 = 16,000, limit 15,000."
A daily limit uses the city tier of the receipt being checked.
Accommodation with Office context is flagged.
10. Validation outcomes
Check	Result
Issue date, total, or unique ID unreadable or missing	Info needed
Required employee input missing	Info needed
Submitted more than 15 days after issue date (live uploads)	Rejected
Contains a non-reimbursable item	Rejected
Item list unreadable or only a total shown	Flagged: Items unclear
Items, discount, shipping, and tax do not match the total beyond rounding	Flagged
Employee category differs from the model's reading	Flagged
Accommodation with Office context	Flagged
Any limit in Section 9 exceeded	Flagged, math shown
City not found	Flagged: City unclear, Tier C limits
Duplicate (Section 11)	Flagged, grouped
Suspicious indicator (Section 12)	Flagged
All checks pass	Verified

Rounding tolerance for the math check: 1 INR or 1 percent of the total, whichever is larger.

11. Duplicates

A receipt is a possible duplicate if any holds:

Same unique ID as an earlier submission.
Same merchant, issue date, and amount as an earlier submission, even in a different format (photo versus PDF).
Byte-identical file.

Duplicates are grouped in the admin's flagged section. The earliest upload is the original. Each receipt in a group still gets its own decision.

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
Approved / Denied	Admin decision	Admin, with a written reason
14. Rejections, queries, and help
A rejected receipt cannot be resubmitted.
The employee may raise one query per rejected receipt: one message, no thread.
The admin reviews each query personally, gives one response, and records uphold or overturn with a written reason. An overturned receipt becomes Approved.
Rejected receipts and queries appear in a separate admin tab.
A Help page, linked at the bottom of every user page, shows the helpline number [TBD] and a user manual with the steps to claim reimbursement on the website. [Stretch] An AI chatbot answering from that manual only.
15. Admin responsibilities
Every approval, denial, and query decision needs a written reason, recorded with the admin and a timestamp.
The admin sees all flag reasons beside the receipt.
Views: by category (default) or by employee.
The admin sets the base currency and rate table.
16. Data handling
Mask card numbers (keep the last 4 digits) in stored and displayed text.
Keep the original model extraction alongside any employee correction.
[TBD] Retention period.
17. Exclusions and open items
No approval threshold. Every verified or flagged receipt goes to the admin regardless of amount.
Partial reimbursement of mixed receipts is out of scope.
[TBD] Helpline number to display.
[TBD] Whether suburbs map to the nearest listed city.
[TBD] Payout process, outside this system.
18. Assumptions
The 15-day rule is a hard rejection for live uploads, and seeded data is exempt.
Attendee count is required on every Food receipt; client name only when marked client-related.
A mixed receipt is rejected as a whole.
One open query per receipt; an overturned rejection becomes Approved.
Exceeding a limit flags and never rejects.
Exchange rates are static and set in config.
International cities use the tier limits of the same tier.
Sample data mapping: Furniture and Technology invoices map to Equipment; Office Supplies map to Miscellaneous.
The limit values in Section 9 are suggested starting points, not benchmarked figures.