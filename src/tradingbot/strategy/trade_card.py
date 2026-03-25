from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from tradingbot.models import Side, SymbolSnapshot, TradeCard


MIN_RR = 1.5   # Minimum reward:risk ratio (TP1 / stop distance)


def _assess_risk(stock: SymbolSnapshot, rr: float) -> str:
    """Return 'low', 'medium', or 'high' based on trade-quality factors.

    Penalty points (0-10 scale):
      +2  price < $3            (penny-stock territory)
      +1  price < $5            (small-cap friction)
      +2  spread > 1.5%         (execution risk)
      +1  spread > 0.8%         (wider than ideal)
      +2  dollar_volume < $500K (thin liquidity)
      +1  dollar_volume < $2M   (below-average liquidity)
      +1  R:R < 2.0             (marginal reward)
      +1  ATR/price > 5%        (highly volatile)

    Mapping:  0-2 -> low,  3-4 -> medium,  5+ -> high
    """
    penalty = 0

    # Price level
    if stock.price < 3:
        penalty += 2
    elif stock.price < 5:
        penalty += 1

    # Spread
    if stock.spread_pct > 1.5:
        penalty += 2
    elif stock.spread_pct > 0.8:
        penalty += 1

    # Dollar volume (liquidity)
    if stock.dollar_volume < 500_000:
        penalty += 2
    elif stock.dollar_volume < 2_000_000:
        penalty += 1

    # Risk/reward
    if rr < 2.0:
        penalty += 1

    # Volatility
    if stock.price > 0 and stock.atr / stock.price > 0.05:
        penalty += 1

    if penalty >= 5:
        return "high"
    if penalty >= 3:
        return "medium"
    return "low"


def build_trade_card(
    stock: SymbolSnapshot,
    side: Side,
    score: float,
    fixed_stop_pct: float,
    session_tag: Literal["morning", "midday", "close"],
    risk_per_trade_pct: float = 0.5,
    account_value: float = 25_000.0,
) -> TradeCard | None:
    """Build a level-based trade card.

    Entry  = current market price (what you'd actually get filled at).
    Stop   = key support − ATR buffer (long) or key resistance + ATR buffer (short).
    TP1    = key resistance (long) or key support (short) — the nearest real level.
    TP2    = TP1 + 1R extension.
    R:R    = (TP1 − entry) / (entry − stop).  Must be ≥ MIN_RR or the card is dropped.

    Position sizing: shares = (account_value × risk_per_trade_pct) / (entry − stop)
    so that each trade risks exactly the configured % of the account.

    The fixed_stop_pct is kept as a MAXIMUM stop distance — if the level-derived
    stop is wider than this %, we cap it so risk stays bounded.
    """
    entry = round(stock.price, 2)
    atr_buffer = stock.atr * 0.5 if stock.atr > 0 else entry * 0.005

    # Reject if key levels aren't set — a card with TP1=0 is nonsensical
    if side == "long" and stock.key_resistance <= 0:
        return None
    if side == "short" and stock.key_support <= 0:
        return None
    # Support/resistance must be on the correct side of price
    if side == "long" and stock.key_resistance <= entry:
        return None
    if side == "short" and stock.key_support >= entry:
        return None

    if side == "long":
        # Stop: just below the key support level
        level_stop = stock.key_support - atr_buffer
        # Cap stop so max risk never exceeds fixed_stop_pct
        max_stop = entry * (1.0 - fixed_stop_pct / 100.0)
        stop = round(max(level_stop, max_stop), 2)

        risk = entry - stop
        if risk <= 0:
            return None

        # TP1 = key resistance; TP2 = TP1 + 1R extension
        tp1 = round(stock.key_resistance, 2)
        tp2 = round(tp1 + risk, 2)
        invalidation = round(stock.pullback_low, 2)
    else:
        # Stop: just above the key resistance level
        level_stop = stock.key_resistance + atr_buffer
        # Cap stop so max risk never exceeds fixed_stop_pct
        max_stop = entry * (1.0 + fixed_stop_pct / 100.0)
        stop = round(min(level_stop, max_stop), 2)

        risk = stop - entry
        if risk <= 0:
            return None

        # TP1 = key support; TP2 = TP1 − 1R extension
        tp1 = round(stock.key_support, 2)
        tp2 = round(tp1 - risk, 2)
        invalidation = round(stock.pullback_high, 2)

    # R:R based on TP1 (the real level), not TP2
    reward = abs(tp1 - entry)
    rr = round(reward / risk, 2) if risk > 0 else 0.0
    if rr < MIN_RR:
        return None

    # ── Position sizing ──────────────────────────────────────────────
    # shares = (account_value × risk%) / risk_per_share
    risk_dollars = account_value * (risk_per_trade_pct / 100.0)
    position_size = int(risk_dollars / risk) if risk > 0 else 0
    # Safety cap: never exceed $10K notional or 50% of account
    max_notional = min(10_000.0, account_value * 0.5)
    if position_size * entry > max_notional and entry > 0:
        position_size = int(max_notional / entry)

    # Patterns: strictly technical/chart patterns
    patterns = list(getattr(stock, "patterns", []))

    # Reason: combine patterns with context (gap, relvol, etc.)
    reasons = []
    if hasattr(stock, "gap_pct") and abs(stock.gap_pct) >= 2.0:
        reasons.append(f"Gap: {stock.gap_pct:+.1f}%")
    if hasattr(stock, "relative_volume") and stock.relative_volume >= 1.5:
        reasons.append(f"RelVol: {stock.relative_volume:.1f}x")
    if patterns:
        reasons.extend(patterns)
    if not reasons:
        if side == "long":
            reasons = ["volume_spike", "ema9_20_hold", "vwap_reclaim", "pullback_entry"]
        else:
            reasons = ["volume_spike", "ema9_20_reject", "vwap_break", "pullback_entry"]

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
        scan_price=entry,
        key_support=round(stock.key_support, 2),
        key_resistance=round(stock.key_resistance, 2),
        risk_level=_assess_risk(stock, rr),
        position_size=position_size,
    )
