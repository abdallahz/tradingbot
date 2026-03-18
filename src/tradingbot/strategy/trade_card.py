from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from tradingbot.models import Side, SymbolSnapshot, TradeCard


MIN_RR = 2.0

def build_trade_card(
    stock: SymbolSnapshot,
    side: Side,
    score: float,
    fixed_stop_pct: float,
    session_tag: Literal["morning", "midday"],
) -> TradeCard:
    # Entry = current market price (what you'd actually get filled at)
    entry = round(stock.price, 2)
    if side == "long":
        stop = round(entry * (1.0 - fixed_stop_pct / 100.0), 2)
        risk = entry - stop
        tp1 = round(entry + risk, 2)
        tp2 = round(entry + 2 * risk, 2)
        invalidation = round(stock.pullback_low, 2)
    else:
        stop = round(entry * (1.0 + fixed_stop_pct / 100.0), 2)
        risk = stop - entry
        tp1 = round(entry - risk, 2)
        tp2 = round(entry - 2 * risk, 2)
        invalidation = round(stock.pullback_high, 2)


    # Patterns: strictly technical/chart patterns
    patterns = list(getattr(stock, "patterns", []))

    # Reason: combine patterns with context (gap, relvol, etc.)
    reasons = []
    if hasattr(stock, "gap_pct") and abs(stock.gap_pct) >= 2.0:
        reasons.append(f"Gap: {stock.gap_pct:+.1f}%")
    if hasattr(stock, "relative_volume") and stock.relative_volume >= 1.5:
        reasons.append(f"RelVol: {stock.relative_volume:.1f}x")
    # Add patterns as part of the reason, but not the only reason
    if patterns:
        reasons.extend(patterns)
    # Fallback for legacy/no patterns
    if not reasons:
        if side == "long":
            reasons = ["volume_spike", "ema9_20_hold", "vwap_reclaim", "pullback_entry"]
        else:
            reasons = ["volume_spike", "ema9_20_reject", "vwap_break", "pullback_entry"]

    # True R:R = (tp2 - entry) / (entry - stop) for long, symmetric for short
    rr = round(abs(tp2 - entry) / risk, 2) if risk > 0 else 0.0
    if rr < MIN_RR:
        # Option 1: Return None to signal this trade should be dropped
        return None
    return TradeCard(
        symbol=stock.symbol,
        side=side,
        score=round(score, 2),
        entry_price=entry,
        stop_price=stop,
        tp1_price=tp1,
        tp2_price=tp2,
        invalidation_price=invalidation,
        session_tag=session_tag,
        reason=reasons,
        patterns=patterns,
        risk_reward=rr,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        scan_price=round(stock.price, 2),  # price at scan time; levels derived from this
    )
