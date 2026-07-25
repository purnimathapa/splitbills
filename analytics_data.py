"""Analytics aggregations with optional date range."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

ANALYTICS_RANGES = {
    "30": 30,
    "90": 90,
    "all": None,
}


def parse_range_key(raw: str | None) -> str:
    key = (raw or "30").strip().lower()
    return key if key in ANALYTICS_RANGES else "30"


def filter_expenses_by_range(expenses, range_key: str):
    days = ANALYTICS_RANGES[range_key]
    if days is None:
        return list(expenses)
    cutoff = datetime.utcnow() - timedelta(days=days)
    filtered = []
    for expense in expenses:
        created = expense.created_at
        if created is None:
            filtered.append(expense)
        elif created.replace(tzinfo=None) >= cutoff:
            filtered.append(expense)
    return filtered


def aggregate_category_spending(expenses, trip_names: dict[int, str]):
    totals = defaultdict(float)
    for expense in expenses:
        label = (expense.category or "").strip() or "General"
        totals[label] += expense.amount or 0
    labels = sorted(totals.keys(), key=lambda k: totals[k], reverse=True)
    return labels, [round(totals[l], 2) for l in labels]


def aggregate_trip_spending(expenses, trip_names: dict[int, str]):
    totals = defaultdict(float)
    for expense in expenses:
        name = trip_names.get(expense.trip_id, "Group")
        totals[name] += expense.amount or 0
    labels = sorted(totals.keys(), key=lambda k: totals[k], reverse=True)
    return labels, [round(totals[l], 2) for l in labels]


def aggregate_spending_trend(expenses):
    """Daily totals for line chart (sorted oldest → newest)."""
    totals = defaultdict(float)
    for expense in expenses:
        if expense.created_at:
            key = expense.created_at.strftime("%Y-%m-%d")
        else:
            key = "Unknown"
        totals[key] += expense.amount or 0
    labels = sorted(k for k in totals.keys() if k != "Unknown")
    if "Unknown" in totals:
        labels.append("Unknown")
    return labels, [round(totals[l], 2) for l in labels]
