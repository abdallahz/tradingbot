"""PnL calculation helpers shared across tracker, executor, and web layers."""
from __future__ import annotations


def pnl_pct(entry: float, price: float, side: str = "long") -> float:
    """Return single-leg P&L percentage."""
    if entry <= 0:
        return 0.0
    if side == "long":
        return ((price - entry) / entry) * 100
    return ((entry - price) / entry) * 100


def blended_pnl(trade: dict, exit_price: float, status: str = "") -> float:
    """Return blended P&L % honouring the 50% TP1 partial-fill rule.

    When TP1 is hit the first half is sold there; the runner targets TP2.
    Any terminal status that follows a TP1 hit blends both legs:
        blended = (pnl_tp1 + pnl_final) / 2

    Statuses that imply TP1 was already taken:
      tp2_hit     – runner hit TP2  (half @ TP1, half @ TP2)
      tp1_locked  – runner stopped at TP1 (both halves @ TP1)
      trailed_out – runner trailed out above entry
      stopped / breakeven / expired — only when prev status was tp1_hit

    tp1_hit itself is NOT blended: only the first half has been sold.
    """
    entry = float(trade.get("entry_price") or 0)
    if entry <= 0:
        return 0.0

    tp1 = float(trade.get("tp1_price") or 0)
    side = trade.get("side", "long")
    prev_status = trade.get("status", "open")

    pnl_final = pnl_pct(entry, exit_price, side)

    needs_blend = (
        status == "tp2_hit"
        or status == "tp1_locked"
        or (
            status in ("trailed_out", "stopped", "breakeven", "expired", "emergency_closed")
            and prev_status == "tp1_hit"
        )
    )

    if needs_blend and tp1 > 0:
        blended = (pnl_pct(entry, tp1, side) + pnl_final) / 2
        return round(blended, 2)

    return round(pnl_final, 2)
