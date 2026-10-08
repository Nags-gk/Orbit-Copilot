"""The 12 copilot tools, run against a seeded Orbit-shaped database."""
from __future__ import annotations

import json
from datetime import date

import pytest
from sqlalchemy import select

from orbit_copilot.models.orbit import Account, Subscription, Transaction
from orbit_copilot.tools import TOOL_SCHEMAS, execute_tool

from .conftest import ALICE, BOB

pytestmark = pytest.mark.asyncio


async def run(db, tool, /, **kwargs):
    return json.loads(await execute_tool(tool, kwargs, db, ALICE))


async def test_registry_has_twelve_tools_with_handlers():
    names = [t["name"] for t in TOOL_SCHEMAS]
    assert len(names) == 12 and len(set(names)) == 12
    for t in TOOL_SCHEMAS:
        assert t["input_schema"]["type"] == "object"


async def test_unknown_tool_returns_error(db):
    assert "Unknown tool" in (await run(db, "drop_tables"))["error"]


async def test_bad_arguments_are_reported_not_raised(db):
    assert "error" in await run(db, "get_transactions", not_a_param=1)


async def test_get_transactions_filters_and_isolates_users(db):
    out = await run(db, "get_transactions", limit=50)
    descriptions = {t["description"] for t in out["transactions"]}
    assert "Bob secret expense" not in descriptions
    assert out["count"] == 5

    food = await run(db, "get_transactions", category="food")  # case-insensitive
    assert {t["description"] for t in food["transactions"]} == {"Chipotle lunch", "Whole Foods"}

    search = await run(db, "get_transactions", search="uber")
    assert [t["description"] for t in search["transactions"]] == ["Uber to airport"]


async def test_spending_summary_month(db):
    out = await run(db, "get_spending_summary", period="month")
    assert out["totalIncome"] == 4000.0
    assert out["totalExpenses"] == 100.0  # 15 + 45 + 40; the 400-day-old purchase is excluded
    assert out["netBalance"] == 3900.0
    assert out["transactionCount"] == 4


async def test_category_breakdown_percentages(db):
    out = await run(db, "get_category_breakdown")
    by_cat = {c["category"]: c for c in out["categories"]}
    assert by_cat["Food"]["total"] == 55.0 and by_cat["Food"]["percentage"] == 55.0
    assert by_cat["Transport"]["percentage"] == 45.0
    assert out["grandTotal"] == 100.0


async def test_create_transaction_auto_categorizes(db):
    out = await run(db, "create_transaction", description="Lyft ride home", amount=22.5, type="Expense")
    assert out["success"] and out["auto_categorized"]
    assert out["transaction"]["category"] == "Transport"
    assert out["transaction"]["type"] == "expense"
    rows = (await db.execute(select(Transaction).where(Transaction.description == "Lyft ride home"))).scalars().all()
    assert len(rows) == 1 and rows[0].user_id == ALICE


async def test_cancel_subscription_by_name(db):
    out = await run(db, "cancel_subscription", name="netflix")
    assert out["success"] and out["cancelled"]["active"] is False
    active = await run(db, "get_subscriptions")
    assert [s["name"] for s in active["subscriptions"]] == ["Spotify"]
    assert active["totalMonthlyCost"] == 11.99


async def test_cancel_subscription_cannot_touch_other_users(db):
    db.add(Subscription(user_id=BOB, name="Hulu", cost=8.0, renewal_date=date.today(),
                        category="Entertainment"))
    await db.commit()
    assert (await run(db, "cancel_subscription", name="hulu"))["error"] == "Subscription not found."


async def test_account_create_update_and_net_worth(db):
    created = await run(db, "create_account", name="Ally Savings", type="Depository", balance=2500)
    assert created["account"]["subtype"] == "Checking"  # inferred default for depository

    updated = await run(db, "update_account", name="ally", new_balance=3000, new_name="Ally HYSA")
    assert updated["account"]["name"] == "Ally HYSA" and updated["account"]["balance"] == 3000

    nw = await run(db, "get_net_worth")
    # assets 5000 + 15000 + 3000, liabilities 1200 (credit)
    assert nw["totalAssets"] == 23000 and nw["totalLiabilities"] == 1200 and nw["netWorth"] == 21800

    accounts = await run(db, "get_accounts", type="investment")
    assert [a["name"] for a in accounts["accounts"]] == ["Schwab Brokerage"]
    rows = (await db.execute(select(Account).where(Account.user_id == BOB))).scalars().all()
    assert [a.balance for a in rows] == [1.0]  # Bob untouched


async def test_auto_categorize_tool(db):
    out = await run(db, "auto_categorize_transaction", description="Starbucks latte")
    assert out["suggestedCategory"] == "Food" and out["method"] == "keyword"
