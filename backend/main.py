import os
from dotenv import load_dotenv
load_dotenv() # Load env keys from .env file

from datetime import datetime
from fastapi import FastAPI, Depends, Request, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from backend.database import engine, Base, get_db
from backend.models import Account, Transaction, MonthlyCardSummary, PaymentPlan, PayerSummary, SystemSettings, ManualOutflow, PlaidItem
from backend.sync_service_plaid import sync_data_from_plaid, get_days_in_month
from backend.plaid_client import PlaidClientWrapper
from backend.calculations import to_currency, to_milliunits, determine_status

# Initialize DB tables
from sqlalchemy import text
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE accounts ADD COLUMN custom_name VARCHAR"))
        conn.commit()
    except Exception:
        pass
    try:
        conn.execute(text("ALTER TABLE accounts ADD COLUMN plaid_item_id VARCHAR"))
        conn.commit()
    except Exception:
        pass
    try:
        conn.execute(text("ALTER TABLE payment_plans ADD COLUMN is_active BOOLEAN DEFAULT 1"))
        conn.commit()
    except Exception:
        pass
Base.metadata.create_all(bind=engine)

def seed_initial_data():
    from backend.database import SessionLocal
    db = SessionLocal()
    try:
        # Backfill plaid_item_id mapping for any pre-existing accounts in DB
        items = db.query(PlaidItem).all()
        for item in items:
            try:
                from backend.plaid_client import PlaidClientWrapper
                client = PlaidClientWrapper()
                acc_resp = client.get_accounts_and_balances(item.access_token)
                accs = acc_resp.get("accounts", [])
                for a in accs:
                    db_acc = db.query(Account).filter(Account.ynab_account_id == a["account_id"]).first()
                    if db_acc and not db_acc.plaid_item_id:
                        db_acc.plaid_item_id = item.item_id
                db.commit()
            except Exception as e:
                print(f"Error backfilling account associations: {e}")
                pass
                
        # Only seed if there are zero payer summaries for 2026-06 AND no real Plaid accounts exist
        has_real_plaid_accounts = db.query(Account).filter(
            Account.source == "plaid"
        ).count() > 0
        
        if has_real_plaid_accounts:
            # Real accounts already synced from Plaid, skip seeding
            db.close()
            return
        
        # Seed PayerSummaries for 2026-06 if not already there
        existing_payers = db.query(PayerSummary).filter(PayerSummary.month == "2026-06").count()
        if existing_payers == 0:
            p_pathum = PayerSummary(
                month="2026-06",
                payer_name="Pathum",
                starting_cash=to_milliunits(3340.0)
            )
            p_ramesha = PayerSummary(
                month="2026-06",
                payer_name="Ramesha",
                starting_cash=to_milliunits(400.0)
            )
            db.add(p_pathum)
            db.add(p_ramesha)
            db.commit()
        
        # Only seed dummy accounts if there are no accounts at all (first startup, no YNAB configured)
        if db.query(Account).count() > 0:
            db.close()
            return
            
        # Add placeholder checking account
        checking = Account(
            ynab_account_id="ynab_checking_acc",
            name="Checking Account (placeholder)",
            type="checking",
            balance=0,
            is_credit_card=False,
            is_cash=True,
            is_active=True
        )
        db.add(checking)
        db.commit()
        
    except Exception as e:
        print(f"Error seeding DB: {e}")
    finally:
        db.close()

seed_initial_data()

app = FastAPI(title="YNAB CC Payment Planner")

# Setup templates and static files directories relative to the workspace root
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "frontend", "templates"))

