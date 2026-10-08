"""
Test fixtures.

Each test session gets two throwaway SQLite files: one shaped like Orbit's
database (seeded with two users so isolation can be checked) and one for the
copilot's own chat tables. Env vars are set before orbit_copilot is imported,
because the engines are built at import time.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import date, timedelta

_tmp = tempfile.mkdtemp(prefix="orbit-copilot-tests-")
os.environ.update(
    ORBIT_DATABASE_URL=f"sqlite+aiosqlite:///{_tmp}/orbit.db",
    COPILOT_DATABASE_URL=f"sqlite+aiosqlite:///{_tmp}/copilot.db",
    ANTHROPIC_API_KEY="",
    GEMINI_API_KEY="",
    USE_LOCAL_LLM="false",
    ALLOW_LOCAL_USER="true",
    ORBIT_JWT_SECRET="test-secret",
)

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from orbit_copilot.config import get_settings  # noqa: E402
from orbit_copilot.db import CopilotBase, OrbitBase, copilot_engine, init_copilot_db, orbit_engine, orbit_session  # noqa: E402
from orbit_copilot.models import chat as _chat_models  # noqa: E402,F401
from orbit_copilot.models.orbit import (  # noqa: E402
    Account, AccountType, Document, Subscription, Transaction, TransactionCategory, TransactionType, User,
)

ALICE = "11111111-1111-1111-1111-111111111111"   # oldest user → local-mode default
BOB = "22222222-2222-2222-2222-222222222222"


async def _seed() -> None:
    # In production Orbit creates these tables. Here the test plays Orbit.
    async with orbit_engine.begin() as conn:
        await conn.run_sync(OrbitBase.metadata.drop_all)
        await conn.run_sync(OrbitBase.metadata.create_all)
    async with copilot_engine.begin() as conn:
        await conn.run_sync(CopilotBase.metadata.drop_all)
    await init_copilot_db()

    today = date.today()
    async with orbit_session() as db:
        from datetime import datetime
        db.add_all([
            User(id=ALICE, email="alice@example.com", full_name="Alice", created_at=datetime(2026, 1, 1)),
            User(id=BOB, email="bob@example.com", full_name="Bob", created_at=datetime(2026, 2, 1)),
        ])
        await db.flush()
        db.add_all([
            Transaction(user_id=ALICE, description="Paycheck", amount=4000.0, category=TransactionCategory.Income,
                        type=TransactionType.income, date=today - timedelta(days=3)),
            Transaction(user_id=ALICE, description="Chipotle lunch", amount=15.0, category=TransactionCategory.Food,
                        type=TransactionType.expense, date=today - timedelta(days=2)),
            Transaction(user_id=ALICE, description="Uber to airport", amount=45.0, category=TransactionCategory.Transport,
                        type=TransactionType.expense, date=today - timedelta(days=1)),
            Transaction(user_id=ALICE, description="Whole Foods", amount=40.0, category=TransactionCategory.Food,
                        type=TransactionType.expense, date=today),
            Transaction(user_id=ALICE, description="Old purchase", amount=999.0, category=TransactionCategory.Shopping,
                        type=TransactionType.expense, date=today - timedelta(days=400)),
            Transaction(user_id=BOB, description="Bob secret expense", amount=123.0,
                        category=TransactionCategory.Shopping, type=TransactionType.expense, date=today),
            Account(user_id=ALICE, name="Chase Checking", type=AccountType.depository, subtype="Checking", balance=5000.0),
            Account(user_id=ALICE, name="Schwab Brokerage", type=AccountType.investment, subtype="Brokerage", balance=15000.0),
            Account(user_id=ALICE, name="Amex Gold", type=AccountType.credit, subtype="Credit Card", balance=1200.0),
            Account(user_id=BOB, name="Bob Checking", type=AccountType.depository, subtype="Checking", balance=1.0),
            Subscription(user_id=ALICE, name="Netflix", cost=15.49, renewal_date=today, category="Entertainment"),
            Subscription(user_id=ALICE, name="Spotify", cost=11.99, renewal_date=today, category="Entertainment"),
            Document(user_id=ALICE, filename="chase_march.pdf",
                     content="Chase statement March. Payment to landlord rent 2100. Coffee at Starbucks."),
            Document(user_id=ALICE, filename="amex_march.pdf",
                     content="American Express statement March. Delta airlines flight 450. Hotel booking."),
        ])
        await db.commit()


@pytest.fixture(autouse=True)
def fresh_db():
    """Reseed before every test so writes in one test can't leak into another."""
    get_settings.cache_clear()
    asyncio.run(_seed())
    yield


@pytest_asyncio.fixture
async def db():
    async with orbit_session() as session:
        yield session
