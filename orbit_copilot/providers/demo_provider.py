"""
Demo mode — handles chat responses when no valid Anthropic API key is configured.

Uses keyword matching to detect intent and executes tools directly,
formatting results into readable responses. This allows the full
chat UI to work without an LLM for development and demonstration.
"""
from __future__ import annotations
import json
import re
from datetime import date, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from .tools import execute_tool
from .analytics_engine import detect_anomalies, generate_insights, subscription_recommendations


# ── Intent patterns ────────────────────────────────────────

INTENT_PATTERNS = [
    {
        "intent": "forecast",
        "patterns": [r"predict", r"forecast", r"future\s*(spend|cost)", r"next\s*(month|week)"],
        "tool": "__forecast",
        "input": {},
    },
    {
        "intent": "get_subscriptions",
        "patterns": [r"subscri", r"recurring", r"monthly\s*(payment|bill|charge)"],
        "tool": "get_subscriptions",
        "input": {"active_only": True},
    },
    {
        "intent": "spending_summary",
        "patterns": [r"spend", r"summary", r"overview", r"how\s*much", r"total"],
        "tool": "get_spending_summary",
        "input": {"period": "month"},
    },
    {
        "intent": "category_breakdown",
        "patterns": [r"categor", r"breakdown", r"where.*money", r"expense.*by"],
        "tool": "get_category_breakdown",
        "input": {},
    },
    {
        "intent": "get_transactions",
        "patterns": [r"transaction", r"recent", r"history", r"show.*purchas", r"list.*expens"],
        "tool": "get_transactions",
        "input": {"limit": 10},
    },
    {
        "intent": "create_transaction",
        "patterns": [r"add.*(expense|transaction|income)", r"log.*(expense|purchase)", r"spent.*\$", r"paid.*\$", r"bought"],
        "tool": "__create_tx",
        "input": {},
    },
    {
        "intent": "cancel_subscription",
        "patterns": [r"cancel.*subscri", r"remove.*subscri", r"stop.*subscri"],
        "tool": None,
        "input": {},
    },
    {
        "intent": "anomalies",
        "patterns": [r"anomal", r"unusual", r"suspicious", r"weird", r"strange", r"spike", r"flagged"],
        "tool": "__anomalies",
        "input": {},
    },
    {
        "intent": "savings",
        "patterns": [r"saving", r"budget", r"income.*vs", r"net\b", r"how.*doing", r"financial.*health"],
        "tool": "__insights",
        "input": {},
    },
    {
        "intent": "sub_optimize",
        "patterns": [r"optimi", r"too.*much.*subscri", r"cut.*cost", r"save.*subscri", r"reduce"],
        "tool": "__sub_optimize",
        "input": {},
    },
]


def is_demo_mode(api_key: str) -> bool:
    """Check if we should use demo mode (no valid API key)."""
    if not api_key:
        return True
    if api_key.startswith("sk-ant-") and len(api_key) > 20:
        return False
    # Likely a placeholder/test key
    return True


def detect_intent(message: str) -> dict | None:
    """Match user message to an intent using keyword patterns."""
    lower = message.lower()
    for pattern_group in INTENT_PATTERNS:
        for pattern in pattern_group["patterns"]:
            if re.search(pattern, lower):
                return pattern_group
    return None


