"""
portfolio_calculator.py — Simulates a day-trading account to calculate
accurate portfolio-level P&L from individual trade outcomes.

Instead of naively summing per-trade P&L percentages (which ignores
concurrent capital splits), this module reconstructs the capital timeline:

1. Sorts trades by open/close timestamps.
2. Allocates capital per-trade using risk-based sizing:
     shares = (total_capital × risk_pct) / |entry − stop|
     position_value = shares × entry_price
   Capped at available capital if insufficient.
3. When a trade closes, frees the allocated capital ± realized P&L.
4. For partial exits (TP1 hit), frees 50% of position capital.
5. Portfolio return = (ending_capital − starting_capital) / starting_capital.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger(__name__)

# Statuses where the position is fully closed
_TERMINAL = {"tp2_hit", "stopped", "breakeven", "trailed_out", "tp1_locked", "expired"}

# Terminal statuses that definitively went through TP1 first.
# tp2_hit:    TP1 → TP2 (runner hit second target)
# tp1_locked: TP1 → stop trailed to TP1 → stopped (runner locked in TP1)
_TP1_FIRST = {"tp2_hit", "tp1_locked"}


def calculate_portfolio_return(
    outcomes: list[dict[str, Any]],
    starting_capital: float = 10_000.0,
    risk_per_trade_pct: float = 0.5,
) -> dict[str, Any]:
    """Simulate account-level P&L from a day's trade outcomes.

    Parameters
    ----------
    outcomes:
        List of outcome dicts from ``load_outcomes_for_date()``.
        Each must have: entry_price, stop_price, tp1_price, exit_price,
        status, alerted_at, closed_at, pnl_pct.
    starting_capital:
        Notional account value at market open (or actual IBKR balance).
    risk_per_trade_pct:
        Percentage of *total* capital risked per trade (from risk.yaml).

    Returns
    -------
    dict with:
        portfolio_pnl_pct  — account-level return for the day
        portfolio_pnl_dollar — dollar P&L
        ending_capital     — final account value
        max_concurrent     — peak number of simultaneous positions
        max_capital_used   — peak capital allocation (dollar)
        capital_used_pct   — peak allocation as % of starting capital
    """
    if not outcomes:
        return _empty_result(starting_capital)

    # Build timeline events: (timestamp, event_type, trade_dict)
    events = _build_event_timeline(outcomes)
    if not events:
        return _fallback_average(outcomes, starting_capital)

    risk_amount = starting_capital * (risk_per_trade_pct / 100.0)
    available = starting_capital
    positions: dict[str, dict] = {}  # symbol → {shares, entry, allocated, half_sold}
    max_concurrent = 0
    max_capital_used = 0.0

    for ts, etype, trade in events:
        if etype == "open":
            sym = trade.get("symbol", "?")
            entry = float(trade.get("entry_price") or 0)
            stop = float(trade.get("stop_price") or 0)

            if entry <= 0 or stop <= 0:
                continue

            stop_distance = abs(entry - stop)
            if stop_distance <= 0:
                continue

            # Risk-based position sizing
            ideal_shares = risk_amount / stop_distance
            ideal_value = ideal_shares * entry

            # Cap at available capital
            actual_value = min(ideal_value, available)
            if actual_value <= 0:
                continue

            actual_shares = actual_value / entry
            available -= actual_value
            positions[sym] = {
                "shares": actual_shares,
                "entry": entry,
                "allocated": actual_value,
                "half_sold": False,
            }

            concurrent = len(positions)
            if concurrent > max_concurrent:
                max_concurrent = concurrent
            capital_in_use = starting_capital - available
            if capital_in_use > max_capital_used:
                max_capital_used = capital_in_use

        elif etype == "partial_close":
            sym = trade.get("symbol", "?")
            pos = positions.get(sym)
            if not pos or pos["half_sold"]:
                continue

            tp1 = float(trade.get("tp1_price") or 0)
            if tp1 <= 0:
                tp1 = pos["entry"]  # fallback

            half_shares = pos["shares"] / 2.0
            proceeds = half_shares * tp1
            available += proceeds
            pos["shares"] -= half_shares
            pos["allocated"] /= 2.0
            pos["half_sold"] = True

        elif etype == "close":
            sym = trade.get("symbol", "?")
            pos = positions.get(sym)
            if not pos:
                continue

            exit_price = float(trade.get("exit_price") or 0)
            if exit_price <= 0:
                exit_price = pos["entry"]  # no data = flat

            proceeds = pos["shares"] * exit_price
            available += proceeds
            del positions[sym]

    # Any positions still open (shouldn't happen at EOD but be safe)
    for sym, pos in list(positions.items()):
        available += pos["allocated"]  # return at cost

    ending = available
    pnl_dollar = ending - starting_capital
    pnl_pct = (pnl_dollar / starting_capital) * 100.0 if starting_capital > 0 else 0.0

    return {
        "portfolio_pnl_pct": round(pnl_pct, 2),
        "portfolio_pnl_dollar": round(pnl_dollar, 2),
        "ending_capital": round(ending, 2),
        "max_concurrent": max_concurrent,
        "max_capital_used": round(max_capital_used, 2),
        "capital_used_pct": round((max_capital_used / starting_capital) * 100, 1) if starting_capital > 0 else 0.0,
    }


# ── Helpers ──────────────────────────────────────────────────────────────

def _build_event_timeline(
    outcomes: list[dict[str, Any]],
) -> list[tuple[datetime, str, dict]]:
    """Convert outcome dicts into a sorted list of (timestamp, type, trade).

    Returns empty list if timestamps are missing (pre-feature trades).
    """
    events: list[tuple[datetime, str, dict]] = []
    usable = 0

    for o in outcomes:
        alerted_raw = o.get("alerted_at")
        closed_raw = o.get("closed_at")
        status = o.get("status", "open")

        if not alerted_raw:
            continue

        open_ts = _parse_ts(alerted_raw)
        if open_ts is None:
            continue
        usable += 1

        events.append((open_ts, "open", o))

        # ── Partial close for trades that went through TP1 ──────────
        # Day-trade rule: sell 50 % at TP1, let runner ride.
        # We need a partial_close event for every status where TP1
        # was hit before the final exit.

        if status == "tp1_hit":
            # Still-live runner — TP1 just hit, no full close yet.
            hit_raw = o.get("hit_at")
            if hit_raw:
                hit_ts = _parse_ts(hit_raw)
                if hit_ts and hit_ts > open_ts:
                    events.append((hit_ts, "partial_close", o))

        elif status in _TP1_FIRST:
            # Terminal statuses that definitively went through TP1.
            # Use tp1_hit_at if recorded, else estimate timing.
            tp1_ts = _infer_tp1_time(o, open_ts, closed_raw)
            if tp1_ts and tp1_ts > open_ts:
                events.append((tp1_ts, "partial_close", o))

        elif status == "expired":
            # Expired trades may or may not have been tp1_hit.
            # Only add partial if tp1_hit_at is explicitly recorded.
            tp1_hit_raw = o.get("tp1_hit_at")
            if tp1_hit_raw:
                tp1_ts = _parse_ts(tp1_hit_raw)
                if tp1_ts and tp1_ts > open_ts:
                    events.append((tp1_ts, "partial_close", o))

        # Full close
        if status in _TERMINAL and closed_raw:
            close_ts = _parse_ts(closed_raw)
            if close_ts and close_ts > open_ts:
                events.append((close_ts, "close", o))

    if usable == 0:
        return []

    # Sort by timestamp, then by event priority: close < partial < open
    # so capital is freed before being re-allocated in the same instant
    priority = {"close": 0, "partial_close": 1, "open": 2}
    events.sort(key=lambda e: (e[0], priority.get(e[1], 9)))
    return events


def _infer_tp1_time(
    outcome: dict[str, Any],
    open_ts: datetime,
    closed_raw: str | None,
) -> datetime | None:
    """Return the best-available timestamp for when TP1 was hit.

    Priority:
      1. ``tp1_hit_at`` (explicit column, set once when TP1 fires).
      2. Estimate: 1/3 of the way between alert and close.
         TP1 typically fires early in the trade's life.
    """
    tp1_raw = outcome.get("tp1_hit_at")
    if tp1_raw:
        ts = _parse_ts(tp1_raw)
        if ts and ts > open_ts:
            return ts

    # Fallback: estimate from open/close bracket
    if closed_raw:
        close_ts = _parse_ts(closed_raw)
        if close_ts and close_ts > open_ts:
            duration = (close_ts - open_ts).total_seconds()
            return open_ts + timedelta(seconds=duration / 3)

    return None


def _parse_ts(raw: str | None) -> datetime | None:
    """Parse an ISO timestamp string to a timezone-aware datetime."""
    if not raw:
        return None
    try:
        s = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _fallback_average(
    outcomes: list[dict[str, Any]],
    starting_capital: float,
) -> dict[str, Any]:
    """When timestamps are missing, fall back to simple average P&L.

    This handles legacy data that predates closed_at tracking.
    """
    pnls = []
    for o in outcomes:
        if o.get("status") not in ("open",):
            pnls.append(float(o.get("pnl_pct") or 0.0))

    if not pnls:
        return _empty_result(starting_capital)

    avg = sum(pnls) / len(pnls)  # conservative: average, not sum
    return {
        "portfolio_pnl_pct": round(avg, 2),
        "portfolio_pnl_dollar": round(starting_capital * avg / 100.0, 2),
        "ending_capital": round(starting_capital * (1 + avg / 100.0), 2),
        "max_concurrent": len(pnls),
        "max_capital_used": round(starting_capital, 2),
        "capital_used_pct": 100.0,
    }


def _empty_result(starting_capital: float) -> dict[str, Any]:
    return {
        "portfolio_pnl_pct": 0.0,
        "portfolio_pnl_dollar": 0.0,
        "ending_capital": round(starting_capital, 2),
        "max_concurrent": 0,
        "max_capital_used": 0.0,
        "capital_used_pct": 0.0,
    }