# Mount static files
static_dir = os.path.join(BASE_DIR, "frontend", "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Jinja2 custom filters for format conversions
def format_currency(value):
    val_float = to_currency(value)
    return f"${val_float:,.2f}"

def format_date(value):
    if not value:
        return ""
    try:
        dt = datetime.strptime(value, "%Y-%m-%d")
        return dt.strftime("%b %d, %Y")
    except ValueError:
        return value

templates.env.filters["currency"] = format_currency
templates.env.filters["date"] = format_date
templates.env.filters["to_currency_raw"] = to_currency

@app.get("/", response_class=HTMLResponse)
def root():
    now = datetime.now()
    year_str = now.strftime("%Y")
    month_str = now.strftime("%m")
    return RedirectResponse(url=f"/dashboard/{year_str}/{month_str}", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/dashboard/{year}/{month}", response_class=HTMLResponse)
def read_dashboard(year: str, month: str, request: Request, db: Session = Depends(get_db)):
    month_str = f"{year}-{month.zfill(2)}"
    month_start, month_end = get_days_in_month(year, month)
    
    # Get settings
    settings = db.query(SystemSettings).first()
    
    # Fetch cash accounts for YNAB income calculation
    cash_accounts = db.query(Account).filter(Account.is_cash == True, Account.is_active == True).all()
    cash_account_ids = {acc.ynab_account_id for acc in cash_accounts}
    
    # Calculate overall monthly income (positive inflows to cash accounts excluding transfers)
    txs = db.query(Transaction).filter(
        Transaction.date >= str(month_start),
        Transaction.date <= str(month_end)
    ).all()
    
    total_income = 0
    for tx in txs:
        if tx.account_id in cash_account_ids and tx.amount > 0 and not tx.transfer_account_id:
            total_income += tx.amount
            
    # Load active credit cards
    cards = db.query(Account).filter(Account.is_credit_card == True, Account.is_active == True).all()
    
    card_list = []
    total_spending = 0
    total_planned = 0
    
    for card in cards:
        summary = db.query(MonthlyCardSummary).filter(
            MonthlyCardSummary.month == month_str,
            MonthlyCardSummary.ynab_account_id == card.ynab_account_id
        ).first()
        
        if not summary and card.source == "manual":
            summary = MonthlyCardSummary(
                month=month_str,
                ynab_account_id=card.ynab_account_id,
                card_name=card.name,
                starting_balance=0,
                ending_balance=card.balance,
                cc_spending=0,
                payments_made=0,
                refunds_or_credits=0,
                available_for_payment=0,
                synced_at=datetime.utcnow()
            )
            db.add(summary)
            db.commit()
            db.refresh(summary)
        
        plan = db.query(PaymentPlan).filter(
            PaymentPlan.month == month_str,
            PaymentPlan.ynab_account_id == card.ynab_account_id
        ).first()
        
        if not plan:
            try:
                dt_curr = datetime.strptime(month_str, "%Y-%m")
                if dt_curr.month == 1:
                    prev_month_str = f"{dt_curr.year - 1}-12"
                else:
                    prev_month_str = f"{dt_curr.year}-{str(dt_curr.month - 1).zfill(2)}"
                
                prev_plan = db.query(PaymentPlan).filter(
                    PaymentPlan.month == prev_month_str,
                    PaymentPlan.ynab_account_id == card.ynab_account_id
                ).first()
                
                if prev_plan:
                    plan = PaymentPlan(
                        ynab_account_id=card.ynab_account_id,
                        month=month_str,
                        planned_amount=0,
                        from_account=prev_plan.from_account,
                        payer=prev_plan.payer,
                        payment_type=prev_plan.payment_type,
                        due_date=prev_plan.due_date,
                        min_payment=prev_plan.min_payment,
                        notes=prev_plan.notes,
                        is_done=False
                    )
                    db.add(plan)
                    db.commit()
                    db.refresh(plan)
            except Exception as e:
                print(f"Error carrying forward payment plan: {e}")
                pass
        
        spending = summary.cc_spending if summary else 0
        payments_made = summary.payments_made if summary else 0
        opening_override = summary.opening_balance_override if summary else None
        
        planned_amt = plan.planned_amount if plan else 0
        if plan:
            ptype_str = "Auto" if plan.payment_type == "Auto" else "Manual"
            from_account = f"{plan.payer} {ptype_str}"
        else:
            from_account = "Pathum Manual"
        payer = plan.payer if plan else "Pathum"
        is_done = plan.is_done if plan else False
        unpaid_override = plan.unpaid_balance_override if plan else None
        
        # Unpaid Balance = spending - planned payment (with manual override option)
        if unpaid_override is not None:
            unpaid_balance = unpaid_override
        else:
            unpaid_balance = max(0, spending - planned_amt)
        
        # Status
        if is_done or (spending == 0 and planned_amt == 0):
            card_status = "—"
        elif planned_amt == 0 and spending > 0:
            card_status = "Needs Plan"
        elif unpaid_balance == 0:
            card_status = "Cleared"
        else:
            card_status = "Partial"
        
        total_spending += spending
        total_planned += planned_amt
        
        card_list.append({
            "account_id": card.ynab_account_id,
            "name": card.display_name,
            "balance": card.balance,
            "spending": spending,
            "payments_made": payments_made,
            "planned_payment": planned_amt,
            "from_account": from_account,
            "payer": payer,
            "unpaid_balance": unpaid_balance,
            "status": card_status,
            "is_done": is_done,
            "notes": plan.notes if plan else "",
            "is_active": plan.is_active if plan else False,
        })
    
    # Sort card_list: cards with spending first, then by name
    card_list.sort(key=lambda c: (-c["spending"], c["name"]))
        
    # Get Payer Summaries
    payers = db.query(PayerSummary).filter(PayerSummary.month == month_str).all()
    payer_data = []
    for p in payers:
        # Sum planned payments assigned to this payer (e.g. starts with "Pathum" or "Ramesha")
        p_planned_total = sum(c["planned_payment"] for c in card_list if c["from_account"] and c["from_account"].startswith(p.payer_name))
        
        # Sum pending planned payments (where is_done is False)
        p_pending_planned = sum(c["planned_payment"] for c in card_list if c["from_account"] and c["from_account"].startswith(p.payer_name) and not c["is_done"])
        
        # Get manual outflows for this payer this month
        manual_outflows = db.query(ManualOutflow).filter(
            ManualOutflow.month == month_str,
            ManualOutflow.payer == p.payer_name
        ).all()
        manual_total = sum(m.amount for m in manual_outflows)
        
        # Remaining = Inflow - Zelle - Manual Outflows - CC planned payments
        remaining = p.starting_cash - p.zelle_outflows - manual_total - p_planned_total
        
        # Projected Ending Balance = Current Bank Balance - Pending CC Payments - Manual Outflows
        projected_ending_balance = p.current_bank_balance - p_pending_planned - manual_total
        
        payer_data.append({
            "id": p.id,
            "name": p.payer_name,
            "starting_cash": p.starting_cash,
            "zelle_outflows": p.zelle_outflows,
            "manual_total": manual_total,
            "manual_outflows": manual_outflows,
            "cc_planned_total": p_planned_total,
            "cc_pending_planned": p_pending_planned,
            "current_bank_balance": p.current_bank_balance,
            "projected_ending_balance": projected_ending_balance,
            "remaining": remaining,
        })
        
    total_earnings = sum(p["starting_cash"] for p in payer_data)
    total_remaining = sum(p["remaining"] for p in payer_data)
    
    # Format month for display
    dt_month = datetime.strptime(month_str, "%Y-%m")
    display_month = dt_month.strftime("%B %Y")
    
    prev_year, prev_month = get_prev_next_month(int(year), int(month), -1)
    next_year, next_month = get_prev_next_month(int(year), int(month), 1)
    
    plaid_configured = db.query(PlaidItem).count() > 0
    success_msg = request.query_params.get("success")
    error_msg = request.query_params.get("error")
    
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "year": year,
            "month": month,
            "month_str": month_str,
            "display_month": display_month,
            "prev_year": prev_year,
            "prev_month": prev_month,
            "next_year": next_year,
            "next_month": next_month,
            "total_income": total_income,
            "total_earnings": total_earnings,
            "total_spending": total_spending,
            "total_planned": total_planned,
            "total_remaining": total_remaining,
            "card_list": card_list,
            "payer_data": payer_data,
            "settings": settings,
            "plaid_configured": plaid_configured,
            "success_msg": success_msg,
            "error_msg": error_msg
        }
    )