def _parse_time_range(message: str) -> dict:
    """Extract a date range from natural language like 'past 3 days', 'last week', etc."""
    lower = message.lower()
    today = date.today()

    # "past/last N days/weeks/months"
    m = re.search(r'(?:past|last|previous)\s+(\d+)\s+(day|week|month)', lower)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit == 'day':
            start = today - timedelta(days=n)
        elif unit == 'week':
            start = today - timedelta(weeks=n)
        else:
            start = today - timedelta(days=n * 30)
        return {
            "date_from": start.isoformat(),
            "date_to": today.isoformat(),
            "period": f"{n} {unit}{'s' if n > 1 else ''}",
        }

    # "today"
    if re.search(r'\btoday\b', lower):
        return {"date_from": today.isoformat(), "date_to": today.isoformat(), "period": "today"}

    # "yesterday"
    if re.search(r'\byesterday\b', lower):
        y = today - timedelta(days=1)
        return {"date_from": y.isoformat(), "date_to": y.isoformat(), "period": "yesterday"}

    # "this week" / "last week"
    if re.search(r'\bthis\s+week\b', lower):
        start = today - timedelta(days=today.weekday())
        return {"date_from": start.isoformat(), "date_to": today.isoformat(), "period": "this week"}
    if re.search(r'\blast\s+week\b', lower):
        start = today - timedelta(days=today.weekday() + 7)
        end = start + timedelta(days=6)
        return {"date_from": start.isoformat(), "date_to": end.isoformat(), "period": "last week"}

    # No match — return empty (caller uses default)
    return {}


def _format_transactions(data: dict) -> str:
    """Format transaction data into a readable response."""
    txns = data.get("transactions", [])
    if not txns:
        return "No transactions found for the given criteria."

    lines = [f"📋 Here are your recent transactions ({data['count']} found):\n"]
    for tx in txns:
        emoji = "💰" if tx["type"] == "income" else "💸"
        sign = "+" if tx["type"] == "income" else "-"
        lines.append(f"{emoji} {tx['description']} — {sign}${tx['amount']:.2f}")
        lines.append(f"    📁 {tx['category']}  ·  📅 {tx['date']}\n")
    return "\n".join(lines)


def _format_subscriptions(data: dict) -> str:
    """Format subscription data into a readable response."""
    subs = data.get("subscriptions", [])
    if not subs:
        return "You don't have any active subscriptions."

    lines = [f"🔄 Your Active Subscriptions ({data['count']} total):\n"]
    for sub in subs:
        status = "✅" if sub["active"] else "❌"
        lines.append(f"{status} {sub['name']} — ${sub['cost']:.2f}/month")
        lines.append(f"    📁 {sub['category']}  ·  🗓️ Renews: {sub['renewalDate']}\n")
    lines.append(f"💵 Total Monthly Cost: ${data['totalMonthlyCost']:.2f}")
    return "\n".join(lines)


def _format_spending_summary(data: dict) -> str:
    """Format spending summary into a readable response."""
    lines = [f"📊 Spending Summary ({data['period'].title()})\n"]
    lines.append(f"📅 {data['dateRange']['from']} → {data['dateRange']['to']}\n")
    lines.append(f"💰 Total Income:    ${data['totalIncome']:,.2f}")
    lines.append(f"💸 Total Expenses:  ${data['totalExpenses']:,.2f}")
    net = data['netBalance']
    emoji = "📈" if net >= 0 else "📉"
    lines.append(f"{emoji} Net Balance:     ${net:,.2f}")
    lines.append(f"📝 Transactions:    {data['transactionCount']}")
    if net >= 0:
        lines.append(f"\n✅ You're in the green! Keep it up.")
    else:
        lines.append(f"\n⚠️ You're spending more than you're earning this period.")
    return "\n".join(lines)


def _format_category_breakdown(data: dict) -> str:
    """Format category breakdown into a readable response."""
    cats = data.get("categories", [])
    if not cats:
        return "No expense data found for the given period."

    lines = [f"📈 Expense Breakdown by Category\n"]
    lines.append(f"📅 {data['dateRange']['from']} → {data['dateRange']['to']}\n")

    bar_chars = "█"
    for cat in cats:
        bar_len = max(1, int(cat["percentage"] / 5))
        bar = bar_chars * bar_len
        lines.append(f"{cat['category']} — ${cat['total']:,.2f} ({cat['percentage']}%)")
        lines.append(f"  {bar}\n")

    lines.append(f"💵 Grand Total: ${data['grandTotal']:,.2f}")
    return "\n".join(lines)


