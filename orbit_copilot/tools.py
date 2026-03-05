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
from ..models import Transaction, Subscription, TransactionType, TransactionCategory


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
        "description": "Create a new transaction. Use this when the user wants to log an expense or income.",
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
                    "enum": ["Food", "Transport", "Utilities", "Entertainment", "Shopping", "Income", "Subscription"],
                    "description": "Transaction category",
                },
                "type": {
                    "type": "string",
                    "enum": ["income", "expense"],
                    "description": "Whether this is income or an expense",
                },
            },
            "required": ["description", "amount", "category", "type"],
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
        conditions.append(Transaction.category == TransactionCategory(category))
    if type:
        conditions.append(Transaction.type == TransactionType(type))
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
    category: str,
    type: str,
) -> dict:
    tx = Transaction(
        user_id=user_id,
        description=description,
        amount=amount,
        category=TransactionCategory(category),
        type=TransactionType(type),
        date=date.today(),
    )
    db.add(tx)
    await db.commit()
    await db.refresh(tx)
    return {"success": True, "transaction": tx.to_dict()}


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
