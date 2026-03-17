from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from tradingbot.models import Side, SymbolSnapshot, TradeCard


def build_trade_card(
    stock: SymbolSnapshot,
    side: Side,
    score: float,
    fixed_stop_pct: float,
    session_tag: Literal["morning", "midday"],
) -> TradeCard:
    if side == "long":
        entry = round(stock.reclaim_level * 1.0005, 2)
        stop = round(entry * (1.0 - fixed_stop_pct / 100.0), 2)
        risk = entry - stop
        tp1 = round(entry + risk, 2)
        tp2 = round(entry + 2 * risk, 2)
        invalidation = round(stock.pullback_low, 2)
        reasons = ["volume_spike", "ema9_20_hold", "vwap_reclaim", "pullback_entry"]
    else:
        entry = round(stock.reclaim_level * 0.9995, 2)
        stop = round(entry * (1.0 + fixed_stop_pct / 100.0), 2)
        risk = stop - entry
        tp1 = round(entry - risk, 2)
        tp2 = round(entry - 2 * risk, 2)
        invalidation = round(stock.pullback_high, 2)
        reasons = ["volume_spike", "ema9_20_reject", "vwap_break", "pullback_entry"]

    # True R:R = (tp2 - entry) / (entry - stop) for long, symmetric for short
    rr = round(abs(tp2 - entry) / risk, 2) if risk > 0 else 0.0

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
        risk_reward=rr,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        scan_price=round(stock.price, 2),  # price at scan time; levels derived from this
    )