FORMATTERS = {
    "get_transactions": _format_transactions,
    "get_subscriptions": _format_subscriptions,
    "get_spending_summary": _format_spending_summary,
    "get_category_breakdown": _format_category_breakdown,
}


def _format_anomalies(data: dict) -> str:
    """Format anomaly detection results."""
    anomalies = data.get("anomalies", [])
    if not anomalies:
        return "✅ All clear! No unusual transactions detected in the past 60 days."

    lines = [f"🔍 Found {data['count']} unusual transaction{'s' if data['count'] != 1 else ''}:\n"]
    for a in anomalies:
        severity = "🔴" if a.get("severity") == "high" else "🟡"
        lines.append(f"{severity} {a['description']} — ${a['amount']:.2f}")
        lines.append(f"    {a['reason']}\n")
    return "\n".join(lines)


def _format_insights(data: dict) -> str:
    """Format spending insights."""
    insights = data.get("insights", [])
    if not insights:
        return "📊 Not enough data yet to generate insights. Keep tracking your finances!"

    lines = [f"💡 Here are your financial insights:\n"]
    for ins in insights:
        lines.append(f"{ins.get('icon', '📌')} {ins['title']}")
        lines.append(f"    {ins['description']}\n")
    return "\n".join(lines)


def _format_sub_optimization(data: dict) -> str:
    """Format subscription optimization recommendations."""
    recs = data.get("recommendations", [])
    total = data.get("totalMonthly", 0)
    annual = data.get("totalAnnual", 0)

    lines = [f"🔧 Subscription Analysis ({data.get('subscriptionCount', 0)} active):\n"]
    lines.append(f"💵 Monthly: ${total:.2f}  ·  Annual: ${annual:,.2f}\n")

    if recs:
        lines.append("Recommendations:\n")
        for r in recs:
            lines.append(f"{r.get('icon', '📌')} {r['message']}\n")
    else:
        lines.append("✅ Your subscriptions look well-managed!")

    return "\n".join(lines)


def _format_forecast(data: dict) -> str:
    """Format spending forecast into a readable response."""
    trend = data.get("trend", "stable")
    monthly = data.get("predictedMonthlyTotal", 0)
    daily = data.get("dailyAverage", 0)

    emoji = "📈" if trend == "increasing" else "📉" if trend == "decreasing" else "➡️"
    
    lines = [
        f"🔮 Spending Forecast (Next 30 Days)\n",
        f"💵 Projected Monthly Total: ${monthly:,.2f}",
        f"📅 Daily Average Spend:     ${daily:,.2f}",
        f"{emoji} Spending Trend:          {trend.title()}",
    ]
    
    if trend == "increasing":
        lines.append("\n⚠️ Your spending is trending upward. Watch your budget!")
    elif trend == "decreasing":
        lines.append("\n✅ Great job! Your spending is trending downward.")
        
    return "\n".join(lines)


