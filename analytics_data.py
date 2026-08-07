"""Analytics aggregations — re-exports from services.analytics for backward compatibility."""

from services.analytics import (  # noqa: F401
    ANALYTICS_RANGES,
    aggregate_category_spending,
    aggregate_spending_trend,
    aggregate_trip_spending,
    build_user_analytics,
    parse_range_key,
    range_cutoff,
    visible_expense_filter,
)


def filter_expenses_by_range(expenses, range_key: str):
    """Legacy helper for in-memory filtering (prefer SQL aggregations)."""
    cutoff = range_cutoff(parse_range_key(range_key))
    if cutoff is None:
        return list(expenses)
    filtered = []
    for expense in expenses:
        created = expense.created_at
        if created is None:
            filtered.append(expense)
        elif created.replace(tzinfo=None) >= cutoff:
            filtered.append(expense)
    return filtered
