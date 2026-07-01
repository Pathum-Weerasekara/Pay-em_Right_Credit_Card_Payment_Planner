import os
from datetime import datetime
from fastapi import FastAPI, Depends, Request, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from backend.database import engine, Base, get_db
from backend.models import Account, Transaction, MonthlyCardSummary, PaymentPlan, PayerSummary, SystemSettings, ManualOutflow, PlaidItem
from backend.sync_service import sync_data_from_ynab, get_days_in_month
from backend.sync_service_plaid import sync_data_from_plaid
from backend.ynab_client import YNABClient
from backend.plaid_client import PlaidClientWrapper
from backend.calculations import to_currency, to_milliunits, determine_status

# Initialize DB tables
Base.metadata.create_all(bind=engine)

def seed_initial_data():
    from backend.database import SessionLocal
    db = SessionLocal()
    try:
        # Only seed if there are zero payer summaries for 2026-06 AND no real YNAB accounts exist
        has_real_ynab_accounts = db.query(Account).filter(
            ~Account.ynab_account_id.like("ynab_%")
        ).count() > 0
        
        if has_real_ynab_accounts:
            # Real accounts already synced from YNAB, skip seeding
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
    # User specified starting date is 01 June 2026
    return RedirectResponse(url="/dashboard/2026/06", status_code=status.HTTP_303_SEE_OTHER)

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
        
        plan = db.query(PaymentPlan).filter(
            PaymentPlan.month == month_str,
            PaymentPlan.ynab_account_id == card.ynab_account_id
        ).first()
        
        spending = summary.cc_spending if summary else 0
        payments_made = summary.payments_made if summary else 0
        opening_override = summary.opening_balance_override if summary else None
        
        planned_amt = plan.planned_amount if plan else 0
        from_account = plan.from_account if plan else "Pathum"
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
            "name": card.name,
            "balance": card.balance,
            "spending": spending,
            "payments_made": payments_made,
            "planned_payment": planned_amt,
            "from_account": from_account,
            "payer": payer,
            "unpaid_balance": unpaid_balance,
            "status": card_status,
            "is_done": is_done,
        })
    
    # Sort card_list: cards with spending first, then by name
    card_list.sort(key=lambda c: (-c["spending"], c["name"]))
        
    # Get Payer Summaries
    payers = db.query(PayerSummary).filter(PayerSummary.month == month_str).all()
    payer_data = []
    for p in payers:
        # Sum planned payments assigned to this payer
        p_planned_total = sum(c["planned_payment"] for c in card_list if c["from_account"] == p.payer_name)
        
        # Sum pending planned payments (where is_done is False)
        p_pending_planned = sum(c["planned_payment"] for c in card_list if c["from_account"] == p.payer_name and not c["is_done"])
        
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
    token_configured = bool(os.getenv("YNAB_API_TOKEN"))
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
            "token_configured": token_configured,
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
            planned_amt = to_milliunits(float(planned_raw))
        except (ValueError, TypeError):
            planned_amt = 0
        from_account = form.get(f"from_{account_id}", "Pathum")
        
        plan = db.query(PaymentPlan).filter(
            PaymentPlan.month == month_str,
            PaymentPlan.ynab_account_id == account_id
        ).first()
        
        if not plan:
            plan = PaymentPlan(ynab_account_id=account_id, month=month_str)
            db.add(plan)
        
        plan.planned_amount = planned_amt
        plan.from_account = from_account
        plan.payer = from_account  # keep payer in sync
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
    plan.is_done = is_done
    plan.due_date = due_date or None
    plan.min_payment = to_milliunits(min_payment)
    plan.notes = notes
    
    db.commit()
    
    return RedirectResponse(
        url=f"/card/{account_id}/{year}/{month}?success=Payment plan saved successfully", 
        status_code=status.HTTP_303_SEE_OTHER
    )

@app.get("/settings", response_class=HTMLResponse)
def read_settings(request: Request, db: Session = Depends(get_db)):
    # Read settings and active budget list
    sys_settings = db.query(SystemSettings).first()
    accounts = db.query(Account).order_by(Account.type, Account.name).all()
    plaid_items = db.query(PlaidItem).all()
    
    token = os.getenv("YNAB_API_TOKEN")
    budgets = []
    token_valid = False
    
    if token:
        try:
            client = YNABClient(token)
            token_valid = client.verify_token()
            if token_valid:
                budgets = client.get_budgets()
        except Exception:
            token_valid = False
            
    success_msg = request.query_params.get("success")
    error_msg = request.query_params.get("error")
    
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "sys_settings": sys_settings,
            "accounts": accounts,
            "budgets": budgets,
            "token_valid": token_valid,
            "token": token,
            "plaid_items": plaid_items,
            "success_msg": success_msg,
            "error_msg": error_msg
        }
    )