def get_prev_next_month(year: int, month: int, delta: int):
    m = month + delta
    y = year
    if m < 1:
        m = 12
        y -= 1
    elif m > 12:
        m = 1
        y += 1
    return str(y), str(m).zfill(2)

@app.post("/payer-limit/{year}/{month}", response_class=RedirectResponse)
def update_payer_limit(
    year: str, 
    month: str, 
    payer_id: int = Form(...), 
    starting_cash: float = Form(...),
    db: Session = Depends(get_db)
):
    p_summary = db.query(PayerSummary).filter(PayerSummary.id == payer_id).first()
    if p_summary:
        p_summary.starting_cash = to_milliunits(starting_cash)
        db.commit()
    return RedirectResponse(
        url=f"/dashboard/{year}/{month}?success=Payer budget updated", 
        status_code=status.HTTP_303_SEE_OTHER
    )

@app.post("/card-plan-inline/{year}/{month}", response_class=RedirectResponse)
async def save_card_plan_inline(year: str, month: str, request: Request, db: Session = Depends(get_db)):
    """Inline save of planned payment + from_account for a card from the dashboard table."""
    form = await request.form()
    month_str = f"{year}-{month.zfill(2)}"
    
    # Extract all card plan fields from form (account_id is prefix key)
    # Form fields: planned_{account_id}, from_{account_id}
    updated = 0
    accounts_seen = set()
    for key in form.keys():
        if key.startswith("planned_"):
            account_id = key[len("planned_"):]
            accounts_seen.add(account_id)
    
    for account_id in accounts_seen:
        try:
            planned_raw = form.get(f"planned_{account_id}", "0") or "0"
            clean_planned_raw = planned_raw.replace("$", "").replace(",", "").strip()
            planned_amt = to_milliunits(float(clean_planned_raw))
        except (ValueError, TypeError):
            planned_amt = 0
        from_account = form.get(f"from_{account_id}", "Pathum Manual")
        
        plan = db.query(PaymentPlan).filter(
            PaymentPlan.month == month_str,
            PaymentPlan.ynab_account_id == account_id
        ).first()
        
        if not plan:
            plan = PaymentPlan(ynab_account_id=account_id, month=month_str)
            db.add(plan)
        
        plan.planned_amount = planned_amt
        plan.from_account = from_account
        parts = from_account.split()
        plan.payer = parts[0] if parts else "Pathum"
        plan.payment_type = "Auto" if len(parts) > 1 and parts[1] == "Auto" else "Man"
        
        # Save Paid checkbox status
        plan.is_done = f"done_{account_id}" in form
        
        # Save Active/Selected checkbox status
        plan.is_active = f"active_{account_id}" in form
        
        # Save inline comment
        notes_val = form.get(f"notes_{account_id}", "").strip()
        plan.notes = notes_val if notes_val else None
        
        updated += 1
    
    db.commit()
    return RedirectResponse(
        url=f"/dashboard/{year}/{month}?success=Payment plans saved ({updated} cards updated)", 
        status_code=status.HTTP_303_SEE_OTHER
    )