async def handle_demo_message(message: str, db: AsyncSession, user_id: str) -> tuple:
    """
    Handle a chat message in demo mode (no LLM).
    
    Returns (response_text, tool_calls_log).
    """
    intent = detect_intent(message)

    if not intent or not intent["tool"]:
        # Default helpful response
        return (
            "👋 Hi! I'm Orbit AI running in demo mode (no API key configured).\n\n"
            "I can help you explore your financial data! Try:\n\n"
            "  📊  \"Show my spending summary\"\n"
            "  💳  \"Show recent transactions\"\n"
            "  🔄  \"List my subscriptions\"\n"
            "  📈  \"Category breakdown\"\n"
            "  🔍  \"Any unusual spending?\"\n"
            "  💡  \"How are my savings?\"\n"
            "  🔧  \"Optimize my subscriptions\"\n\n"
            "💡 To enable full AI conversations, add your Anthropic API key to backend/.env",
            [],
        )

    tool_name = intent["tool"]
    tool_input = dict(intent["input"])  # copy so we don't mutate the pattern

    # ── Inject date range for time-aware intents ──
    time_range = _parse_time_range(message)
    if time_range and tool_name in ("get_spending_summary", "get_transactions"):
        tool_input["date_from"] = time_range["date_from"]
        tool_input["date_to"] = time_range["date_to"]
        if tool_name == "get_spending_summary":
            tool_input["period"] = time_range.get("period", "custom")

    # ── Analytics-engine intents (not regular tools) ──
    if tool_name == "__anomalies":
        result_data = await detect_anomalies(db, user_id)
        return _format_anomalies(result_data), []

    if tool_name == "__insights":
        result_data = await generate_insights(db, user_id)
        return _format_insights(result_data), []

    if tool_name == "__sub_optimize":
        result_data = await subscription_recommendations(db, user_id)
        return _format_sub_optimization(result_data), []

    if tool_name == "__forecast":
        # Hardcoding to 30 days as that's the default and what the UI shows
        from .analytics_engine import forecast_spending
        result_data = await forecast_spending(db, user_id, forecast_days=30)
        return _format_forecast(result_data), []

    if tool_name == "__create_tx":
        # Parse amount from message: look for $XX.XX or just numbers
        amount_match = re.search(r"\$?([\d,]+(?:\.\d{1,2})?)", message)
        amount = float(amount_match.group(1).replace(",", "")) if amount_match else 0

        # Extract description — remove amount and common filler words
        desc = re.sub(r"\$?[\d,]+(?:\.\d{1,2})?", "", message)
        desc = re.sub(r"\b(i |spent|paid|on|for|bought|add|log|expense|transaction)\b", "", desc, flags=re.I)
        desc = " ".join(desc.split()).strip(" .,!?") or "Transaction"

        # Guess category from keywords
        cat = "Shopping"
        lower = message.lower()
        if any(w in lower for w in ["food", "lunch", "dinner", "coffee", "restaurant", "eat", "meal"]):
            cat = "Food"
        elif any(w in lower for w in ["uber", "lyft", "gas", "taxi", "bus", "train", "transport"]):
            cat = "Transport"
        elif any(w in lower for w in ["electric", "water", "internet", "phone", "util"]):
            cat = "Utilities"
        elif any(w in lower for w in ["movie", "game", "netflix", "spotify", "concert", "entertainment"]):
            cat = "Entertainment"
        elif any(w in lower for w in ["income", "salary", "earned", "received", "paid me"]):
            cat = "Income"

        tx_type = "income" if cat == "Income" else "expense"

        if amount > 0:
            result_str = await execute_tool("create_transaction", {
                "description": desc.title() if len(desc) > 1 else "Expense",
                "amount": amount,
                "category": cat,
                "type": tx_type,
            }, db, user_id)
            result_data = json.loads(result_str)
            emoji = "💰" if tx_type == "income" else "💸"
            return (
                f"✅ Transaction logged!\n\n"
                f"{emoji} {result_data.get('description', desc)} — ${amount:.2f}\n"
                f"📁 {cat}  ·  📅 {result_data.get('date', 'Today')}",
                [{"name": "create_transaction", "input": {"amount": amount, "description": desc}, "result": result_data}],
            )
        else:
            return "I couldn't find an amount in your message. Try something like: \"I spent $25 on lunch\"", []

    # ── Regular tool intents ──
    result_str = await execute_tool(tool_name, tool_input, db, user_id)
    result_data = json.loads(result_str)

    tool_calls_log = [{
        "name": tool_name,
        "input": tool_input,
        "result": result_data,
    }]

    formatter = FORMATTERS.get(tool_name)
    if formatter:
        response_text = formatter(result_data)
    else:
        response_text = json.dumps(result_data, indent=2)

    return response_text, tool_calls_log