@app.post("/settings", response_class=RedirectResponse)
def save_settings(
    ynab_token: str = Form(None),
    budget_id: str = Form(None),
    db: Session = Depends(get_db)
):
    # Save token in .env file
    if ynab_token is not None:
        # Read existing .env contents
        env_lines = []
        token_found = False
        if os.path.exists(".env"):
            with open(".env", "r") as f:
                env_lines = f.readlines()
                
            for i, line in enumerate(env_lines):
                if line.startswith("YNAB_API_TOKEN="):
                    env_lines[i] = f"YNAB_API_TOKEN={ynab_token.strip()}\n"
                    token_found = True
                    break
        if not token_found:
            env_lines.append(f"YNAB_API_TOKEN={ynab_token.strip()}\n")
            
        with open(".env", "w") as f:
            f.writelines(env_lines)
            
        # Also refresh environment variable
        os.environ["YNAB_API_TOKEN"] = ynab_token.strip()
        
    # Save active budget
    sys_settings = db.query(SystemSettings).first()
    if not sys_settings:
        sys_settings = SystemSettings()
        db.add(sys_settings)
    if budget_id:
        sys_settings.ynab_budget_id = budget_id
    db.commit()
    
    return RedirectResponse(url="/settings?success=Settings saved", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/settings/accounts", response_class=RedirectResponse)
def save_accounts_settings(request: Request, db: Session = Depends(get_db)):
    # Parse form data to determine which accounts are checked
    # Since checkboxes only submit if checked, we fetch all active accounts from db and check presence in form
    accounts = db.query(Account).all()
    
    async def parse_form():
        form_data = await request.form()
        return form_data
        
    # FastAPI path methods can't easily wait inside sync methods without custom tricks,
    # but we can resolve the form data by accessing it synchronously. Actually, FastAPI allows 
    # declaring form parameters or using the Request object. To make it extremely safe:
    return RedirectResponse(url="/settings?error=Method not supported", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/settings/accounts-sync", response_class=RedirectResponse)
async def update_accounts_sync(request: Request, db: Session = Depends(get_db)):
    form_data = await request.form()
    accounts = db.query(Account).all()
    
    for acc in accounts:
        # Check cash checkbox
        cash_key = f"cash_{acc.ynab_account_id}"
        acc.is_cash = cash_key in form_data
        
        # Check active checkbox
        active_key = f"active_{acc.ynab_account_id}"
        acc.is_active = active_key in form_data
        
    db.commit()
    return RedirectResponse(url="/settings?success=Account roles updated", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/accounts/deactivate-seeds", response_class=RedirectResponse)
async def deactivate_seed_accounts(db: Session = Depends(get_db)):
    """Deactivate placeholder seeded accounts that have fake ynab_ prefixed IDs"""
    seeded = db.query(Account).filter(Account.ynab_account_id.like("ynab_%")).all()
    for acc in seeded:
        acc.is_active = False
    db.commit()
    return RedirectResponse(url="/settings?success=Placeholder accounts deactivated", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/sync/{year}/{month}", response_class=RedirectResponse)
def trigger_sync(year: str, month: str, db: Session = Depends(get_db)):
    sys_settings = db.query(SystemSettings).first()
    budget_id = sys_settings.ynab_budget_id if sys_settings else None
    
    # If no budget id set, try to default to first budget from API
    token = os.getenv("YNAB_API_TOKEN")
    if not token:
        return RedirectResponse(
            url=f"/dashboard/{year}/{month}?error=YNAB API Token is not configured. Go to Settings.", 
            status_code=status.HTTP_303_SEE_OTHER
        )
        
    if not budget_id:
        try:
            client = YNABClient(token)
            budgets = client.get_budgets()
            if budgets:
                budget_id = budgets[0]["id"]
            else:
                return RedirectResponse(
                    url=f"/dashboard/{year}/{month}?error=No YNAB budgets found.", 
                    status_code=status.HTTP_303_SEE_OTHER
                )
        except Exception as e:
            return RedirectResponse(
                url=f"/dashboard/{year}/{month}?error=Failed to fetch budget list: {str(e)}", 
                status_code=status.HTTP_303_SEE_OTHER
            )
            
    try:
        sync_data_from_ynab(db, budget_id, year, month)
        return RedirectResponse(
            url=f"/dashboard/{year}/{month}?success=YNAB data synced successfully", 
            status_code=status.HTTP_303_SEE_OTHER
        )
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return RedirectResponse(
            url=f"/dashboard/{year}/{month}?error=Sync failed: {str(e)}", 
            status_code=status.HTTP_303_SEE_OTHER
        )

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
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}

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
