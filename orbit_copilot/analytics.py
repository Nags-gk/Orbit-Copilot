"""
Analytics engine — spending forecasts, anomaly detection, and AI insights.

Uses pure Python statistics (no ML dependencies) for:
- Linear regression spending forecasts
- Standard deviation anomaly detection
- Actionable spending insights
- Subscription optimization recommendations
"""
from __future__ import annotations
import json
import statistics
from datetime import date, timedelta
from collections import defaultdict
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from ..models import Transaction, Subscription, TransactionType, TransactionCategory


# ── Forecasting ───────────────────────────────────────────

async def forecast_spending(db: AsyncSession, user_id: str, forecast_days: int = 30) -> dict:
    """
    Forecast future spending using linear regression on daily totals.
    Returns historical data + predicted data points.
    """
    # Get last 90 days of expenses
    start = date.today() - timedelta(days=90)
    result = await db.execute(
        select(Transaction.date, func.sum(Transaction.amount).label("total"))
        .where(Transaction.user_id == user_id)
        .where(and_(
            Transaction.type == TransactionType.expense,
            Transaction.date >= start,
        ))
        .group_by(Transaction.date)
        .order_by(Transaction.date)
    )
    rows = result.all()

    if len(rows) < 5:
        return {
            "historical": [],
            "forecast": [],
            "dailyAverage": 0,
            "predictedMonthlyTotal": 0,
            "trend": "insufficient_data",
        }

    # Build daily spending map (fill gaps with 0)
    daily = {}
    for row in rows:
        d = row[0] if isinstance(row[0], date) else date.fromisoformat(str(row[0]))
        daily[d] = float(row[1])

    # Fill missing days with 0
    all_days = []
    current = start
    while current <= date.today():
        all_days.append(current)
        current += timedelta(days=1)

    x_vals = list(range(len(all_days)))
    y_vals = [daily.get(d, 0) for d in all_days]

    # Linear regression: y = mx + b
    n = len(x_vals)
    mean_x = sum(x_vals) / n
    mean_y = sum(y_vals) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(x_vals, y_vals))
    den = sum((x - mean_x) ** 2 for x in x_vals)
    slope = num / den if den != 0 else 0
    intercept = mean_y - slope * mean_x

    # Historical data points (weekly averages for cleaner chart)
    historical = []
    for i in range(0, len(all_days), 7):
        week = y_vals[i:i+7]
        if week:
            historical.append({
                "date": all_days[i].isoformat(),
                "amount": round(sum(week) / len(week), 2),
                "type": "actual",
            })

    # Forecast data points
    forecast = []
    for i in range(forecast_days):
        future_x = n + i
        predicted = max(0, slope * future_x + intercept)
        future_date = date.today() + timedelta(days=i + 1)
        if i % 7 == 0:  # Weekly points
            forecast.append({
                "date": future_date.isoformat(),
                "amount": round(predicted, 2),
                "type": "predicted",
            })

    daily_avg = mean_y
    trend = "increasing" if slope > 0.5 else "decreasing" if slope < -0.5 else "stable"

    return {
        "historical": historical,
        "forecast": forecast,
        "dailyAverage": round(daily_avg, 2),
        "predictedMonthlyTotal": round(daily_avg * 30, 2),
        "trend": trend,
        "trendSlope": round(slope, 4),
    }


# ── Anomaly Detection ────────────────────────────────────