@app.post("/manual-outflow/add/{year}/{month}", response_class=RedirectResponse)
async def add_manual_outflow(year: str, month: str, request: Request, db: Session = Depends(get_db)):
    """Add a new manual outflow entry for a payer."""
    form = await request.form()
    month_str = f"{year}-{month.zfill(2)}"
    payer = form.get("payer", "Pathum")
    description = form.get("description", "").strip()
    try:
        amount = to_milliunits(float(form.get("amount", 0) or 0))
    except (ValueError, TypeError):
        amount = 0
    
    if description and amount > 0:
        outflow = ManualOutflow(
            month=month_str,
            payer=payer,
            description=description,
            amount=amount
        )
        db.add(outflow)
        db.commit()
    
    return RedirectResponse(
        url=f"/dashboard/{year}/{month}?success=Manual outflow added", 
        status_code=status.HTTP_303_SEE_OTHER
    )

@app.post("/manual-outflow/edit/{outflow_id}", response_class=RedirectResponse)
async def edit_manual_outflow(outflow_id: int, request: Request, db: Session = Depends(get_db)):
    """Edit an existing manual outflow."""
    form = await request.form()
    outflow = db.query(ManualOutflow).filter(ManualOutflow.id == outflow_id).first()
    year = form.get("year", "2026")
    month = form.get("month", "06")
    if outflow:
        desc = form.get("description", "").strip()
        try:
            amt = to_milliunits(float(form.get("amount", 0) or 0))
        except (ValueError, TypeError):
            amt = 0
        if desc:
            outflow.description = desc
        if amt > 0:
            outflow.amount = amt
        db.commit()
    return RedirectResponse(
        url=f"/dashboard/{year}/{month}?success=Manual outflow updated", 
        status_code=status.HTTP_303_SEE_OTHER
    )

