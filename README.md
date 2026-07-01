# Pay'em Right - CC Payment Planner & Tracker (Connected via Plaid)

A locally hosted web application to plan, monitor, and manage your credit card payments and bank balances. This tracker caches monthly transactions, checking account balances, and credit card balances locally, featuring custom payer bank account tracking, manual bank overrides, and dynamic projected ending balance calculations.

---

## Core Features

- **Payer Allocations**: Manage individual available cash budgets and current bank account balances for multiple payers (e.g. Pathum and Ramesha).
- **Projected Ending Balances**: Automatically compute expected bank balances at the end of the month: `Current Bank Balance - Pending CC Payments - Manual Outflows`.
- **Plaid Integration**: Safely retrieve monthly accounts, real-time balances, and transactions using Plaid's secure token-exchange API.
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
│   ├── plaid_client.py
│   └── sync_service_plaid.py
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

### Step 3: Get Your Plaid API Keys
1. Sign up on the [Plaid Dashboard](https://dashboard.plaid.com/).
2. Navigate to your Account Settings to find your API credentials (`client_id` and `secret`).
3. By default, your account starts with access to Plaid's free **Sandbox** mode. To connect real accounts for personal use, click "Request Access" to enable the **Development** tier (supporting up to 10 real bank connections).

### Step 4: Configure the App
Create a file named `.env` in the root folder, and insert your keys:
```env
PLAID_CLIENT_ID=your_plaid_client_id
PLAID_SECRET=your_plaid_secret
PLAID_ENV=sandbox # use 'sandbox' for testing, 'development' for real bank accounts
```

### Step 5: Initialize the Database
Run the migration script to configure database schemas:
```bash
python migrate.py
```

---

## Running the Application

1. In your terminal, run the following command to start the FastAPI server:
   ```bash
   uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
   ```
2. Open your browser and navigate to:
   [http://127.0.0.1:8000](http://127.0.0.1:8000)
3. Go to **Settings** to link your bank accounts or credit cards using the secure Plaid Link widget.

---

## Running the Tests
To run the automated math and calculation tests:
```bash
python -m pytest
```
