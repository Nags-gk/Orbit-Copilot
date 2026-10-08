"""
Two async database connections.

* orbit  — Orbit's database. Orbit owns the schema; the copilot only maps the
           tables its tools need and never calls create_all on them.
* copilot — the copilot's own database for conversations and messages.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings


class OrbitBase(DeclarativeBase):
    """Declarative base for read/write mirrors of Orbit-owned tables."""


class CopilotBase(DeclarativeBase):
    """Declarative base for tables the copilot owns."""


def _engine(url: str) -> AsyncEngine:
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_async_engine(url, echo=False, connect_args=connect_args)


_settings = get_settings()

orbit_engine = _engine(_settings.orbit_database_url)
copilot_engine = _engine(_settings.copilot_database_url)

orbit_session = async_sessionmaker(orbit_engine, class_=AsyncSession, expire_on_commit=False)
copilot_session = async_sessionmaker(copilot_engine, class_=AsyncSession, expire_on_commit=False)


async def init_copilot_db() -> None:
    """Create the copilot's own tables. Orbit's tables are left alone."""
    from .models import chat  # noqa: F401  (registers the tables on CopilotBase)

    async with copilot_engine.begin() as conn:
        await conn.run_sync(CopilotBase.metadata.create_all)