@app.post("/manual-outflow/delete/{outflow_id}", response_class=RedirectResponse)
async def delete_manual_outflow(outflow_id: int, request: Request, db: Session = Depends(get_db)):
    """Delete a manual outflow entry."""
    form = await request.form()
    year = form.get("year", "2026")
    month = form.get("month", "06")
    outflow = db.query(ManualOutflow).filter(ManualOutflow.id == outflow_id).first()
    if outflow:
        db.delete(outflow)
        db.commit()
    return RedirectResponse(
        url=f"/dashboard/{year}/{month}?success=Manual outflow deleted", 
        status_code=status.HTTP_303_SEE_OTHER
    )

@app.post("/card/{account_id}/{year}/{month}/unpaid-override", response_class=RedirectResponse)
async def set_unpaid_override(account_id: str, year: str, month: str, request: Request, db: Session = Depends(get_db)):
    """Manually override the unpaid balance for a card in a given month."""
    form = await request.form()
    month_str = f"{year}-{month.zfill(2)}"
    try:
        override_val = to_milliunits(float(form.get("unpaid_balance_override", 0) or 0))
    except (ValueError, TypeError):
        override_val = 0
    clear_override = form.get("clear_override") == "true"
    
    plan = db.query(PaymentPlan).filter(
        PaymentPlan.month == month_str,
        PaymentPlan.ynab_account_id == account_id
    ).first()
    if not plan:
        plan = PaymentPlan(ynab_account_id=account_id, month=month_str)
        db.add(plan)
    
    plan.unpaid_balance_override = None if clear_override else override_val
    db.commit()
    return RedirectResponse(
        url=f"/dashboard/{year}/{month}?success=Unpaid balance updated", 
        status_code=status.HTTP_303_SEE_OTHER
    )

@app.get("/card/{account_id}/{year}/{month}", response_class=HTMLResponse)
def read_card_detail(account_id: str, year: str, month: str, request: Request, db: Session = Depends(get_db)):
    month_str = f"{year}-{month.zfill(2)}"
    month_start, month_end = get_days_in_month(year, month)
    
    card = db.query(Account).filter(Account.ynab_account_id == account_id).first()
    if not card:
        return RedirectResponse(
            url=f"/dashboard/{year}/{month}?error=Card not found", 
            status_code=status.HTTP_303_SEE_OTHER
        )
        
    summary = db.query(MonthlyCardSummary).filter(
        MonthlyCardSummary.month == month_str,
        MonthlyCardSummary.ynab_account_id == account_id
    ).first()
    
    plan = db.query(PaymentPlan).filter(
        PaymentPlan.month == month_str,
        PaymentPlan.ynab_account_id == account_id
    ).first()
    
    # If no plan exists, initialize one
    if not plan:
        plan = PaymentPlan(
            ynab_account_id=account_id,
            month=month_str,
            planned_amount=0,
            payer="Pathum",
            payment_type="Man",
            is_done=False
        )
        db.add(plan)
        db.commit()
        db.refresh(plan)
        
    # Fetch all transactions for this card during the month
    txs = db.query(Transaction).filter(
        Transaction.account_id == account_id,
        Transaction.date >= str(month_start),
        Transaction.date <= str(month_end)
    ).order_by(Transaction.date.desc()).all()
    
    purchases = [tx for tx in txs if tx.amount < 0 and not tx.transfer_account_id]
    payments = [tx for tx in txs if tx.amount > 0 and tx.transfer_account_id]
    credits = [tx for tx in txs if tx.amount > 0 and not tx.transfer_account_id]
    
    # Calculate status and carry forward
    spending = summary.cc_spending if summary else 0
    available = summary.available_for_payment if summary else 0
    status_val = determine_status(
        plan.is_done, 
        plan.planned_amount, 
        available, 
        card.balance, 
        plan.due_date
    )
    carry_fwd = max(0, spending - plan.planned_amount)
    
    # Retrieve eligible payers for list selection
    payers = ["Pathum", "Ramesha"]
    
    dt_month = datetime.strptime(month_str, "%Y-%m")
    display_month = dt_month.strftime("%B %Y")
    
    success_msg = request.query_params.get("success")
    
    return templates.TemplateResponse(
        request=request,
        name="card_detail.html",
        context={
            "card": card,
            "summary": summary,
            "plan": plan,
            "purchases": purchases,
            "payments": payments,
            "credits": credits,
            "status": status_val,
            "carry_forward": carry_fwd,
            "payers": payers,
            "year": year,
            "month": month,
            "display_month": display_month,
            "success_msg": success_msg
        }
    )

