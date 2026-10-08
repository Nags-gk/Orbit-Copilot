"""
Mirrors of the Orbit tables the copilot's tools touch.

This file is the data contract between Orbit and Orbit Copilot. Column
names, types and enum values must match orbit/backend/app/models.py. Orbit
creates and migrates these tables; the copilot never does. If Orbit changes
one of these columns, update this file in the same release.

Tables used:   users (identity only), transactions, accounts,
               subscriptions, documents, net_worth_snapshots
Writes:        transactions (create), accounts (create, update),
               subscriptions (deactivate)
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Enum as SAEnum, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import OrbitBase


class TransactionType(str, enum.Enum):
    income = "income"
    expense = "expense"


class AccountType(str, enum.Enum):
    depository = "depository"   # Checking, Savings
    credit = "credit"           # Credit Cards
    investment = "investment"   # Stocks, 401k, Crypto
    loan = "loan"               # Mortgages, Auto Loans


class TransactionCategory(str, enum.Enum):
    Food = "Food"
    Transport = "Transport"
    Utilities = "Utilities"
    Entertainment = "Entertainment"
    Shopping = "Shopping"
    Income = "Income"
    Subscription = "Subscription"


def _uuid() -> str:
    return str(uuid.uuid4())


class User(OrbitBase):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Transaction(OrbitBase):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    account_id: Mapped[Optional[str]] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    date: Mapped[date] = mapped_column(Date, default=date.today)
    description: Mapped[str] = mapped_column(String(255))
    category: Mapped[TransactionCategory] = mapped_column(SAEnum(TransactionCategory))
    amount: Mapped[float] = mapped_column(Float)
    type: Mapped[TransactionType] = mapped_column(SAEnum(TransactionType))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "accountId": self.account_id,
            "date": self.date.isoformat(),
            "description": self.description,
            "category": self.category.value,
            "amount": self.amount,
            "type": self.type.value,
        }


class Account(OrbitBase):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    type: Mapped[AccountType] = mapped_column(SAEnum(AccountType))
    subtype: Mapped[str] = mapped_column(String(100))
    balance: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type.value,
            "subtype": self.subtype,
            "balance": self.balance,
            "currency": self.currency,
            "updatedAt": self.updated_at.isoformat(),
        }


class Subscription(OrbitBase):
    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    cost: Mapped[float] = mapped_column(Float)
    renewal_date: Mapped[date] = mapped_column(Date)
    category: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    logo: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "cost": self.cost,
            "renewalDate": self.renewal_date.isoformat(),
            "category": self.category,
            "active": self.active,
            "logo": self.logo,
        }


class Document(OrbitBase):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NetWorthSnapshot(OrbitBase):
    __tablename__ = "net_worth_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    date: Mapped[date] = mapped_column(Date, default=date.today)
    total_assets: Mapped[float] = mapped_column(Float, default=0.0)
    total_liabilities: Mapped[float] = mapped_column(Float, default=0.0)
    net_worth: Mapped[float] = mapped_column(Float, default=0.0)
