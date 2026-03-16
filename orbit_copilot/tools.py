"""
Tool definitions and execution engine for the Orbit AI assistant.

Each tool is defined as an Anthropic-compatible tool schema and has a
corresponding execution function that operates on the database.
"""
from __future__ import annotations
import json
from datetime import date, datetime, timedelta
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from ..models import Transaction, Subscription, TransactionType, TransactionCategory, Account, AccountType, NetWorthSnapshot
from .categorizer import auto_categorize


# ── Tool Schemas (Anthropic format) ────────────────────────

TOOL_SCHEMAS = [
    {
        "name": "get_transactions",
        "description": "Fetch the user's transactions with optional filters. Returns a list of transactions sorted by date (newest first).",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["Food", "Transport", "Utilities", "Entertainment", "Shopping", "Income", "Subscription"],
                    "description": "Filter by transaction category",
                },
                "type": {
                    "type": "string",
                    "enum": ["income", "expense"],
                    "description": "Filter by income or expense",
                },
                "date_from": {
                    "type": "string",
                    "description": "Start date filter in YYYY-MM-DD format",
                },
                "date_to": {
                    "type": "string",
                    "description": "End date filter in YYYY-MM-DD format",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of transactions to return (default: 20)",
                },
                "search": {
                    "type": "string",
                    "description": "Search transactions by description text",
                },
            },
            "required": [],
        },
    },
    {
        "name": "create_transaction",
        "description": "Create a new transaction. Use this when the user wants to log an expense or income. If category is omitted or set to 'Auto', the system will intelligently auto-categorize based on the description.",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Description of the transaction (e.g., 'Lunch at Chipotle')",
                },
                "amount": {
                    "type": "number",
                    "description": "Transaction amount in dollars (always positive)",
                },
                "category": {
                    "type": "string",
                    "enum": ["Food", "Transport", "Utilities", "Entertainment", "Shopping", "Income", "Subscription", "Auto"],
                    "description": "Transaction category. Use 'Auto' to let the AI auto-categorize from the description.",
                },
                "type": {
                    "type": "string",
                    "enum": ["income", "expense"],
                    "description": "Whether this is income or an expense",
                },
            },
            "required": ["description", "amount", "type"],
        },
    },
    {
        "name": "get_subscriptions",
        "description": "Get the user's subscriptions. Can filter for only active subscriptions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "active_only": {
                    "type": "boolean",
                    "description": "If true, only return active subscriptions (default: true)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "cancel_subscription",
        "description": "Cancel (deactivate) a subscription by its ID or name. Use this when the user wants to cancel a subscription.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subscription_id": {
                    "type": "string",
                    "description": "The ID of the subscription to cancel",
                },
                "name": {
                    "type": "string",
                    "description": "The name of the subscription to cancel (used if ID not provided)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_spending_summary",
        "description": "Get a summary of the user's spending for a given time period. Returns total income, total expenses, net balance, and transaction count.",
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": ["week", "month", "year", "all"],
                    "description": "Time period for the summary (default: month)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_category_breakdown",
        "description": "Get spending breakdown by category for a given time period. Returns each category's total spend and percentage of overall spending.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {
                    "type": "string",
                    "description": "Start date in YYYY-MM-DD format",
                },
                "date_to": {
                    "type": "string",
                    "description": "End date in YYYY-MM-DD format",
                },
            },
            "required": [],
        },
    },
    {
        "name": "search_documents",
        "description": "Search the user's uploaded documents (like bank statements or contracts) for specific information using semantic keyword search.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to look for in the documents.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "auto_categorize_transaction",
        "description": "Intelligently categorize a transaction description into the best matching category. Use this when you want to suggest or verify a category for a transaction.",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "The transaction description to categorize (e.g., 'Uber ride to airport')",
                },
            },
            "required": ["description"],
        },
    },
    {
        "name": "get_accounts",
        "description": "Get all of the user's financial accounts with their current balances, grouped by type (depository, credit, investment, loan).",
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["depository", "credit", "investment", "loan"],
                    "description": "Optional: filter accounts by type",
                },
            },
            "required": [],
        },
    },
    {
        "name": "create_account",
        "description": "Create a new financial account. Use this when a user wants to add an account like 'Add my Schwab brokerage with $15,000'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Account name (e.g., 'Schwab Brokerage', 'Chase Sapphire', 'Ally Savings')",
                },
                "type": {
                    "type": "string",
                    "enum": ["depository", "credit", "investment", "loan"],
                    "description": "Account type",
                },
                "subtype": {
                    "type": "string",
                    "description": "Account subtype (e.g., 'Checking', 'Savings', 'Credit Card', 'Brokerage', '401k', 'IRA', 'Stock', 'Mortgage')",
                },
                "balance": {
                    "type": "number",
                    "description": "Current account balance in dollars",
                },
            },
            "required": ["name", "type"],
        },
    },
    {
        "name": "update_account",
        "description": "Update an existing account's name or balance. Use this when a user says things like 'Update my checking to $5,000'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account_id": {
                    "type": "string",
                    "description": "The account ID to update",
                },
                "name": {
                    "type": "string",
                    "description": "Search by account name if ID not known",
                },
                "new_name": {
                    "type": "string",
                    "description": "New name for the account",
                },
                "new_balance": {
                    "type": "number",
                    "description": "New balance for the account",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_net_worth",
        "description": "Get the user's current net worth breakdown showing total assets, total liabilities, and net worth. Also returns the portfolio allocation.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


# ── Tool Execution Functions ───────────────────────────────

async def execute_tool(tool_name: str, tool_input: dict, db: AsyncSession, user_id: str) -> str:
    """Route a tool call to its handler and return a JSON string result."""
    handlers = {
        "get_transactions": _get_transactions,
        "create_transaction": _create_transaction,
        "get_subscriptions": _get_subscriptions,
        "cancel_subscription": _cancel_subscription,
        "get_spending_summary": _get_spending_summary,
        "get_category_breakdown": _get_category_breakdown,
        "search_documents": _search_documents,
        "auto_categorize_transaction": _auto_categorize,
        "get_accounts": _get_accounts,
        "create_account": _create_account,
        "update_account": _update_account,
        "get_net_worth": _get_net_worth,
    }

    handler = handlers.get(tool_name)
    if not handler:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    try:
        result = await handler(db, user_id, **tool_input)
        return json.dumps(result, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


async def _search_documents(db: AsyncSession, user_id: str, query: str) -> dict:
    from .rag_engine import query_documents
    results = await query_documents(user_id, query)
    return {"results": results}


async def _get_transactions(
    db: AsyncSession,
    user_id: str,
    category: str | None = None,
    type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
    search: str | None = None,
) -> dict:
    query = select(Transaction)
    conditions = [Transaction.user_id == user_id]

    if category:
        try:
            # Try exact match, then case-insensitive
            cat_enum = TransactionCategory(category)
        except ValueError:
            category_map = {c.value.lower(): c for c in TransactionCategory}
            cat_enum = category_map.get(category.lower())
        
        if cat_enum:
            conditions.append(Transaction.category == cat_enum)

    if type:
        try:
            type_enum = TransactionType(type.lower())
            conditions.append(Transaction.type == type_enum)
        except ValueError:
            pass # Invalid type filter, ignore
    if date_from:
        conditions.append(Transaction.date >= date.fromisoformat(date_from))
    if date_to:
        conditions.append(Transaction.date <= date.fromisoformat(date_to))
    if search:
        conditions.append(Transaction.description.ilike(f"%{search}%"))

    if conditions:
        query = query.where(and_(*conditions))

    query = query.order_by(Transaction.date.desc()).limit(limit)
    result = await db.execute(query)
    transactions = result.scalars().all()

    return {
        "transactions": [tx.to_dict() for tx in transactions],
        "count": len(transactions),
    }


async def _create_transaction(
    db: AsyncSession,
    user_id: str,
    description: str,
    amount: float,
    type: str,
    category: str | None = None,
) -> dict:
    # Auto-categorize if category is missing or "Auto"
    auto_tagged = False
    if not category or category == "Auto":
        result = auto_categorize(description)
        category = result["category"]
        auto_tagged = True
    
    # Normalize type and category for Enum conversion
    try:
        norm_type = type.lower()
        tx_type_enum = TransactionType(norm_type)
    except ValueError:
        # Fallback for common AI spelling/capitalization
        if "expense" in type.lower():
            tx_type_enum = TransactionType.expense
        elif "income" in type.lower():
            tx_type_enum = TransactionType.income
        else:
            raise ValueError(f"Invalid transaction type: {type}")

    # Handle category with case-insensitivity
    try:
        # Try exact match first (value)
        tx_cat_enum = TransactionCategory(category)
    except ValueError:
        # Try case-insensitive match against values
        category_map = {c.value.lower(): c for c in TransactionCategory}
        if category.lower() in category_map:
            tx_cat_enum = category_map[category.lower()]
        else:
            # Try matching by key (case-insensitive)
            try:
                tx_cat_enum = getattr(TransactionCategory, category)
            except (AttributeError, TypeError):
                # Default to Shopping if it's an expense, or Income if it's income
                if tx_type_enum == TransactionType.income:
                    tx_cat_enum = TransactionCategory.Income
                else:
                    tx_cat_enum = TransactionCategory.Shopping
    
    tx = Transaction(
        user_id=user_id,
        description=description,
        amount=amount,
        category=tx_cat_enum,
        type=tx_type_enum,
        date=date.today(),
    )
    db.add(tx)
    await db.commit()
    await db.refresh(tx)
    result = {"success": True, "transaction": tx.to_dict()}
    if auto_tagged:
        result["auto_categorized"] = True
        result["suggested_category"] = category
    return result


async def _get_subscriptions(
    db: AsyncSession,
    user_id: str,
    active_only: bool = True,
) -> dict:
    query = select(Subscription).where(Subscription.user_id == user_id)
    if active_only:
        query = query.where(Subscription.active == True)
    query = query.order_by(Subscription.name)
    result = await db.execute(query)
    subs = result.scalars().all()
    total_monthly = sum(s.cost for s in subs)
    return {
        "subscriptions": [s.to_dict() for s in subs],
        "count": len(subs),
        "totalMonthlyCost": round(total_monthly, 2),
    }


async def _cancel_subscription(
    db: AsyncSession,
    user_id: str,
    subscription_id: str | None = None,
    name: str | None = None,
) -> dict:
    if subscription_id:
        query = select(Subscription).where(Subscription.id == subscription_id).where(Subscription.user_id == user_id)
    elif name:
        query = select(Subscription).where(Subscription.name.ilike(f"%{name}%")).where(Subscription.user_id == user_id)
    else:
        return {"error": "Please provide either a subscription_id or name."}

    result = await db.execute(query)
    sub = result.scalar_one_or_none()

    if not sub:
        return {"error": "Subscription not found."}

    sub.active = False
    await db.commit()
    return {"success": True, "cancelled": sub.to_dict()}


async def _get_spending_summary(
    db: AsyncSession,
    user_id: str,
    period: str = "month",
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    today = date.today()

    # Explicit date range overrides period
    if date_from:
        start_date = date.fromisoformat(date_from)
        end_date = date.fromisoformat(date_to) if date_to else today
    elif period == "week":
        start_date = today - timedelta(days=7)
        end_date = today
    elif period == "month":
        start_date = today - timedelta(days=30)
        end_date = today
    elif period == "year":
        start_date = today - timedelta(days=365)
        end_date = today
    else:
        start_date = date(2000, 1, 1)
        end_date = today

    income_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0))
        .where(Transaction.user_id == user_id)
        .where(and_(
            Transaction.type == TransactionType.income,
            Transaction.date >= start_date,
            Transaction.date <= end_date,
        ))
    )
    total_income = float(income_result.scalar())

    expense_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0))
        .where(Transaction.user_id == user_id)
        .where(and_(
            Transaction.type == TransactionType.expense,
            Transaction.date >= start_date,
            Transaction.date <= end_date,
        ))
    )
    total_expenses = float(expense_result.scalar())

    count_result = await db.execute(
        select(func.count(Transaction.id))
        .where(Transaction.user_id == user_id)
        .where(and_(
            Transaction.date >= start_date,
            Transaction.date <= end_date,
        ))
    )
    tx_count = int(count_result.scalar())

    return {
        "period": period,
        "dateRange": {"from": start_date.isoformat(), "to": end_date.isoformat()},
        "totalIncome": round(total_income, 2),
        "totalExpenses": round(total_expenses, 2),
        "netBalance": round(total_income - total_expenses, 2),
        "transactionCount": tx_count,
    }