@app.post("/card/{account_id}/{year}/{month}/plan", response_class=RedirectResponse)
async def update_card_plan(
    account_id: str,
    year: str,
    month: str,
    request: Request,
    db: Session = Depends(get_db)
):
    form = await request.form()
    planned_amount = float(form.get("planned_amount", 0))
    planned_payment_date = form.get("planned_payment_date") or None
    payer = form.get("payer", "Pathum")
    payment_type = form.get("payment_type", "Man")
    # HTML checkbox only submits when checked; absence means unchecked
    is_done = "is_done" in form
    due_date = form.get("due_date") or None
    min_payment = float(form.get("min_payment", 0) or 0)
    notes = form.get("notes") or None
    month_str = f"{year}-{month.zfill(2)}"
    
    plan = db.query(PaymentPlan).filter(
        PaymentPlan.month == month_str,
        PaymentPlan.ynab_account_id == account_id
    ).first()
    
    if not plan:
        plan = PaymentPlan(
            ynab_account_id=account_id,
            month=month_str
        )
        db.add(plan)
        
    plan.planned_amount = to_milliunits(planned_amount)
    plan.planned_payment_date = planned_payment_date or None
    plan.payer = payer
    plan.payment_type = payment_type
    plan.from_account = f"{payer} {'Auto' if payment_type == 'Auto' else 'Manual'}"
    plan.is_done = is_done
    plan.due_date = due_date or None
    plan.min_payment = to_milliunits(min_payment)
    plan.notes = notes
    
    # If card is manually managed, update its balance and monthly summary details
    card = db.query(Account).filter(Account.ynab_account_id == account_id).first()
    if card and card.source == "manual":
        try:
            card_balance = float(form.get("card_balance", 0) or 0)
            cc_spending = float(form.get("cc_spending", 0) or 0)
            payments_made = float(form.get("payments_made", 0) or 0)
            refunds_or_credits = float(form.get("refunds_or_credits", 0) or 0)
            
            card.balance = -to_milliunits(card_balance)
            
            summary = db.query(MonthlyCardSummary).filter(
                MonthlyCardSummary.month == month_str,
                MonthlyCardSummary.ynab_account_id == account_id
            ).first()
            
            if not summary:
                summary = MonthlyCardSummary(
                    month=month_str,
                    ynab_account_id=account_id,
                    card_name=card.name
                )
                db.add(summary)
                
            summary.ending_balance = card.balance
            summary.cc_spending = to_milliunits(cc_spending)
            summary.payments_made = to_milliunits(payments_made)
            summary.refunds_or_credits = to_milliunits(refunds_or_credits)
        except Exception as e:
            print(f"Error updating manual card details: {e}")
            
    db.commit()
    
    return RedirectResponse(
        url=f"/card/{account_id}/{year}/{month}?success=Payment plan saved successfully", 
        status_code=status.HTTP_303_SEE_OTHER
    )

