# YNAB-Based Credit Card Payment Tracker Plan

## 1. Project Goal

Create a locally hosted web platform that helps track and plan credit card payments.

The platform should:

- Connect to YNAB using the official YNAB API.
- Pull monthly income, credit card spending, payments, and balances.
- Calculate payment coverage for each credit card.
- Allow manual payment planning through a local dashboard.
- Run locally on the computer only.
- Avoid direct bank-login automation for the MVP.

The main idea is:

```text
YNAB → Local Sync Service → SQLite Database → Local Web Dashboard
```

---

## 2. Recommended Approach

Use **YNAB as the main financial data source** instead of making an AI agent log in to bank and credit card websites.

This is safer because:

- Bank usernames and passwords are not stored in the app.
- The system avoids MFA, CAPTCHA, and browser scraping issues.
- YNAB already connects to the credit cards and bank accounts.
- The YNAB API gives structured access to accounts, transactions, categories, and scheduled transactions.
- A YNAB API token can be revoked if needed.

Direct bank-login automation should be avoided unless there is an official API or secure integration method.

---

## 3. Main Features

### 3.1 Monthly Dashboard

The dashboard should show data for a selected month, from the **1st day to the last day of the month**.

It should display:

| Metric | Meaning |
|---|---|
| Monthly income / earned | Total inflows into selected cash accounts, excluding transfers |
| Credit card spending | Total purchases on each credit card |
| Credit card payments made | Payments already sent to credit cards |
| Available for payment | Amount YNAB says is set aside for each card payment |
| Current card balance | Current YNAB balance for each credit card |
| Planned payment | User-entered planned payment amount and date |
| Shortfall / surplus | Whether the planned payment is fully covered |

---

### 3.2 Credit Card Dashboard

For each credit card, show:

| Field | Description |
|---|---|
| Card name | Name of the credit card from YNAB |
| Current balance | Current card balance |
| Spending this month | Total purchases during selected month |
| Payments made this month | Total payments already made |
| Refunds / credits / cashback | Positive card transactions that are not transfers |
| Available for payment | YNAB Credit Card Payment category available amount |
| Planned payment | User-entered planned payment |
| Due date | Manual due date entry |
| Status | Covered, Short, Over-reserved, Paid, or Needs Review |

---

### 3.3 Card Detail Page

Each credit card should have a detail page that shows:

- Purchases for the selected month.
- Payments made for the selected month.
- Credits, cashback, and refunds.
- Available amount for payment.
- Planned payment amount.
- Planned payment date.
- Notes.
- Monthly transaction history.

The app should allow editing local-only planning fields, but it should **not write those changes back to YNAB** in the MVP.

---

### 3.4 Settings Page

The settings page should include:

- YNAB API token status.
- YNAB budget / plan ID.
- Selected income accounts.
- Selected credit card accounts.
- Manual due dates for each credit card.
- Manual minimum payment amounts.
- Optional notes for each card.

---

## 4. Data Source Plan

### 4.1 YNAB API Data Needed

The app should use the YNAB API to pull:

1. **Accounts**
   - Used to identify checking, savings, and credit card accounts.

2. **Monthly transactions**
   - Used to calculate income, credit card spending, payments, refunds, and credits.

3. **Categories**
   - Used to get Credit Card Payment category available amounts.

4. **Scheduled transactions**
   - Optional later feature to detect planned payments already scheduled in YNAB.

---

## 5. Suggested Tech Stack

Use a simple Python-based local app for the MVP.

```text
Backend: Python FastAPI
Database: SQLite
ORM: SQLAlchemy or SQLModel
Frontend: Jinja2 templates + simple CSS
Hosting: Localhost only
Local URL: http://127.0.0.1:8000
```

This keeps the first version simple and easier to debug.

---

## 6. Project Folder Structure