async def detect_anomalies(db: AsyncSession, user_id: str) -> dict:
    """
    Detect unusual transactions using standard deviation.
    A transaction is anomalous if its amount is > 2 std deviations above the category mean.
    """
    # Get all expenses from last 60 days
    start = date.today() - timedelta(days=60)
    result = await db.execute(
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .where(and_(
            Transaction.type == TransactionType.expense,
            Transaction.date >= start,
        ))
        .order_by(Transaction.date.desc())
    )
    transactions = result.scalars().all()

    if not transactions:
        return {"anomalies": [], "count": 0}

    # Group by category
    by_category: dict[str, list[float]] = defaultdict(list)
    tx_data = []
    for tx in transactions:
        cat = tx.category.value if hasattr(tx.category, 'value') else str(tx.category)
        by_category[cat].append(tx.amount)
        tx_data.append({
            "id": tx.id,
            "description": tx.description,
            "amount": tx.amount,
            "category": cat,
            "date": tx.date.isoformat() if isinstance(tx.date, date) else str(tx.date),
        })

    # Find anomalies per category
    anomalies = []
    for tx_info in tx_data:
        cat = tx_info["category"]
        amounts = by_category[cat]
        if len(amounts) < 3:
            continue

        mean = statistics.mean(amounts)
        stdev = statistics.stdev(amounts) if len(amounts) > 1 else 0

        if stdev > 0 and tx_info["amount"] > mean + 2 * stdev:
            z_score = (tx_info["amount"] - mean) / stdev
            anomalies.append({
                **tx_info,
                "reason": f"${tx_info['amount']:.2f} is unusually high for {cat} (avg: ${mean:.2f})",
                "severity": "high" if z_score > 3 else "medium",
                "zScore": round(z_score, 2),
            })

    anomalies.sort(key=lambda a: a.get("zScore", 0), reverse=True)

    return {
        "anomalies": anomalies[:10],  # Top 10 anomalies
        "count": len(anomalies),
    }


# ── Insights ─────────────────────────────────────────────

async def generate_insights(db: AsyncSession, user_id: str) -> dict:
    """
    Generate actionable financial insights based on spending patterns.
    """
    insights = []
    today = date.today()

    # ── 1. Current vs previous month comparison ──
    current_month_start = today.replace(day=1)
    prev_month_end = current_month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)

    for period_start, period_end, label in [
        (current_month_start, today, "current"),
        (prev_month_start, prev_month_end, "previous"),
    ]:
        result = await db.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0))
            .where(Transaction.user_id == user_id)
            .where(and_(
                Transaction.type == TransactionType.expense,
                Transaction.date >= period_start,
                Transaction.date <= period_end,
            ))
        )
        if label == "current":
            current_total = float(result.scalar())
        else:
            prev_total = float(result.scalar())

    if prev_total > 0:
        change_pct = ((current_total - prev_total) / prev_total) * 100
        days_elapsed = (today - current_month_start).days + 1
        projected = (current_total / days_elapsed) * 30 if days_elapsed > 0 else 0

        if change_pct > 20:
            insights.append({
                "type": "warning",
                "icon": "📈",
                "title": "Spending is up",
                "description": f"You've spent ${current_total:,.2f} so far this month — {abs(change_pct):.0f}% more than last month's ${prev_total:,.2f}.",
                "action": "Review recent expenses",
                "priority": 1,
            })
        elif change_pct < -10:
            insights.append({
                "type": "success",
                "icon": "🎉",
                "title": "Great savings!",
                "description": f"Spending is down {abs(change_pct):.0f}% compared to last month. You're projected to save ${prev_total - projected:,.2f}.",
                "priority": 3,
            })

        if projected > prev_total * 1.3:
            insights.append({
                "type": "warning",
                "icon": "⚠️",
                "title": "Overspending alert",
                "description": f"At this rate, you'll spend ~${projected:,.2f} this month vs ${prev_total:,.2f} last month.",
                "action": "Set a budget limit",
                "priority": 1,
            })

    # ── 2. Top spending category ──
    result = await db.execute(
        select(Transaction.category, func.sum(Transaction.amount).label("total"))
        .where(Transaction.user_id == user_id)
        .where(and_(
            Transaction.type == TransactionType.expense,
            Transaction.date >= current_month_start,
        ))
        .group_by(Transaction.category)
        .order_by(func.sum(Transaction.amount).desc())
        .limit(1)
    )
    top_cat = result.first()
    if top_cat:
        cat_name = top_cat[0].value if hasattr(top_cat[0], 'value') else str(top_cat[0])
        insights.append({
            "type": "info",
            "icon": "🏷️",
            "title": f"Top category: {cat_name}",
            "description": f"You've spent ${float(top_cat[1]):,.2f} on {cat_name} this month — your largest expense category.",
            "priority": 4,
        })

    # ── 3. Subscription cost awareness ──
    sub_result = await db.execute(
        select(func.sum(Subscription.cost))
        .where(Subscription.user_id == user_id)
        .where(Subscription.active == True)
    )
    total_subs = float(sub_result.scalar() or 0)
    if total_subs > 0:
        # What percentage of spending are subscriptions?
        if current_total > 0:
            sub_pct = (total_subs / current_total) * 100
            if sub_pct > 30:
                insights.append({
                    "type": "tip",
                    "icon": "🔄",
                    "title": "Subscriptions are significant",
                    "description": f"Your ${total_subs:,.2f}/mo in subscriptions makes up {sub_pct:.0f}% of your spending. Consider reviewing.",
                    "action": "Review subscriptions",
                    "priority": 2,
                })

    # ── 4. Income vs expense ratio ──
    income_result = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0))
        .where(Transaction.user_id == user_id)
        .where(and_(
            Transaction.type == TransactionType.income,
            Transaction.date >= current_month_start,
        ))
    )
    income = float(income_result.scalar())
    if income > 0 and current_total > 0:
        savings_rate = ((income - current_total) / income) * 100
        if savings_rate > 20:
            insights.append({
                "type": "success",
                "icon": "💰",
                "title": f"Saving {savings_rate:.0f}% of income",
                "description": f"You're on track to save ${income - current_total:,.2f} this month. Keep it up!",
                "priority": 5,
            })
        elif savings_rate < 5:
            insights.append({
                "type": "warning",
                "icon": "💸",
                "title": "Low savings rate",
                "description": f"You're only saving {max(0, savings_rate):.0f}% of your income this month. Aim for at least 20%.",
                "action": "Create a savings plan",
                "priority": 1,
            })

    # Sort by priority
    insights.sort(key=lambda i: i.get("priority", 5))

    return {
        "insights": insights,
        "count": len(insights),
        "generatedAt": today.isoformat(),
    }


