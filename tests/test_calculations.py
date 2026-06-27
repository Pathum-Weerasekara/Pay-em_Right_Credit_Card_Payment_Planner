import pytest
from backend.calculations import (
    to_currency, 
    to_milliunits, 
    determine_status, 
    calculate_monthly_income,
    calculate_cc_spending,
    calculate_payments_made,
    calculate_cashback_or_credits
)

# Mock transaction class for testing
class MockTx:
    def __init__(self, account_id, amount, transfer_account_id=None):
        self.account_id = account_id
        self.amount = amount
        self.transfer_account_id = transfer_account_id

def test_currency_conversion():
    assert to_currency(123450) == 123.45
    assert to_currency(0) == 0.0
    assert to_currency(None) == 0.0
    
    assert to_milliunits(123.45) == 123450
    assert to_milliunits(0.0) == 0
    assert to_milliunits(None) == 0

def test_determine_status():
    # Case 1: Already paid
    assert determine_status(True, 100000, 100000, -100000, "15") == "Paid"
    # Case 2: Balance positive or zero
    assert determine_status(False, 100000, 100000, 0, "15") == "Paid"
    # Case 3: Needs Review (missing due date or planned payment is 0)
    assert determine_status(False, 0, 100000, -100000, "15") == "Needs Review"
    assert determine_status(False, 100000, 100000, -100000, "") == "Needs Review"
    # Case 4: Over-reserved
    assert determine_status(False, 50000, 120000, -100000, "15") == "Over-reserved"
    # Case 5: Short
    assert determine_status(False, 100000, 80000, -150000, "15") == "Short"
    # Case 6: Covered
    assert determine_status(False, 80000, 80000, -150000, "15") == "Covered"

def test_calculate_monthly_income():
    cash_accs = {"cash_1", "cash_2"}
    txs = [
        MockTx("cash_1", 100000),  # Valid income
        MockTx("cash_1", -5000),   # Spending
        MockTx("cash_2", 200000),  # Valid income
        MockTx("cash_1", 150000, "other_acc"),  # Transfer (should exclude)
        MockTx("card_1", 500000),  # Inflow in card (not cash_accs)
    ]
    assert calculate_monthly_income(txs, cash_accs) == 300000

def test_calculate_cc_spending():
    txs = [
        MockTx("card_1", -150000), # Valid spending
        MockTx("card_1", -50000, "cash_1"), # Card transfer spending (should exclude)
        MockTx("card_1", 20000),  # Refund
        MockTx("card_2", -200000), # Other card
    ]
    assert calculate_cc_spending(txs, "card_1") == 150000

def test_calculate_payments_made():
    txs = [
        MockTx("card_1", 100000, "cash_1"),  # Valid payment transfer
        MockTx("card_1", 50000),            # Refund (no transfer)
        MockTx("card_1", -100000),          # Spending
    ]
    assert calculate_payments_made(txs, "card_1") == 100000

def test_calculate_cashback_or_credits():
    txs = [
        MockTx("card_1", 30000),            # Valid cashback/refund
        MockTx("card_1", 100000, "cash_1"),  # Payment transfer (should exclude)
        MockTx("card_1", -10000),           # Spending
    ]
    assert calculate_cashback_or_credits(txs, "card_1") == 30000