@app.post("/dashboard/manual-card/add/{year}/{month}", response_class=RedirectResponse)
async def add_manual_card(
    year: str,
    month: str,
    name: str = Form(...),
    balance: float = Form(0.0),
    db: Session = Depends(get_db)
):
    import uuid
    month_str = f"{year}-{month.zfill(2)}"
    
    # Generate unique ID for the manual account
    ynab_acc_id = f"manual_cc_{uuid.uuid4().hex[:12]}"
    
    # Balance in database is stored as negative for credit card debt
    db_balance = -to_milliunits(balance)
    
    # Create the manual Account record
    new_card = Account(
        ynab_account_id=ynab_acc_id,
        name=name.strip(),
        type="creditCard",
        balance=db_balance,
        is_credit_card=True,
        is_cash=False,
        is_active=True,
        source="manual"
    )
    db.add(new_card)
    db.commit()
    db.refresh(new_card)
    
    # Auto-create summary for the current month
    summary = MonthlyCardSummary(
        month=month_str,
        ynab_account_id=ynab_acc_id,
        card_name=new_card.name,
        starting_balance=0,
        ending_balance=db_balance,
        cc_spending=to_milliunits(balance),
        payments_made=0,
        refunds_or_credits=0,
        available_for_payment=0,
        synced_at=datetime.utcnow()
    )
    db.add(summary)
    
    # Auto-create the PaymentPlan so it displays on the active list right away
    plan = PaymentPlan(
        ynab_account_id=ynab_acc_id,
        month=month_str,
        planned_amount=to_milliunits(balance),
        payer="Pathum",
        payment_type="Man",
        is_done=False,
        is_active=True
    )
    db.add(plan)
    db.commit()
    
    return RedirectResponse(
        url=f"/dashboard/{year}/{month}?success=Manual card added successfully",
        status_code=status.HTTP_303_SEE_OTHER
    )

@app.post("/card/{account_id}/{year}/{month}/delete", response_class=RedirectResponse)
def delete_manual_card(account_id: str, year: str, month: str, db: Session = Depends(get_db)):
    card = db.query(Account).filter(Account.ynab_account_id == account_id, Account.source == "manual").first()
    if card:
        # Delete summary, plan, transactions, and account
        db.query(Account).filter(Account.ynab_account_id == account_id).delete(synchronize_session=False)
        db.query(MonthlyCardSummary).filter(MonthlyCardSummary.ynab_account_id == account_id).delete(synchronize_session=False)
        db.query(PaymentPlan).filter(PaymentPlan.ynab_account_id == account_id).delete(synchronize_session=False)
        db.query(Transaction).filter(Transaction.account_id == account_id).delete(synchronize_session=False)
        db.commit()
        return RedirectResponse(
            url=f"/dashboard/{year}/{month}?success=Manual card deleted successfully",
            status_code=status.HTTP_303_SEE_OTHER
        )
    return RedirectResponse(
        url=f"/dashboard/{year}/{month}?error=Manual card not found",
        status_code=status.HTTP_303_SEE_OTHER
    )

@app.get("/settings", response_class=HTMLResponse)
def read_settings(request: Request, db: Session = Depends(get_db)):
    sys_settings = db.query(SystemSettings).first()
    accounts = db.query(Account).order_by(Account.type, Account.name).all()
    plaid_items = db.query(PlaidItem).all()
    
    success_msg = request.query_params.get("success")
    error_msg = request.query_params.get("error")
    
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "sys_settings": sys_settings,
            "accounts": accounts,
            "plaid_items": plaid_items,
            "success_msg": success_msg,
            "error_msg": error_msg
        }
    )