# ── Subscription Optimization ────────────────────────────

async def subscription_recommendations(db: AsyncSession, user_id: str) -> dict:
    """
    Analyze subscriptions and recommend optimizations.
    """
    result = await db.execute(
        select(Subscription)
        .where(Subscription.user_id == user_id)
        .where(Subscription.active == True)
        .order_by(Subscription.cost.desc())
    )
    subs = result.scalars().all()

    recommendations = []
    total_monthly = sum(s.cost for s in subs)
    annual_cost = total_monthly * 12

    for sub in subs:
        # Check if renewal date is approaching
        if isinstance(sub.renewal_date, date):
            days_until = (sub.renewal_date - date.today()).days
            if 0 < days_until <= 7:
                recommendations.append({
                    "subscription": sub.name,
                    "cost": sub.cost,
                    "type": "renewal_alert",
                    "icon": "🗓️",
                    "message": f"{sub.name} renews in {days_until} day{'s' if days_until > 1 else ''} (${sub.cost:.2f}/mo)",
                    "priority": 1,
                })

        # Flag expensive subscriptions
        if sub.cost > total_monthly * 0.3:
            recommendations.append({
                "subscription": sub.name,
                "cost": sub.cost,
                "type": "high_cost",
                "icon": "💰",
                "message": f"{sub.name} is ${sub.cost:.2f}/mo — {sub.cost/total_monthly*100:.0f}% of your subscription spending",
                "priority": 2,
            })

    recommendations.sort(key=lambda r: r.get("priority", 5))

    return {
        "recommendations": recommendations,
        "totalMonthly": round(total_monthly, 2),
        "totalAnnual": round(annual_cost, 2),
        "subscriptionCount": len(subs),
    }
