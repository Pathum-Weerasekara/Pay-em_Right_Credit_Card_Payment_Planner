# YNAB Credit Card Payment Planner & Tracker

A locally hosted web application to plan, monitor, and manage your credit card payments using YNAB data. This tracker caches monthly transactions, credit card category available balances, and cash inflow summaries locally, and features custom payer budgeting limits and carry-forward debt calculation support.

## Core Features

- **Payer Allocations**: Manage individual available cash budgets for multiple payers (e.g. Pathum and Ramesha) and check remaining balances after planned card payments.
- **Carry Forward Calculations**: Track monthly credit card spending vs. planned payments to compute carried-forward balances.
- **YNAB Integration**: Safely retrieve monthly accounts, transactions, and categories using YNAB's official API (strictly read-only).
- **Interactive Details**: Drill down into individual card transactions (grouped by purchases, payments, and credits/refunds).
- **Responsive Theme**: Sleek, glassmorphic dark theme built for premium visual quality.

---

## Getting Started

### Prerequisites
- Python 3.8+ installed on your computer.

### Step 1: Clone or Copy the Code
Ensure the project folder structure is as follows:
```text
CC Payment Planner and Tracker/
├── backend/
│   ├── main.py
│   ├── database.py
│   ├── models.py
│   ├── calculations.py
│   ├── ynab_client.py
│   └── sync_service.py
├── frontend/
│   ├── static/
│   │   └── style.css
│   └── templates/
│       ├── dashboard.html
│       ├── card_detail.html
│       └── settings.html
├── tests/
│   └── test_calculations.py
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

### Step 2: Install Dependencies
Open your terminal (PowerShell, Command Prompt, or bash) in the project directory and run:
```bash
pip install -r requirements.txt
```

### Step 3: Get Your YNAB Personal Access Token
1. Log in to your **YNAB account** on your browser.
2. Go to your **Account Settings** (click your profile in the bottom-left corner and select **Settings**).
3. Scroll down and click **Manage Developer Settings** (or navigate directly to [app.ynab.com/settings/developer](https://app.ynab.com/settings/developer)).
4. Under **Personal Access Tokens**, click **New Token**.
5. Re-enter your password and add a descriptive label (e.g. *Local CC Planner*).
6. Copy the generated token string immediately.

### Step 4: Configure the App
Create a file named `.env` in the root folder, and insert your token:
```env
YNAB_API_TOKEN=your_copied_ynab_token_here
```
*(Alternatively, you can paste the token directly inside the app's **Settings** page later).*

---

## Running the Application

1. In your terminal, run the following command to start the FastAPI server:
   ```bash
   uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
   ```
2. Open your browser and navigate to:
   [http://127.0.0.1:8000](http://127.0.0.1:8000)

---

## Running the Tests
To run the automated math and calculation tests:
```bash
pytest
```