async def _get_category_breakdown(
    db: AsyncSession,
    user_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    start = date.fromisoformat(date_from) if date_from else date.today() - timedelta(days=30)
    end = date.fromisoformat(date_to) if date_to else date.today()

    result = await db.execute(
        select(Transaction.category, func.sum(Transaction.amount).label("total"))
        .where(Transaction.user_id == user_id)
        .where(and_(
            Transaction.type == TransactionType.expense,
            Transaction.date >= start,
            Transaction.date <= end,
        ))
        .group_by(Transaction.category)
        .order_by(func.sum(Transaction.amount).desc())
    )
    rows = result.all()

    grand_total = sum(r.total for r in rows) if rows else 0

    categories = []
    for row in rows:
        categories.append({
            "category": row.category.value if hasattr(row.category, 'value') else row.category,
            "total": round(float(row.total), 2),
            "percentage": round(float(row.total) / grand_total * 100, 1) if grand_total > 0 else 0,
        })

    return {
        "dateRange": {"from": start.isoformat(), "to": end.isoformat()},
        "categories": categories,
        "grandTotal": round(grand_total, 2),
    }


# ── Auto-Categorize Tool ──────────────────────────────────

async def _auto_categorize(
    db: AsyncSession,
    user_id: str,
    description: str,
) -> dict:
    result = auto_categorize(description)
    return {
        "description": description,
        "suggestedCategory": result["category"],
        "confidence": result["confidence"],
        "method": result["method"],
        "alternatives": result.get("alternatives", []),
    }


# ── Account Management Tools ──────────────────────────────

async def _get_accounts(
    db: AsyncSession,
    user_id: str,
    type: str | None = None,
) -> dict:
    query = select(Account).where(Account.user_id == user_id)
    if type:
        query = query.where(Account.type == AccountType(type))
    query = query.order_by(Account.type)
    result = await db.execute(query)
    accounts = result.scalars().all()

    # Group by type for readability
    grouped = {}
    total_balance = 0.0
    for acc in accounts:
        acc_type = acc.type.value if hasattr(acc.type, 'value') else acc.type
        if acc_type not in grouped:
            grouped[acc_type] = []
        grouped[acc_type].append(acc.to_dict())
        total_balance += acc.balance

    return {
        "accounts": [a.to_dict() for a in accounts],
        "count": len(accounts),
        "totalBalance": round(total_balance, 2),
        "byType": grouped,
    }


async def _create_account(
    db: AsyncSession,
    user_id: str,
    name: str,
    type: str,
    subtype: str | None = None,
    balance: float = 0.0,
) -> dict:
    # Infer subtype from type if not provided
    if not subtype:
        type_to_subtype = {
            "depository": "Checking",
            "credit": "Credit Card",
            "investment": "Brokerage",
            "loan": "Loan",
        }
        subtype = type_to_subtype.get(type, "Other")

    try:
        acc_type_enum = AccountType(type.lower())
    except ValueError:
        # Fallback for common AI casing
        if "depository" in type.lower(): acc_type_enum = AccountType.depository
        elif "credit" in type.lower(): acc_type_enum = AccountType.credit
        elif "investment" in type.lower(): acc_type_enum = AccountType.investment
        elif "loan" in type.lower(): acc_type_enum = AccountType.loan
        else: raise ValueError(f"Invalid account type: {type}")

    acc = Account(
        user_id=user_id,
        name=name,
        type=acc_type_enum,
        subtype=subtype,
        balance=balance,
    )
    db.add(acc)
    await db.commit()
    await db.refresh(acc)
    return {"success": True, "account": acc.to_dict()}


async def _update_account(
    db: AsyncSession,
    user_id: str,
    account_id: str | None = None,
    name: str | None = None,
    new_name: str | None = None,
    new_balance: float | None = None,
) -> dict:
    # Find by ID or name
    if account_id:
        query = select(Account).where(Account.id == account_id, Account.user_id == user_id)
    elif name:
        query = select(Account).where(Account.name.ilike(f"%{name}%"), Account.user_id == user_id)
    else:
        return {"error": "Please provide an account_id or name to identify the account."}

    result = await db.execute(query)
    acc = result.scalar_one_or_none()

    if not acc:
        return {"error": "Account not found."}

    if new_name:
        acc.name = new_name
    if new_balance is not None:
        acc.balance = new_balance

    db.add(acc)
    await db.commit()
    await db.refresh(acc)
    return {"success": True, "account": acc.to_dict()}


async def _get_net_worth(
    db: AsyncSession,
    user_id: str,
) -> dict:
    result = await db.execute(
        select(Account).where(Account.user_id == user_id)
    )
    accounts = result.scalars().all()

    depository = sum(a.balance for a in accounts if a.type == AccountType.depository)
    credit = sum(a.balance for a in accounts if a.type == AccountType.credit)
    investment = sum(a.balance for a in accounts if a.type == AccountType.investment)
    loan = sum(a.balance for a in accounts if a.type == AccountType.loan)

    total_assets = depository + investment
    total_liabilities = credit + loan
    net_worth = total_assets - total_liabilities

    return {
        "netWorth": round(net_worth, 2),
        "totalAssets": round(total_assets, 2),
        "totalLiabilities": round(total_liabilities, 2),
        "breakdown": {
            "depository": round(depository, 2),
            "credit": round(credit, 2),
            "investment": round(investment, 2),
            "loan": round(loan, 2),
        },
        "accounts": [a.to_dict() for a in accounts],
    }
