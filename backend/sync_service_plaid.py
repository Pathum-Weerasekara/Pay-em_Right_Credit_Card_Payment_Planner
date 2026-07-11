import os
from datetime import datetime, date
import calendar
from sqlalchemy.orm import Session
from backend.models import Account, Transaction, MonthlyCardSummary, PaymentPlan, PayerSummary, SystemSettings, PlaidItem
from backend.plaid_client import PlaidClientWrapper
from backend.calculations import (
    calculate_cc_spending,
    calculate_payments_made,
    calculate_cashback_or_credits
)

def get_days_in_month(year_str, month_str):
    year = int(year_str)
    month = int(month_str)
    _, num_days = calendar.monthrange(year, month)
    return date(year, month, 1), date(year, month, num_days)

def sync_data_from_plaid(db: Session, year_str: str, month_str: str):
    """
    Syncs accounts, transactions, and balances from linked Plaid Items.
    """
    client = PlaidClientWrapper()
    month_start, month_end = get_days_in_month(year_str, month_str)
    month_str_formatted = f"{year_str}-{month_str}"

    plaid_items = db.query(PlaidItem).all()
    if not plaid_items:
        raise ValueError("No linked bank/credit card accounts. Go to Settings to link your accounts via Plaid.")

    # Track currently synced accounts and transactions
    plaid_account_ids = set()
    cash_account_ids = set()
    card_account_ids = set()

    for item in plaid_items:
        try:
            # 1. Fetch Accounts and Balances from Plaid
            accounts_resp = client.get_accounts_and_balances(item.access_token)
            accounts = accounts_resp.get("accounts", [])

            for acc in accounts:
                acc_id = acc["account_id"]
                
                # Plaid types: 'depository' (checking/savings), 'credit' (creditCard), etc.
                # Skip non-credit card accounts to save free connection limits
                is_card = acc["type"] == "credit"
                if not is_card:
                    continue

                plaid_account_ids.add(acc_id)
                db_acc = db.query(Account).filter(Account.ynab_account_id == acc_id).first()
                if not db_acc:
                    db_acc = Account(ynab_account_id=acc_id)
                    db.add(db_acc)

                db_acc.name = acc["name"]
                db_acc.type = "creditCard"
                
                # Standardize balance signs (Plaid credit card balance is positive, but in our db debt must be negative)
                raw_balance = int(round(acc["balances"]["current"] * 1000))
                db_acc.balance = -raw_balance
                db_acc.is_credit_card = True
                db_acc.is_cash = False
                db_acc.is_active = True
                db_acc.source = "plaid"
                db_acc.plaid_item_id = item.item_id
                db_acc.last_synced_at = datetime.utcnow()

                card_account_ids.add(acc_id)

            db.commit()

            # 2. Fetch Transactions from Plaid
            tx_resp = client.get_transactions(item.access_token, month_start, month_end)
            transactions = tx_resp.get("transactions", [])

            for tx in transactions:
                # Skip transactions that do not belong to our tracked credit card accounts
                if tx["account_id"] not in card_account_ids:
                    continue

                tx_id = tx["transaction_id"]
                db_tx = db.query(Transaction).filter(Transaction.ynab_transaction_id == tx_id).first()
                if not db_tx:
                    db_tx = Transaction(ynab_transaction_id=tx_id)
                    db.add(db_tx)

                db_tx.account_id = tx["account_id"]
                db_tx.date = tx["date"]
                
                # In Plaid, positive is outflow, negative is inflow. We multiply by -1 to match database convention.
                plaid_amount = tx["amount"]
                db_tx.amount = int(round(-plaid_amount * 1000))
                
                db_tx.payee_name = tx["name"]
                db_tx.category_id = tx.get("category_id")
                category_list = tx.get("category") or []
                db_tx.category_name = ", ".join(category_list)
                db_tx.memo = tx.get("payment_channel")
                db_tx.source = "plaid"

                # Check if it's a transfer/payment
                # Plaid transactions have a list of categories e.g. ["Transfer", "Credit Card Payment"]
                categories = [c.lower() for c in category_list]
                is_transfer = "transfer" in categories or "payment" in categories or "credit card payment" in categories

                if is_transfer:
                    db_tx.transfer_account_id = "plaid_transfer_placeholder"
                else:
                    db_tx.transfer_account_id = None

                is_cash_acc = tx["account_id"] in cash_account_ids
                is_card_acc = tx["account_id"] in card_account_ids

                # Flag mappings
                db_tx.is_income = is_cash_acc and db_tx.amount > 0 and not db_tx.transfer_account_id
                db_tx.is_payment = is_card_acc and db_tx.amount > 0 and db_tx.transfer_account_id is not None
                db_tx.is_cashback_or_credit = is_card_acc and db_tx.amount > 0 and db_tx.transfer_account_id is None

            db.commit()

        except Exception as e:
            print(f"Error syncing Plaid item {item.item_id}: {e}")
            continue

    # 3. Reload active accounts from DB
    db_card_accs = db.query(Account).filter(Account.is_credit_card == True, Account.is_active == True, Account.source == "plaid").all()
    all_db_txs = db.query(Transaction).filter(
        Transaction.date >= str(month_start),
        Transaction.date <= str(month_end),
        Transaction.source == "plaid"
    ).all()

    # 4. Save Monthly Card Summary and initialize Payment Plans
    for card_acc in db_card_accs:
        cc_spending = calculate_cc_spending(all_db_txs, card_acc.ynab_account_id)
        payments_made = calculate_payments_made(all_db_txs, card_acc.ynab_account_id)
        refunds_or_credits = calculate_cashback_or_credits(all_db_txs, card_acc.ynab_account_id)
        
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
        summary.starting_balance = 0
        summary.ending_balance = card_acc.balance
        summary.cc_spending = cc_spending
        summary.payments_made = payments_made
        summary.refunds_or_credits = refunds_or_credits
        summary.available_for_payment = 0  # Standard YNAB available payment logic not applicable in Plaid
        summary.synced_at = datetime.utcnow()

        # PaymentPlan initialization
        plan = db.query(PaymentPlan).filter(
            PaymentPlan.month == month_str_formatted,
            PaymentPlan.ynab_account_id == card_acc.ynab_account_id
        ).first()

        if not plan:
            # Default payer assignment
            payer = "Pathum"
            if "ramesha" in card_acc.name.lower():
                payer = "Ramesha"

            plan = PaymentPlan(
                ynab_account_id=card_acc.ynab_account_id,
                month=month_str_formatted,
                due_date=None,
                min_payment=0,
                planned_amount=0,
                payer=payer,
                payment_type="Man",
                is_done=False
            )
            db.add(plan)

    # 5. Dynamic Payer Summary updates from Plaid cash account transactions (N/A since we skip depository accounts)
    pathum_checking = None
    ramesha_checking = None
    
    pathum_acc_ids = set()
    ramesha_acc_ids = set()

    pathum_inflow_sum = 0
    ramesha_inflow_sum = 0

    pathum_zelle = 0
    ramesha_zelle = 0

    for p_name in ["Pathum", "Ramesha"]:
        p_summary = db.query(PayerSummary).filter(
            PayerSummary.month == month_str_formatted,
            PayerSummary.payer_name == p_name
        ).first()

        calculated_earning = pathum_inflow_sum if p_name == "Pathum" else ramesha_inflow_sum
        calculated_zelle = pathum_zelle if p_name == "Pathum" else ramesha_zelle
        current_checking = pathum_checking if p_name == "Pathum" else ramesha_checking

        if not p_summary:
            # Let's check previous month's summary as fallback
            prev_y, prev_m = int(year_str), int(month_str)
            if prev_m == 1:
                prev_month = f"{prev_y-1}-12"
            else:
                prev_month = f"{prev_y}-{str(prev_m-1).zfill(2)}"
            
            prev_p_summary = db.query(PayerSummary).filter(
                PayerSummary.month == prev_month,
                PayerSummary.payer_name == p_name
            ).first()
            
            fallback_starting_cash = prev_p_summary.starting_cash if prev_p_summary else 0
            fallback_bank_balance = prev_p_summary.current_bank_balance if prev_p_summary else 0
            
            # Seed default starting cash on 2026-06 if no previous month exists
            if not prev_p_summary and month_str_formatted == "2026-06":
                if p_name == "Ramesha":
                    fallback_starting_cash = 400000 # $400.00
                elif p_name == "Pathum":
                    fallback_starting_cash = 3340000 # $3340.00

            p_summary = PayerSummary(
                month=month_str_formatted,
                payer_name=p_name,
                starting_cash=calculated_earning if calculated_earning > 0 else fallback_starting_cash,
                zelle_outflows=calculated_zelle,
                current_bank_balance=current_checking.balance if current_checking else fallback_bank_balance
            )
            db.add(p_summary)
        else:
            if calculated_earning > 0:
                p_summary.starting_cash = calculated_earning
            p_summary.zelle_outflows = calculated_zelle
            # Auto-update cash balance from Plaid sync, but keep override logic if handled in UI
            if current_checking and p_summary.current_bank_balance == 0:
                p_summary.current_bank_balance = current_checking.balance

    db.commit()

    # Update system settings
    sys_settings = db.query(SystemSettings).first()
    if not sys_settings:
        sys_settings = SystemSettings()
        db.add(sys_settings)
    sys_settings.last_synced_at = datetime.utcnow()
    db.commit()