@app.post("/settings", response_class=RedirectResponse)
def save_settings():
    return RedirectResponse(url="/settings?success=Settings saved", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/api/create_link_token")
def create_link_token():
    try:
        client = PlaidClientWrapper()
        res = client.create_link_token()
        return res
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/exchange_public_token")
async def exchange_public_token(request: Request, db: Session = Depends(get_db)):
    try:
        form = await request.form()
        public_token = form.get("public_token")
        institution_name = form.get("institution_name", "Unknown Institution")
        if not public_token:
            return {"error": "Missing public_token"}
            
        client = PlaidClientWrapper()
        res = client.exchange_public_token(public_token)
        access_token = res.get("access_token")
        item_id = res.get("item_id")
        
        # Check if already linked
        existing = db.query(PlaidItem).filter(PlaidItem.item_id == item_id).first()
        if not existing:
            item = PlaidItem(
                item_id=item_id,
                access_token=access_token,
                institution_name=institution_name
            )
            db.add(item)
            db.commit()
            db.refresh(item)
        else:
            item = existing

        # Parse custom display names from frontend modal
        import json
        custom_names_str = form.get("accounts_custom_names", "{}")
        custom_names = {}
        try:
            custom_names = json.loads(custom_names_str)
        except Exception:
            pass

        # Fetch actual accounts from Plaid and save them immediately with custom names
        accounts_resp = client.get_accounts_and_balances(access_token)
        plaid_accounts = accounts_resp.get("accounts", [])
        
        for acc_data in plaid_accounts:
            # Sync checking, savings, and credit card accounts
            is_cc = acc_data.get("type") == "credit" or acc_data.get("subtype") == "credit card"
            acc_id = acc_data["account_id"]
            
            db_acc = db.query(Account).filter(Account.ynab_account_id == acc_id).first()
            if not db_acc:
                db_acc = Account(ynab_account_id=acc_id)
                db.add(db_acc)
                
            db_acc.name = acc_data["name"]
            db_acc.type = acc_data["subtype"] or acc_data["type"]
            db_acc.is_credit_card = is_cc
            db_acc.is_cash = not is_cc
            
            # Multiply by -1 for credit cards because Plaid returns positive values for credit card balances (debt)
            bal_multiplier = -1 if is_cc else 1
            db_acc.balance = int(round(acc_data["balances"].get("current", 0.0) * bal_multiplier * 1000))
            
            db_acc.is_active = True
            db_acc.source = "plaid"
            
            # Apply user-customized display name
            if acc_id in custom_names:
                db_acc.custom_name = custom_names[acc_id]
                
        db.commit()
        return {"success": True}
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return {"error": str(e)}

@app.post("/settings/plaid-item/delete/{item_id}", response_class=RedirectResponse)
def delete_plaid_item(item_id: str, db: Session = Depends(get_db)):
    item = db.query(PlaidItem).filter(PlaidItem.item_id == item_id).first()
    if item:
        try:
            # Fetch accounts associated with this Plaid item to clean them up
            client = PlaidClientWrapper()
            accounts_resp = client.get_accounts_and_balances(item.access_token)
            accounts = accounts_resp.get("accounts", [])
            acc_ids = [acc["account_id"] for acc in accounts]
            
            # Delete accounts and their transactions/summaries
            if acc_ids:
                db.query(Account).filter(Account.ynab_account_id.in_(acc_ids)).delete(synchronize_session=False)
                db.query(Transaction).filter(Transaction.account_id.in_(acc_ids)).delete(synchronize_session=False)
                db.query(MonthlyCardSummary).filter(MonthlyCardSummary.ynab_account_id.in_(acc_ids)).delete(synchronize_session=False)
                db.query(PaymentPlan).filter(PaymentPlan.ynab_account_id.in_(acc_ids)).delete(synchronize_session=False)
        except Exception as e:
            print(f"Error fetching accounts from Plaid during unlink: {e}")
            pass
            
        db.delete(item)
        db.commit()
        return RedirectResponse(url="/settings?success=Institution unlinked successfully", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(url="/settings?error=Institution not found", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/settings/account/rename/{account_id}", response_class=RedirectResponse)
def rename_settings_account(account_id: str, new_name: str = Form(...), db: Session = Depends(get_db)):
    acc = db.query(Account).filter(Account.ynab_account_id == account_id).first()
    if acc:
        acc.custom_name = new_name.strip() if new_name.strip() else None
        db.commit()
    return RedirectResponse(url="/settings?success=Card renamed successfully", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/sync-plaid/{year}/{month}", response_class=RedirectResponse)
def trigger_plaid_sync(year: str, month: str, db: Session = Depends(get_db)):
    try:
        sync_data_from_plaid(db, year, month)
        return RedirectResponse(
            url=f"/dashboard/{year}/{month}?success=Plaid data synced successfully", 
            status_code=status.HTTP_303_SEE_OTHER
        )
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return RedirectResponse(
            url=f"/dashboard/{year}/{month}?error=Sync failed: {str(e)}", 
            status_code=status.HTTP_303_SEE_OTHER
        )

@app.post("/payer-bank-balance/{year}/{month}", response_class=RedirectResponse)
def update_payer_bank_balance(
    year: str, 
    month: str, 
    payer_id: int = Form(...), 
    current_bank_balance: float = Form(...),
    db: Session = Depends(get_db)
):
    p_summary = db.query(PayerSummary).filter(PayerSummary.id == payer_id).first()
    if p_summary:
        p_summary.current_bank_balance = to_milliunits(current_bank_balance)
        db.commit()
    return RedirectResponse(
        url=f"/dashboard/{year}/{month}?success=Bank balance updated", 
        status_code=status.HTTP_303_SEE_OTHER
    )
