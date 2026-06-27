from datetime import datetime, date
import calendar
from sqlalchemy.orm import Session
from backend.models import Account, Transaction, MonthlyCardSummary, PaymentPlan, PayerSummary, SystemSettings, ManualOutflow
from backend.ynab_client import YNABClient
from backend.calculations import (
    calculate_monthly_income,
    calculate_cc_spending,
    calculate_payments_made,
    calculate_cashback_or_credits
)

def get_days_in_month(year_str, month_str):
    year = int(year_str)
    month = int(month_str)
    _, num_days = calendar.monthrange(year, month)
    return date(year, month, 1), date(year, month, num_days)

def sync_data_from_ynab(db: Session, budget_id: str, year_str: str, month_str: str):
    """
    Syncs accounts, transactions, and credit card category balances from YNAB
    for the specified month (e.g. "2026", "06").
    """
    client = YNABClient()
    month_start, month_end = get_days_in_month(year_str, month_str)
    month_str_formatted = f"{year_str}-{month_str}"
    
    # 1. Sync Accounts
    ynab_accounts = client.get_accounts(budget_id)
    cash_account_ids = set()
    card_account_ids = set()
    
    for y_acc in ynab_accounts:
        if y_acc["deleted"]:
            continue
            
        is_card = y_acc["type"] == "creditCard"
        # Auto-detect checking/savings as cash accounts
        is_cash = y_acc["type"] in ["checking", "savings"]
        
        db_acc = db.query(Account).filter(Account.ynab_account_id == y_acc["id"]).first()
        if not db_acc:
            db_acc = Account(ynab_account_id=y_acc["id"])
            db.add(db_acc)
            
        db_acc.name = y_acc["name"]
        db_acc.type = y_acc["type"]
        db_acc.balance = y_acc["balance"]
        db_acc.is_credit_card = is_card
        
        # Only set is_cash on creation, don't overwrite if user manually changed it
        if db_acc.id is None:
            db_acc.is_cash = is_cash
            
        db_acc.is_active = not y_acc["closed"]
        db_acc.last_synced_at = datetime.utcnow()
        
        if db_acc.is_cash and db_acc.is_active:
            cash_account_ids.add(y_acc["id"])
        if db_acc.is_credit_card and db_acc.is_active:
            card_account_ids.add(y_acc["id"])
            
    db.commit()
    
    # Reload cash & card account ids from db to respect user overrides
    db_cash_accs = db.query(Account).filter(Account.is_cash == True, Account.is_active == True).all()
    cash_account_ids = {acc.ynab_account_id for acc in db_cash_accs}
    
    db_card_accs = db.query(Account).filter(Account.is_credit_card == True, Account.is_active == True).all()
    card_account_ids = {acc.ynab_account_id for acc in db_card_accs}
    
    # 2. Sync Month Categories (for CC Payment category balances)
    # The monthly endpoint needs YYYY-MM-01 format
    ynab_categories = client.get_month_categories(budget_id, f"{month_str_formatted}-01")
    # YNAB categories don't have account_id; match by category name to account name
    # Credit card payment categories have names matching their credit card account names
    card_available_by_name = {}  # card_name -> balance in milliunits
    for cat in ynab_categories:
        if cat.get("deleted"):
            continue
        if cat.get("category_group_name") and "Credit Card" in cat["category_group_name"]:
            # Category name matches the credit card account name
            cat_name = cat["name"].strip()
            cat_balance = cat["balance"]  # Can be negative (needs funding) or positive
            card_available_by_name[cat_name.lower()] = cat["balance"]
            
    # 3. Sync Transactions
    # YNAB API since_date format: YYYY-MM-DD
    # Fetch transactions from the start of the month
    since_date = f"{month_str_formatted}-01"
    ynab_transactions = client.get_transactions_since(budget_id, since_date)
    
    # Filter transactions that fall within our start/end dates
    monthly_txs = []
    for tx in ynab_transactions:
        tx_date = datetime.strptime(tx["date"], "%Y-%m-%d").date()
        if month_start <= tx_date <= month_end:
            monthly_txs.append(tx)
            
    # Create or update transactions in SQLite
    for tx in monthly_txs:
        db_tx = db.query(Transaction).filter(Transaction.ynab_transaction_id == tx["id"]).first()
        if not db_tx:
            db_tx = Transaction(ynab_transaction_id=tx["id"])
            db.add(db_tx)
            
        db_tx.account_id = tx["account_id"]
        db_tx.date = tx["date"]
        db_tx.amount = tx["amount"]
        db_tx.payee_name = tx["payee_name"]
        db_tx.category_id = tx["category_id"]
        db_tx.category_name = tx["category_name"]
        db_tx.memo = tx["memo"]
        db_tx.transfer_account_id = tx["transfer_account_id"]
        
        # Calculate flags
        is_cash_acc = tx["account_id"] in cash_account_ids
        is_card_acc = tx["account_id"] in card_account_ids
        
        db_tx.is_income = is_cash_acc and tx["amount"] > 0 and not tx["transfer_account_id"]
        db_tx.is_payment = is_card_acc and tx["amount"] > 0 and tx["transfer_account_id"] is not None
        db_tx.is_cashback_or_credit = is_card_acc and tx["amount"] > 0 and tx["transfer_account_id"] is None
        
    db.commit()
    
    # Fetch all transactions for this month to run our aggregations
    all_db_txs = db.query(Transaction).filter(
        Transaction.date >= str(month_start),
        Transaction.date <= str(month_end)
    ).all()
    
    # 4. Save Monthly Card Summary and initialize Payment Plans
    for card_acc in db_card_accs:
        # Calculate metrics for the month
        cc_spending = calculate_cc_spending(all_db_txs, card_acc.ynab_account_id)
        payments_made = calculate_payments_made(all_db_txs, card_acc.ynab_account_id)
        refunds_or_credits = calculate_cashback_or_credits(all_db_txs, card_acc.ynab_account_id)
        # Match available balance by card name (YNAB category name = card account name)
        available = card_available_by_name.get(card_acc.name.strip().lower(), 0)
        
        summary = db.query(MonthlyCardSummary).filter(
            MonthlyCardSummary.month == month_str_formatted,
            MonthlyCardSummary.ynab_account_id == card_acc.ynab_account_id
        ).first()
        
        if not summary:
            summary = MonthlyCardSummary(
                month=month_str_formatted,
                ynab_account_id=card_acc.ynab_account_id
            )
            db.add(summary)
            
        summary.card_name = card_acc.name
        summary.starting_balance = 0 # Can be calculated if needed, default to 0 for simplicity
        summary.ending_balance = card_acc.balance
        summary.cc_spending = cc_spending
        summary.payments_made = payments_made
        summary.refunds_or_credits = refunds_or_credits
        summary.available_for_payment = available
        summary.synced_at = datetime.utcnow()
        
        # Ensure a local PaymentPlan exists for this month and card
        plan = db.query(PaymentPlan).filter(
            PaymentPlan.month == month_str_formatted,
            PaymentPlan.ynab_account_id == card_acc.ynab_account_id
        ).first()
        
        if not plan:
            # Look up previous month plan to carry forward due date and min payment
            prev_month = get_previous_month_str(year_str, month_str)
            prev_plan = db.query(PaymentPlan).filter(
                PaymentPlan.month == prev_month,
                PaymentPlan.ynab_account_id == card_acc.ynab_account_id
            ).first()
            
            due_d = prev_plan.due_date if prev_plan else None
            min_p = prev_plan.min_payment if prev_plan else 0
            # Default payer to Pathum or if there is a card owner default
            payer = "Pathum"
            if "ramesha" in card_acc.name.lower():
                payer = "Ramesha"
                
            plan = PaymentPlan(
                ynab_account_id=card_acc.ynab_account_id,
                month=month_str_formatted,
                due_date=due_d,
                min_payment=min_p,
                planned_amount=0,
                payer=payer,
                payment_type="Man",
                is_done=False
            )
            db.add(plan)
            
    # Look up Spend - Pathum and Spend - Ramesha accounts for Zelle and inflow tracking
    pathum_spend_accounts = db.query(Account).filter(Account.name.ilike("%Spend - Pathum%")).all()
    ramesha_spend_accounts = db.query(Account).filter(Account.name.ilike("%Spend - Ramesha%")).all()
    pathum_acc_ids = {acc.ynab_account_id for acc in pathum_spend_accounts}
    ramesha_acc_ids = {acc.ynab_account_id for acc in ramesha_spend_accounts}

    # Calculate Zelle outflows per payer from Spend account transactions
    # Zelle transactions: amount < 0 in Spend accounts AND payee starts with 'zelle'
    pathum_zelle = abs(sum(
        tx.amount for tx in all_db_txs
        if tx.account_id in pathum_acc_ids
        and tx.amount < 0
        and tx.payee_name
        and tx.payee_name.lower().startswith("zelle")
    ))
    ramesha_zelle = abs(sum(
        tx.amount for tx in all_db_txs
        if tx.account_id in ramesha_acc_ids
        and tx.amount < 0
        and tx.payee_name
        and tx.payee_name.lower().startswith("zelle")
    ))

    # Calculate inflows for Spend - Pathum and Spend - Ramesha accounts from current monthly transactions
    pathum_inflow_sum = sum(
        tx.amount for tx in all_db_txs 
        if tx.account_id in pathum_acc_ids and tx.amount > 0 and not tx.transfer_account_id
    )
    ramesha_inflow_sum = sum(
        tx.amount for tx in all_db_txs 
        if tx.account_id in ramesha_acc_ids and tx.amount > 0 and not tx.transfer_account_id
    )

    # Ensure standard default payers exist and update their earnings + zelle dynamically
    for p_name in ["Pathum", "Ramesha"]:
        p_summary = db.query(PayerSummary).filter(
            PayerSummary.month == month_str_formatted,
            PayerSummary.payer_name == p_name
        ).first()
        
        calculated_earning = pathum_inflow_sum if p_name == "Pathum" else ramesha_inflow_sum
        calculated_zelle = pathum_zelle if p_name == "Pathum" else ramesha_zelle
        
        if not p_summary:
            # Let's check previous month's limit as fallback
            prev_month = get_previous_month_str(year_str, month_str)
            prev_p_summary = db.query(PayerSummary).filter(
                PayerSummary.month == prev_month,
                PayerSummary.payer_name == p_name
            ).first()
            starting_cash = prev_p_summary.starting_cash if prev_p_summary else 0
            
            # If no previous month and we are on 2026-06, let's check the excel sheet totals
            if not prev_p_summary and month_str_formatted == "2026-06":
                if p_name == "Ramesha":
                    starting_cash = 400000 # 400.00 milliunits
                elif p_name == "Pathum":
                    starting_cash = 3340000 # 3340.00 milliunits
            
            # Use calculated earning if we found inflows, otherwise use the fallback/seeded cash
            p_summary = PayerSummary(
                month=month_str_formatted,
                payer_name=p_name,
                starting_cash=calculated_earning if calculated_earning > 0 else starting_cash,
                zelle_outflows=calculated_zelle
            )
            db.add(p_summary)
        else:
            # Update the earning if positive inflows were found in YNAB
            if calculated_earning > 0:
                p_summary.starting_cash = calculated_earning
            # Always update Zelle (re-detect on each sync)
            p_summary.zelle_outflows = calculated_zelle
    db.commit()
    
    # 5. Update last synced timestamp
    sys_settings = db.query(SystemSettings).first()
    if not sys_settings:
        sys_settings = SystemSettings()
        db.add(sys_settings)
    sys_settings.ynab_budget_id = budget_id
    sys_settings.last_synced_at = datetime.utcnow()
    db.commit()
    
    # 6. Auto-deactivate placeholder seeded accounts now that real YNAB accounts exist
    seeded_accounts = db.query(Account).filter(Account.ynab_account_id.like("ynab_%")).all()
    if seeded_accounts:
        for acc in seeded_accounts:
            acc.is_active = False
        db.commit()

def get_previous_month_str(year_str, month_str):
    y, m = int(year_str), int(month_str)
    if m == 1:
        return f"{y-1}-12"
    else:
        return f"{y}-{str(m-1).zfill(2)}"
