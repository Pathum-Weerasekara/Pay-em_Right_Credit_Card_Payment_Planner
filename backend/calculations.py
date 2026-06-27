def to_currency(milliunits: int) -> float:
    if milliunits is None:
        return 0.0
    return round(milliunits / 1000.0, 2)

def to_milliunits(amount: float) -> int:
    if amount is None:
        return 0
    return int(round(amount * 1000.0))

def determine_status(
    is_done: bool,
    planned_payment: int,
    available_for_payment: int,
    card_balance: int,
    due_date: str
) -> str:
    """
    Determine payment status for a card.
    Amounts are in milliunits.
    card_balance is typically negative (debt).
    """
    if is_done:
        return "Paid"
    
    # If the balance is 0 or positive, no payment needed
    if card_balance >= 0:
        return "Paid"
        
    abs_balance = abs(card_balance)
    payment_gap = planned_payment - available_for_payment
    
    if not due_date or planned_payment == 0:
        return "Needs Review"
        
    if available_for_payment > abs_balance:
        return "Over-reserved"
        
    if payment_gap > 0:
        return "Short"
        
    return "Covered"

def calculate_monthly_income(transactions: list, cash_account_ids: set) -> int:
    """
    Income: positive inflows into selected checking or savings accounts, excluding transfers.
    Transactions amount in milliunits.
    """
    income = 0
    for tx in transactions:
        if tx.account_id in cash_account_ids:
            # Positive amount and no transfer account
            if tx.amount > 0 and not tx.transfer_account_id:
                income += tx.amount
    return income

def calculate_cc_spending(transactions: list, card_account_id: str) -> int:
    """
    Credit card spending: negative transactions in selected card account, excluding transfers.
    """
    spending = 0
    for tx in transactions:
        if tx.account_id == card_account_id:
            # Negative amount and no transfer account
            if tx.amount < 0 and not tx.transfer_account_id:
                spending += tx.amount
    return abs(spending)

def calculate_payments_made(transactions: list, card_account_id: str) -> int:
    """
    Payments made: positive transactions in selected card account that are transfers from other accounts.
    """
    payments = 0
    for tx in transactions:
        if tx.account_id == card_account_id:
            if tx.amount > 0 and tx.transfer_account_id:
                payments += tx.amount
    return payments

def calculate_cashback_or_credits(transactions: list, card_account_id: str) -> int:
    """
    Cashback/credits: positive transactions in selected card account that are NOT transfers.
    """
    credits = 0
    for tx in transactions:
        if tx.account_id == card_account_id:
            if tx.amount > 0 and not tx.transfer_account_id:
                credits += tx.amount
    return credits
