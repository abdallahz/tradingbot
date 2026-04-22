"""Pullback Re-Entry Detector

Identifies stocks that:
  1. Ran up (gap or intraday) past our normal entry window, AND
  2. Pulled back to a reasonable level (near VWAP / EMA / support), AND
  3. Are now showing signs of recovery (reclaim VWAP, hold EMA, volume).

This gives us a second-chance entry at a much better price than chasing
the initial move.  The classic pattern is:

    Gap-up → run to HOD → profit-taking dip → bounce off VWAP/EMA → 
    continuation to new highs

The key is that the pullback must be "constructive" — not a breakdown.
We require the stock to hold above key support (VWAP, EMA20) with
rising volume on the bounce.
"""
from __future__ import annotations

from dataclasses import dataclass

from tradingbot.models import SymbolSnapshot


@dataclass
class PullbackReentrySignal:
    """Result of pullback re-entry evaluation."""
    qualifies: bool
    reason: str
    reentry_score: float  # 0-100 quality score for the re-entry
    pullback_depth_pct: float  # how far the stock pulled back from high


def evaluate_pullback_reentry(
    stock: SymbolSnapshot,
    prev_entry_price: float | None = None,
    intraday_hod: float | None = None,
) -> PullbackReentrySignal:
    """Evaluate whether a stock qualifies for a pullback re-entry.

    A valid pullback re-entry requires ALL of:
      1. The stock gapped up or ran up (gap_pct ≥ 1% or intraday_change ≥ 1%)
      2. Price has pulled back from the high (at least 30% of the move retraced)
      3. Price is holding above key support:
         - Above VWAP (institutional anchor), OR
         - Above EMA20 (trend support)
      4. Price is at or above EMA9 (short-term recovery signal)
      5. Decent relative volume (≥ 1.0) — participants still interested

    Optional: If prev_entry_price is given, we require price to be meaningfully
    lower (at least 2%) to ensure a materially better entry.

    Returns:
        PullbackReentrySignal with qualification and quality score.
    """
    # ── Basic eligibility: was there an initial move to pull back from? ──
    had_move = stock.gap_pct >= 1.0 or stock.intraday_change_pct >= 1.0
    if not had_move:
        return PullbackReentrySignal(
            qualifies=False,
            reason="no_initial_move",
            reentry_score=0.0,
            pullback_depth_pct=0.0,
        )

    # ── Measure pullback depth ──
    # Prefer intraday_hod (post-alert HOD saved at stop-out time) — it is
    # the exact high the stock reached after the alert, making the pullback
    # depth calculation accurate.  Fall back to reclaim_level (premarket
    # high) or a gap-implied estimate when neither is available.
    if intraday_hod and intraday_hod > stock.price:
        high_ref = intraday_hod
    else:
        high_ref = stock.reclaim_level
        if high_ref <= 0 or high_ref < stock.price * 0.95:
            # reclaim_level seems stale — estimate from open + ATR
            high_ref = stock.price * (1 + stock.gap_pct / 100.0) if stock.gap_pct > 0 else stock.price

    if high_ref <= 0 or high_ref <= stock.price:
        # Price is AT or ABOVE the high — not a pullback, still running
        return PullbackReentrySignal(
            qualifies=False,
            reason="price_at_high_not_pullback",
            reentry_score=0.0,
            pullback_depth_pct=0.0,
        )

    # Calculate how much of the move from open/support to high was retraced
    open_ref = stock.open_price if stock.open_price > 0 else stock.vwap
    if open_ref <= 0:
        open_ref = stock.price * (1 - stock.gap_pct / 100.0) if stock.gap_pct > 0 else stock.price

    move_range = high_ref - open_ref
    if move_range <= 0:
        return PullbackReentrySignal(
            qualifies=False,
            reason="no_measurable_range",
            reentry_score=0.0,
            pullback_depth_pct=0.0,
        )

    pullback_from_high = high_ref - stock.price
    pullback_depth_pct = (pullback_from_high / move_range) * 100.0

    # Require meaningful pullback (30-70% retracement = constructive)
    # < 30% = barely pulled back, not a real dip
    # > 70% = too deep, likely a breakdown not a pullback
    if pullback_depth_pct < 30:
        return PullbackReentrySignal(
            qualifies=False,
            reason=f"pullback_too_shallow:{pullback_depth_pct:.0f}%",
            reentry_score=0.0,
            pullback_depth_pct=pullback_depth_pct,
        )
    if pullback_depth_pct > 70:
        return PullbackReentrySignal(
            qualifies=False,
            reason=f"pullback_too_deep:{pullback_depth_pct:.0f}%",
            reentry_score=0.0,
            pullback_depth_pct=pullback_depth_pct,
        )

    # ── Support checks: price must hold above key levels ──
    above_vwap = stock.vwap > 0 and stock.price >= stock.vwap
    above_ema20 = stock.ema20 > 0 and stock.price >= stock.ema20
    above_ema9 = stock.ema9 > 0 and stock.price >= stock.ema9

    # Must hold at least one major support
    if not (above_vwap or above_ema20):
        return PullbackReentrySignal(
            qualifies=False,
            reason="below_vwap_and_ema20",
            reentry_score=0.0,
            pullback_depth_pct=pullback_depth_pct,
        )

    # Short-term recovery: price should be at or above EMA9
    if not above_ema9:
        return PullbackReentrySignal(
            qualifies=False,
            reason="below_ema9_no_recovery",
            reentry_score=0.0,
            pullback_depth_pct=pullback_depth_pct,
        )

    # ── Volume: participants must still be engaged ──
    if stock.relative_volume < 1.0:
        return PullbackReentrySignal(
            qualifies=False,
            reason=f"low_volume:{stock.relative_volume:.1f}x",
            reentry_score=0.0,
            pullback_depth_pct=pullback_depth_pct,
        )

    # ── If re-entering after a prior alert, require better price ──
    # After a confirmed stop-out (intraday_hod is set) we know the stock
    # genuinely dipped.  Any re-entry below the original entry is valid —
    # use a 0.5% floor so we don't re-alert at essentially the same tick.
    # For first-pass dedup (no stop history) keep the stricter 2% gate.
    if prev_entry_price is not None and prev_entry_price > 0:
        improvement_pct = (prev_entry_price - stock.price) / prev_entry_price * 100
        min_improvement = 0.5 if intraday_hod else 2.0
        if improvement_pct < min_improvement:
            return PullbackReentrySignal(
                qualifies=False,
                reason=f"entry_not_improved:{improvement_pct:.1f}%",
                reentry_score=0.0,
                pullback_depth_pct=pullback_depth_pct,
            )

    # ── Score the re-entry quality (0-100) ──
    score = 0.0

    # Pullback depth: 40-60% retracement is the sweet spot (Fibonacci zone)
    if 40 <= pullback_depth_pct <= 60:
        score += 30.0
    elif 30 <= pullback_depth_pct <= 70:
        score += 20.0

    # Support quality: above both VWAP+EMA20 > just one
    if above_vwap and above_ema20:
        score += 25.0
    else:
        score += 15.0

    # Volume strength
    if stock.relative_volume >= 2.0:
        score += 20.0
    elif stock.relative_volume >= 1.5:
        score += 15.0
    else:
        score += 10.0

    # Catalyst backing — pullbacks on catalyst-driven names are stronger
    if stock.catalyst_score >= 60:
        score += 15.0
    elif stock.catalyst_score >= 40:
        score += 10.0
    else:
        score += 5.0

    # Positive gap still intact
    if stock.gap_pct >= 2.0:
        score += 10.0
    elif stock.gap_pct >= 0.5:
        score += 5.0

    return PullbackReentrySignal(
        qualifies=True,
        reason=f"pullback_reentry:depth={pullback_depth_pct:.0f}%,vwap={'Y' if above_vwap else 'N'},ema20={'Y' if above_ema20 else 'N'}",
        reentry_score=min(100.0, score),
        pullback_depth_pct=pullback_depth_pct,
    )
