"""
Manual SQLite migration to add new columns introduced in the models.py update.
Run once: python migrate.py
"""
import sys, os
sys.path.insert(0, r'c:\Users\pathu\OneDrive\Desktop\CC Payment Planner and Tracker')
os.chdir(r'c:\Users\pathu\OneDrive\Desktop\CC Payment Planner and Tracker')

from backend.database import engine
from sqlalchemy import text

migrations = [
    # MonthlyCardSummary - opening_balance_override
    "ALTER TABLE monthly_card_summaries ADD COLUMN opening_balance_override INTEGER",
    # PaymentPlan - from_account
    "ALTER TABLE payment_plans ADD COLUMN from_account VARCHAR",
    # PaymentPlan - unpaid_balance_override
    "ALTER TABLE payment_plans ADD COLUMN unpaid_balance_override INTEGER",
    # PayerSummary - zelle_outflows
    "ALTER TABLE payer_summaries ADD COLUMN zelle_outflows INTEGER DEFAULT 0",
    # Create manual_outflows table if not exists
    """CREATE TABLE IF NOT EXISTS manual_outflows (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        month VARCHAR NOT NULL,
        payer VARCHAR NOT NULL,
        description VARCHAR NOT NULL,
        amount INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS ix_manual_outflows_month ON manual_outflows (month)",
    "CREATE INDEX IF NOT EXISTS ix_manual_outflows_payer ON manual_outflows (payer)",
]

with engine.connect() as conn:
    for sql in migrations:
        try:
            conn.execute(text(sql))
            conn.commit()
            table = sql.strip().split()[2] if 'ADD COLUMN' in sql else sql.strip().split()[2]
            print(f"OK: {sql[:80]}...")
        except Exception as e:
            if 'duplicate column' in str(e).lower() or 'already exists' in str(e).lower():
                print(f"SKIP (already exists): {sql[:60]}...")
            else:
                print(f"ERROR: {e}")
                print(f"  SQL: {sql[:80]}")

print("\nMigration complete!")
