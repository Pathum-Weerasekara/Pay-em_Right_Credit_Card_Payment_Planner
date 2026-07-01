from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float
from backend.database import Base

class SystemSettings(Base):
    __tablename__ = "system_settings"
    id = Column(Integer, primary_key=True, index=True)
    ynab_budget_id = Column(String, nullable=True)
    last_synced_at = Column(DateTime, nullable=True)

class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True, index=True)
    ynab_account_id = Column(String, unique=True, index=True)  # Used as generic provider account ID (YNAB or Plaid)
    name = Column(String)
    type = Column(String)  # checking, savings, creditCard, etc.
    balance = Column(Integer)  # in milliunits
    is_credit_card = Column(Boolean, default=False)
    is_cash = Column(Boolean, default=False)  # checking/savings accounts
    is_active = Column(Boolean, default=True)
    source = Column(String, default="plaid")  # "ynab" or "plaid"
    last_synced_at = Column(DateTime, default=datetime.utcnow)

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    ynab_transaction_id = Column(String, unique=True, index=True)  # Generic provider transaction ID (YNAB or Plaid)
    account_id = Column(String, index=True)  # Provider account id
    date = Column(String, index=True)  # YYYY-MM-DD
    amount = Column(Integer)  # in milliunits
    payee_name = Column(String, nullable=True)
    category_id = Column(String, nullable=True)
    category_name = Column(String, nullable=True)
    memo = Column(String, nullable=True)
    transfer_account_id = Column(String, nullable=True)
    is_payment = Column(Boolean, default=False)
    is_income = Column(Boolean, default=False)
    is_cashback_or_credit = Column(Boolean, default=False)
    source = Column(String, default="plaid")  # "ynab" or "plaid"
    created_at = Column(DateTime, default=datetime.utcnow)

class MonthlyCardSummary(Base):
    __tablename__ = "monthly_card_summaries"
    id = Column(Integer, primary_key=True, index=True)
    month = Column(String, index=True)  # YYYY-MM
    ynab_account_id = Column(String, index=True)
    card_name = Column(String)
    starting_balance = Column(Integer)  # in milliunits
    ending_balance = Column(Integer)  # in milliunits
    cc_spending = Column(Integer)  # in milliunits (outflows on card this month)
    payments_made = Column(Integer)  # in milliunits
    refunds_or_credits = Column(Integer)  # in milliunits
    available_for_payment = Column(Integer)  # in milliunits (from YNAB CC Category Balance)
    opening_balance_override = Column(Integer, nullable=True)  # milliunits, user-set for Month 1 pre-existing debt
    synced_at = Column(DateTime, default=datetime.utcnow)

class PaymentPlan(Base):
    __tablename__ = "payment_plans"
    id = Column(Integer, primary_key=True, index=True)
    ynab_account_id = Column(String, index=True)
    month = Column(String, index=True)  # YYYY-MM
    planned_payment_date = Column(String, nullable=True)  # YYYY-MM-DD
    planned_amount = Column(Integer, default=0)  # in milliunits — what user plans to pay this month
    from_account = Column(String, nullable=True)  # "Pathum" or "Ramesha" (which Spend account pays)
    payer = Column(String, default="Pathum")  # "Pathum", "Ramesha"
    payment_type = Column(String, default="Man")  # "Man" (Manual), "Auto" (Automatic)
    is_done = Column(Boolean, default=False)  # Reconciled/Done status
    due_date = Column(String, nullable=True)  # day of month e.g. "15"
    min_payment = Column(Integer, default=0)  # in milliunits
    unpaid_balance_override = Column(Integer, nullable=True)  # milliunits, manual override of auto-calc unpaid
    notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class PayerSummary(Base):
    __tablename__ = "payer_summaries"
    id = Column(Integer, primary_key=True, index=True)
    month = Column(String, index=True)  # YYYY-MM
    payer_name = Column(String, index=True)  # "Pathum", "Ramesha"
    starting_cash = Column(Integer, default=0)  # in milliunits (monthly inflow / budget)
    zelle_outflows = Column(Integer, default=0)  # in milliunits, auto-detected from YNAB Zelle txns
    current_bank_balance = Column(Integer, default=0)  # in milliunits, manual input
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ManualOutflow(Base):
    """Per-payer manual outflows not captured in YNAB: Western Union, To Sri Lanka, etc."""
    __tablename__ = "manual_outflows"
    id = Column(Integer, primary_key=True, index=True)
    month = Column(String, index=True)  # YYYY-MM
    payer = Column(String, index=True)  # "Pathum" or "Ramesha"
    description = Column(String)  # e.g. "To Sri Lanka"
    amount = Column(Integer, default=0)  # in milliunits (positive value = outflow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class PlaidItem(Base):
    __tablename__ = "plaid_items"
    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(String, unique=True, index=True)
    access_token = Column(String, unique=True)
    institution_name = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