```text
cc-payment-tracker/
│
├── backend/
│   ├── main.py              # FastAPI app
│   ├── ynab_client.py       # YNAB API wrapper
│   ├── sync_service.py      # Monthly sync logic
│   ├── calculations.py      # Income, payments, spending calculations
│   ├── models.py            # SQLite models
│   └── database.py
│
├── frontend/
│   ├── templates/
│   │   ├── dashboard.html
│   │   ├── card_detail.html
│   │   └── settings.html
│   └── static/
│       └── style.css
│
├── tests/
│   └── test_calculations.py
│
├── .env
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

---

## 7. Database Design

### 7.1 `accounts`

```text
id
ynab_account_id
name
type
balance_milliunits
cleared_balance_milliunits
uncleared_balance_milliunits
is_credit_card
is_active
last_synced_at
```

---

### 7.2 `transactions`

```text
id
ynab_transaction_id
account_id
date
amount_milliunits
payee_name
category_name
memo
cleared
approved
transfer_account_id
is_payment
is_income
is_cashback_or_credit
created_at
```

---

### 7.3 `monthly_card_summary`

```text
id
month
account_id
card_name
starting_balance_milliunits
ending_balance_milliunits
cc_spending_milliunits
payments_made_milliunits
refunds_or_credits_milliunits
available_for_payment_milliunits
planned_payment_milliunits
due_date
payment_status
synced_at
```

---

### 7.4 `payment_plan`

```text
id
account_id
month
planned_payment_date
planned_amount_milliunits
source_account
status
notes
created_at
updated_at
```

---

## 8. Calculation Logic

For a selected month:

```python
month_start = first_day_of_month
month_end = last_day_of_month
```

---

### 8.1 Monthly Income / Earned

Income should be positive inflows into selected checking or savings accounts, excluding transfers.

```python
monthly_income = sum(
    transaction.amount
    for transaction in transactions
    if transaction.account_type in ["checking", "savings"]
    and transaction.amount > 0
    and not transaction.transfer_account_id
)
```

---

### 8.2 Credit Card Spending

Credit card spending should be negative transactions in credit card accounts, excluding transfers.

```python
cc_spending = abs(sum(
    transaction.amount
    for transaction in transactions
    if transaction.account_type == "creditCard"
    and transaction.amount < 0
    and not transaction.transfer_account_id
))
```

---

### 8.3 Payments Made to Credit Cards

Payments made should be positive transactions in credit card accounts where there is a transfer account.

```python
payments_made = sum(
    transaction.amount
    for transaction in transactions
    if transaction.account_type == "creditCard"
    and transaction.amount > 0
    and transaction.transfer_account_id is not None
)
```

---

### 8.4 Cashback / Refunds / Credits

Cashback, refunds, and credits should be positive transactions in credit card accounts that are not transfers.

```python
cashback_or_credits = sum(
    transaction.amount
    for transaction in transactions
    if transaction.account_type == "creditCard"
    and transaction.amount > 0
    and transaction.transfer_account_id is None
)
```

---

### 8.5 Payment Gap

```python
payment_gap = planned_payment - available_for_payment
```

Status rules:

```text
Covered       = payment_gap <= 0
Short         = payment_gap > 0
Over-reserved = available_for_payment > absolute_card_balance
Paid          = planned payment already completed
Needs Review  = missing due date, missing planned payment, or unclear data
```

---

## 9. Security Rules

The project should follow these security rules:

```text
1. Do not store bank usernames or passwords.
2. Do not automate browser login to banks or credit-card websites.
3. Use the YNAB API token only.
4. Store secrets only in a .env file.
5. Add .env to .gitignore.
6. Keep the local web app bound to 127.0.0.1 only.
7. Do not expose the app to the internet.
8. Do not create, edit, or delete YNAB transactions in the MVP.
9. Build read-only sync first.
10. Require manual confirmation before any future write action.
11. Never log the YNAB API token.
12. Avoid storing more financial data than needed.
```

---

## 10. MVP Build Order

Build the project in this order:

1. Create the FastAPI project structure.
2. Add `.env`, `.env.example`, and `.gitignore`.
3. Build YNAB connection test.
4. Sync YNAB accounts.
5. Sync monthly transactions.
6. Store synced data in SQLite.
7. Build calculation functions.
8. Add tests for calculation functions.
9. Build the local dashboard.
10. Build the credit card dashboard.
11. Build the card detail page.
12. Add local-only manual payment planning.
13. Add monthly history.
14. Add better error handling and logging.
15. Create README setup instructions.

---

## 11. Limitations

YNAB can provide useful transaction and category data, but it may not always provide exact credit card statement details, such as:

- Statement closing date.
- Statement balance.
- Minimum payment due.
- Credit card payment due date.
- Reward points or cashback that has not posted yet.

For the MVP, these should be entered manually in the local tracker.

Later, they can be improved if there is a secure official API source.

---

## 12. Prompt for Google Antigravity

Use the following prompt in Google Antigravity:

```text
Build a local credit-card payment tracker web app.

