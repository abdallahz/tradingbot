from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from tradingbot.models import SymbolSnapshot, TradeCard
from tradingbot.data.etf_metadata import get_leverage_factor


MIN_RR = 1.5   # Minimum reward:risk ratio (TP1 / stop distance)
MAX_RR = 3.0   # Maximum R:R — inflated targets look great but never hit intraday


def _assess_risk(stock: SymbolSnapshot, rr: float) -> str:
    """Return 'low', 'medium', or 'high' based on trade-quality factors.

    Penalty points (0-14 scale):
      +2  price < $3              (penny-stock territory)
      +1  price < $5              (small-cap friction)
      +2  spread > 1.5%           (execution risk)
      +1  spread > 0.8%           (wider than ideal)
      +2  dollar_volume < $500K   (thin liquidity)
      +1  dollar_volume < $2M     (below-average liquidity)
      +1  R:R < 2.0               (marginal reward)
      +2  ATR/price > 5%          (highly volatile)
      +1  ATR/price > 3%          (above-average volatility)
      +2  gap_pct > 8%            (large gaps fade frequently)
      +1  gap_pct > 4%            (moderate gap fade risk)
      +1  relative_volume > 3.0   (frenzy buying, likely to reverse)

    Mapping:  0-2 -> low,  3-4 -> medium,  5+ -> high

    History: Apr 6-8 data showed 22/23 trades classified 'low' despite
    including IONQ, RGTI, SOXL, TQQQ — all speculative names with big
    gaps.  The old 5% ATR threshold was too high (most volatile stocks
    sit at 3-4%) and gap size was not considered at all.
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

    # Volatility — tiered (old threshold of 5% was too high; most
    # speculative stocks sit at 3-4% ATR/price and were slipping through)
    if stock.price > 0 and stock.atr > 0:
        atr_pct = stock.atr / stock.price
        if atr_pct > 0.05:
            penalty += 2
        elif atr_pct > 0.03:
            penalty += 1

    # Gap size — large pre-market gaps fade more often.
    # Apr 6-8 data: SOXL +20.6%, TQQQ +10.2%, INTC +9.8% all failed.
    # The only winner (AAOX +8.7%) was the exception, not the rule.
    if stock.gap_pct > 8:
        penalty += 2
    elif stock.gap_pct > 4:
        penalty += 1

    # Volume frenzy — extremely high relative volume (>3×) indicates
    # speculative pile-in that tends to reverse intraday.
    if stock.relative_volume > 3.0:
        penalty += 1

    if penalty >= 5:
        return "high"
    if penalty >= 3:
        return "medium"
    return "low"


def build_trade_card(
    stock: SymbolSnapshot,
    score: float,
    fixed_stop_pct: float,
    session_tag: Literal["morning", "midday", "close"],
    risk_per_trade_pct: float = 0.5,
    account_value: float = 25_000.0,
    stop_buffer_multiplier: float = 1.0,
    stop_pct_by_risk: dict[str, float] | None = None,
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

    Risk-tiered stops: if stop_pct_by_risk is provided (e.g. {"low": 1.5, "medium": 2.5,
    "high": 2.5}), the max stop % is adjusted by the trade's risk level.  Low-risk trades
    (boring, low volatility) get tighter stops since they lack the momentum to reach targets.

    stop_buffer_multiplier widens the ATR buffer in weak markets (from MarketGuard):
      green=1.0, yellow=1.5, red=2.0 (red halts entries, so effectively 1.0-1.5).
    """
    entry = round(stock.price, 2)
    atr_buffer = stock.atr * 0.5 * stop_buffer_multiplier if stock.atr > 0 else entry * 0.005

    # Reject if key levels aren't set — a card with TP1=0 is nonsensical
    if stock.key_resistance <= 0:
        return None
    # Resistance must be above price for a long trade
    if stock.key_resistance <= entry:
        return None

    # Maximum realistic TP distance for intraday trades.
    # Daily S/R levels (5-day range) can be very far from current price,
    # creating unreachable targets that inflate R:R on paper but never hit.
    # Cap at the tighter of 2× daily ATR or 3% of stock price.
    # (Previously 3×ATR / 6% — analysis of Apr 6-8 showed 15/23 trades
    #  hit the 6% cap with 0% win rate on capped trades.)
    if stock.atr > 0:
        max_tp_dist = min(stock.atr * 2, entry * 0.03)
    else:
        max_tp_dist = entry * 0.03

    # Long-only: stop below support, targets above entry
    level_stop = stock.key_support - atr_buffer
    # Cap stop so max risk never exceeds fixed_stop_pct
    max_stop = entry * (1.0 - fixed_stop_pct / 100.0)
    stop = round(max(level_stop, max_stop), 2)

    risk = entry - stop
    if risk <= 0:
        return None

    # ── ATR minimum stop distance ──────────────────────────────────
    # A stop tighter than 0.5 × ATR will almost certainly be clipped by
    # normal intraday noise.  Widen the stop if necessary, but re-check
    # that the fixed_stop_pct cap isn't violated.
    if stock.atr > 0:
        min_stop_dist = stock.atr * 0.5
        if risk < min_stop_dist:
            widened_stop = round(entry - min_stop_dist, 2)
            # Only widen if it doesn't breach the max-risk cap
            if widened_stop >= max_stop:
                stop = widened_stop
                risk = entry - stop

    # TP1 = key resistance, capped at max_tp_dist above entry
    raw_tp1 = stock.key_resistance
    tp1 = round(min(raw_tp1, entry + max_tp_dist), 2)
    tp2 = round(tp1 + risk, 2)
    invalidation = round(stock.pullback_low, 2)

    # R:R based on TP1 (the real level), not TP2
    reward = abs(tp1 - entry)
    rr = round(reward / risk, 2) if risk > 0 else 0.0
    if rr < MIN_RR:
        return None

    # ── Risk-tiered stop adjustment ────────────────────────────────
    # Assess risk level first, then apply tighter stop if configured.
    # Low-risk trades (low volatility, wide spread, thin liquidity)
    # historically show 36% WR and +0.08% avg PnL — a tighter stop
    # (1.5% vs 2.5%) limits loss magnitude without hurting upside.
    risk_level = _assess_risk(stock, rr)

    if stop_pct_by_risk:
        tiered_stop_pct = stop_pct_by_risk.get(risk_level, fixed_stop_pct)
        if tiered_stop_pct < fixed_stop_pct:
            # Recalculate stop with tighter cap
            tiered_max_stop = entry * (1.0 - tiered_stop_pct / 100.0)
            tiered_stop = round(max(level_stop, tiered_max_stop), 2)

            # Apply ATR minimum floor again
            tiered_risk = entry - tiered_stop
            if stock.atr > 0 and tiered_risk > 0:
                min_stop_dist = stock.atr * 0.5
                if tiered_risk < min_stop_dist:
                    widened = round(entry - min_stop_dist, 2)
                    if widened >= tiered_max_stop:
                        tiered_stop = widened
                        tiered_risk = entry - tiered_stop

            if tiered_risk > 0:
                stop = tiered_stop
                risk = tiered_risk
                # Recalculate TP2 and R:R with new risk
                tp2 = round(tp1 + risk, 2)
                rr = round(reward / risk, 2)
                if rr < MIN_RR:
                    return None

    # ── R:R cap — prevent unrealistic targets ──────────────────────
    # Apr 6-8 data: avg R:R was 3.1 (all losers).  Mar 30-31 winners
    # averaged R:R 2.3.  When R:R > 3.0 the stock must move +4-6%
    # without a -1.5% pullback — nearly impossible intraday.
    # Cap TP1 so R:R never exceeds MAX_RR.
    if rr > MAX_RR:
        reward = risk * MAX_RR
        tp1 = round(entry + reward, 2)
        tp2 = round(tp1 + risk, 2)
        rr = MAX_RR

    # ── Position sizing ──────────────────────────────────────────────
    # Adjust risk budget for leveraged ETFs — a 3x ETF moves 3x as far
    # so we reduce position size proportionally to keep real exposure equal.
    leverage = abs(get_leverage_factor(stock.symbol))
    adjusted_risk_pct = risk_per_trade_pct / leverage if leverage > 1 else risk_per_trade_pct

    # shares = (account_value × risk%) / risk_per_share
    risk_dollars = account_value * (adjusted_risk_pct / 100.0)
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
        reasons = ["volume_spike", "ema9_20_hold", "vwap_reclaim", "pullback_entry"]

    return TradeCard(
        symbol=stock.symbol,
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
        risk_level=risk_level,
        position_size=position_size,
    )
