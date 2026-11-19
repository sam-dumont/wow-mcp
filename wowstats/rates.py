"""Per-hour rates, one rounding for every answer: the MCP's tools, the
planner and the pages say the same XP/hour for the same data (int() cut
9826.9 to 9826 in one tool while the planner said 9827)."""

from __future__ import annotations


def per_hour(amount, seconds) -> int | None:
    """`amount` per hour over `seconds`, rounded to a whole number; None
    without time to divide by."""
    try:
        a, s = float(amount), float(seconds)
    except (TypeError, ValueError):
        return None
    return round(a / s * 3600) if s > 0 else None