Goal:
Create a locally hosted web platform that connects to YNAB using the official YNAB API and helps me track monthly income, credit-card spending, credit-card payments, and planned payments.

Important security rules:
- Do not build bank-login scraping.
- Do not store bank usernames or passwords.
- Do not automate browser login to banks or credit-card websites.
- Use only the YNAB API for the MVP.
- Store the YNAB access token in a .env file.
- Add .env to .gitignore.
- The app must run locally only on 127.0.0.1.
- The MVP must be read-only against YNAB. Do not create, update, or delete YNAB data.
- Never log the YNAB token.

Tech stack:
- Python
- FastAPI
- SQLite
- SQLAlchemy or SQLModel
- Jinja2 templates
- Simple responsive HTML/CSS
- Local URL: http://127.0.0.1:8000

Features:
1. Settings page:
   - Enter/save YNAB plan ID. Default can be "last-used".
   - Check YNAB connection.
   - Select which accounts are income/cash accounts.
   - Select which accounts are credit-card accounts.
   - Allow manual due date and minimum payment entry for each credit card.

2. Sync:
   - Pull YNAB accounts.
   - Pull transactions for a selected month from the 1st day to the last day.
   - Pull category data for Credit Card Payment categories.
   - Store synced data in SQLite.
   - Avoid duplicate transactions by using YNAB transaction ID.

3. Monthly dashboard:
   - Month selector.
   - Show total earned/income for the month.
   - Show total credit-card spending.
   - Show total credit-card payments already made.
   - Show total planned payments.
   - Show remaining shortfall or surplus.

4. Credit-card dashboard:
   For each card show:
   - Card name
   - Current balance
   - Spending this month
   - Payments made this month
   - Refunds/cashback/credits this month
   - Available for payment from YNAB
   - Manual planned payment amount
   - Manual due date
   - Status: Covered, Short, Over-reserved, Paid, or Needs Review

5. Card detail page:
   - Show all monthly transactions for that card.
   - Separate purchases, payments, and credits.
   - Allow editing local-only planned payment amount, due date, and notes.
   - Do not write these edits back to YNAB.

6. Calculations:
   - Income = positive inflows into selected cash accounts, excluding transfers.
   - Credit-card spending = negative transactions in selected credit-card accounts, excluding transfers.
   - Payments made = positive credit-card transactions that are transfers from cash accounts.
   - Cashback/credits = positive credit-card transactions that are not transfers.
   - Payment gap = planned payment - available for payment.
   - Covered if payment gap <= 0.
   - Short if payment gap > 0.

7. Data handling:
   - YNAB API amounts use milliunits; add helper functions to convert to/from normal currency.
   - Store raw milliunits in the database.
   - Display formatted currency in the UI.
   - Handle API errors clearly.
   - Add logging, but never log the YNAB token.

Deliverables:
- Working FastAPI app.
- SQLite database setup.
- requirements.txt.
- .env.example.
- README.md with setup and run instructions.
- Basic tests for calculation functions.
```

---

## 13. Future Improvements

After the MVP works, add:

- Payment calendar view.
- Monthly payment history charts.
- Alerts for upcoming due dates.
- Shortfall warnings.
- CSV export.
- Optional local password protection.
- Optional YNAB scheduled transaction reading.
- Optional write-back to YNAB only after manual confirmation.
- Optional statement balance tracking.

---

## 14. Final Recommendation

Start with a **read-only YNAB-powered local tracker**.

Do not start with direct bank or credit card login automation.

The YNAB-based version is safer, easier to build, easier to maintain, and enough for a strong MVP.
