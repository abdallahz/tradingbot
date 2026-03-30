"""
confluence_engine.py — Multi-factor confluence scoring for 90%+ win-rate filtering.

Professional desks don't take a trade unless 5+ independent factors align.
This module replaces simple "touch" logic with a full confluence matrix:

  1. Volume Profile   — Is the volume pattern accumulation or fade?
  2. Trend Alignment  — Is SPY/QQQ supporting or fighting the trade?
  3. ATR Exhaustion   — Is there enough range left for a profitable move?
  4. Technical Stack   — EMA9/EMA20/VWAP/RSI/MACD all confirming?
  5. Catalyst Strength — Is there a real fundamental reason for the move?

Each factor contributes to a 0-100 composite.  Only Grade-A setups
(score >= 75) should fire alerts for a 90%+ win-rate target.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from tradingbot.analysis.volume_quality import (
    classify_volume_profile,
    is_move_exhausted,
    VolumeProfile,
)

logger = logging.getLogger(__name__)


@dataclass
class ConfluenceFactor:
    """A single scored factor in the confluence matrix."""
    name: str
    score: float          # 0-100
    weight: float         # 0.0-1.0 (sums to 1.0 across all factors)
    passed: bool          # Hard pass/fail for veto
    reason: str
    grade: str = ""       # A/B/C/F


@dataclass
class ConfluenceResult:
    """Full confluence assessment for a trade setup."""
    composite_score: float = 0.0
    grade: str = "F"             # A (>=75), B (>=55), C (>=40), F (<40)
    factors: list[ConfluenceFactor] = field(default_factory=list)
    vetoed: bool = False         # True if any hard-fail factor triggered
    veto_reason: str = ""
    summary: str = ""
    false_positive_flags: list[str] = field(default_factory=list)


def _grade_score(score: float) -> str:
    if score >= 75:
        return "A"
    if score >= 55:
        return "B"
    if score >= 40:
        return "C"
    return "F"


def evaluate_confluence(
    # Price data
    current_price: float,
    open_price: float,
    ema9: float,
    ema20: float,
    vwap: float,
    atr: float,
    spread_pct: float,
    # Volume data
    bars_data: list[Any],
    relative_volume: float,
    # Market context
    spy_change_pct: float = 0.0,
    qqq_change_pct: float = 0.0,
    # Technical indicators
    rsi: float = 50.0,
    macd_hist: float = 0.0,
    bb_lower: float = 0.0,
    bb_upper: float = 0.0,
    # Catalyst
    catalyst_score: float = 0.0,
    # Pattern context
    patterns: list[str] | None = None,
    gap_pct: float = 0.0,
) -> ConfluenceResult:
    """Run the full confluence matrix and return a graded result.

    This replaces the simple has_valid_setup() + score_confluence() chain
    with a comprehensive multi-factor check that institutional desks use.
    """
    factors: list[ConfluenceFactor] = []
    false_positive_flags: list[str] = []
    patterns = patterns or []

    # ════════════════════════════════════════════════════════════════
    # FACTOR 1: VOLUME PROFILE (25% weight)
    # ════════════════════════════════════════════════════════════════
    vol_profile = classify_volume_profile(
        bars_data, relative_volume, current_price, vwap,
    )

    vol_veto = vol_profile.classification in ("thin_fade", "distribution")
    if vol_profile.classification == "thin_fade":
        false_positive_flags.append(
            "LOW-VOLUME FADE: Price drifting on no volume — high reversal risk"
        )
    if vol_profile.classification == "distribution":
        false_positive_flags.append(
            "DISTRIBUTION: Smart money selling into the move — avoid longs"
        )

    factors.append(ConfluenceFactor(
        name="Volume Profile",
        score=vol_profile.score,
        weight=0.25,
        passed=not vol_veto,
        reason=vol_profile.reason,
        grade=_grade_score(vol_profile.score),
    ))

    # ════════════════════════════════════════════════════════════════
    # FACTOR 2: MARKET TREND ALIGNMENT (20% weight)
    # ════════════════════════════════════════════════════════════════
    # For long trades, SPY/QQQ should not be in freefall.
    # Professional rule: don't go long individual names when the
    # index is dumping > -0.75% — "don't fight the tape."
    worst_index = min(spy_change_pct, qqq_change_pct)

    if worst_index >= 0.5:
        mkt_score = 95.0
        mkt_reason = f"Strong tape: SPY {spy_change_pct:+.2f}%, QQQ {qqq_change_pct:+.2f}%"
    elif worst_index >= 0.0:
        mkt_score = 80.0
        mkt_reason = f"Neutral/positive tape: SPY {spy_change_pct:+.2f}%"
    elif worst_index >= -0.5:
        mkt_score = 60.0
        mkt_reason = f"Slightly weak tape: SPY {spy_change_pct:+.2f}%"
    elif worst_index >= -1.0:
        mkt_score = 35.0
        mkt_reason = f"Weak tape fighting longs: SPY {spy_change_pct:+.2f}%"
        false_positive_flags.append(
            f"TREND CONFLICT: Market down {worst_index:+.2f}% — headwind for longs"
        )
    else:
        mkt_score = 10.0
        mkt_reason = f"Market sell-off: SPY {spy_change_pct:+.2f}%, QQQ {qqq_change_pct:+.2f}%"
        false_positive_flags.append(
            f"MARKET CRASH: SPY {spy_change_pct:+.2f}% — do NOT go long"
        )

    mkt_veto = worst_index < -1.5
    factors.append(ConfluenceFactor(
        name="Market Trend",
        score=mkt_score,
        weight=0.20,
        passed=not mkt_veto,
        reason=mkt_reason,
        grade=_grade_score(mkt_score),
    ))

    # ════════════════════════════════════════════════════════════════
    # FACTOR 3: ATR / VOLATILITY EXHAUSTION (15% weight)
    # ════════════════════════════════════════════════════════════════
    exhausted, exhaust_reason = is_move_exhausted(
        current_price, open_price, atr, spread_pct,
    )

    if exhausted:
        atr_score = 15.0
        false_positive_flags.append(f"MOVE EXHAUSTED: {exhaust_reason}")
    else:
        if atr > 0:
            move = abs(current_price - open_price)
            used_pct = move / atr
            atr_score = max(10.0, (1.0 - used_pct) * 100)
        else:
            atr_score = 50.0

    factors.append(ConfluenceFactor(
        name="ATR Exhaustion",
        score=atr_score,
        weight=0.15,
        passed=not exhausted,
        reason=exhaust_reason,
        grade=_grade_score(atr_score),
    ))

    # ════════════════════════════════════════════════════════════════
    # FACTOR 4: TECHNICAL STACK (25% weight)
    # EMA9, EMA20, VWAP, RSI, MACD must all agree
    # ════════════════════════════════════════════════════════════════
    tech_checklist = []

    # 4a. EMA alignment: Price > EMA9 > EMA20
    if current_price > ema9 > ema20:
        tech_checklist.append(("EMA stack bullish", 20))
    elif current_price > ema9:
        tech_checklist.append(("Price > EMA9 only", 10))
    else:
        tech_checklist.append(("EMA stack broken", 0))

    # 4b. VWAP: price above VWAP
    if vwap > 0 and current_price > vwap:
        tech_checklist.append(("Above VWAP", 20))
    elif vwap > 0:
        tech_checklist.append(("Below VWAP", 0))
        false_positive_flags.append("BELOW VWAP: Institutional bias is against this long")

    # 4c. RSI momentum (sweet spot 45-70)
    if 45 <= rsi <= 70:
        tech_checklist.append(("RSI in momentum zone", 20))
    elif 35 <= rsi < 45:
        tech_checklist.append(("RSI building momentum", 12))
    elif rsi > 70:
        tech_checklist.append(("RSI overbought", 5))
        false_positive_flags.append(f"RSI OVERBOUGHT ({rsi:.0f}): Pullback likely before continuation")
    elif rsi < 35:
        tech_checklist.append(("RSI oversold", 8))

    # 4d. MACD histogram positive (bullish momentum)
    if macd_hist > 0:
        tech_checklist.append(("MACD bullish", 20))
    else:
        tech_checklist.append(("MACD bearish", 0))

    # 4e. Chart patterns
    bullish_patterns = {"bull_flag", "breakout", "bullish_engulfing", "support_bounce", "hammer"}
    bearish_patterns = {"bearish_engulfing"}
    bull_count = sum(1 for p in patterns if p in bullish_patterns)
    bear_count = sum(1 for p in patterns if p in bearish_patterns)

    if bull_count >= 2:
        tech_checklist.append(("Multiple bullish patterns", 20))
    elif bull_count == 1:
        tech_checklist.append(("Single bullish pattern", 12))
    else:
        tech_checklist.append(("No bullish patterns", 0))

    if bear_count > 0:
        false_positive_flags.append(
            f"BEARISH PATTERN DETECTED: {[p for p in patterns if p in bearish_patterns]}"
        )

    tech_raw = sum(pts for _, pts in tech_checklist)
    tech_score = min(100.0, tech_raw)

    factors.append(ConfluenceFactor(
        name="Technical Stack",
        score=tech_score,
        weight=0.25,
        passed=tech_score >= 30,
        reason=" | ".join(f"{name}={pts}" for name, pts in tech_checklist),
        grade=_grade_score(tech_score),
    ))

    # ════════════════════════════════════════════════════════════════
    # FACTOR 5: CATALYST STRENGTH (15% weight)
    # ════════════════════════════════════════════════════════════════
    if catalyst_score >= 70:
        cat_score = 95.0
        cat_reason = f"Strong catalyst ({catalyst_score:.0f}) — news-driven move"
    elif catalyst_score >= 50:
        cat_score = 70.0
        cat_reason = f"Moderate catalyst ({catalyst_score:.0f})"
    elif catalyst_score >= 30:
        cat_score = 45.0
        cat_reason = f"Weak catalyst ({catalyst_score:.0f}) — may be technical only"
    else:
        cat_score = 20.0
        cat_reason = f"No catalyst ({catalyst_score:.0f}) — higher fade risk"

    # Gap-size validation against catalyst
    if gap_pct > 8 and catalyst_score < 40:
        false_positive_flags.append(
            f"GAP WITHOUT NEWS: {gap_pct:.1f}% gap but only {catalyst_score:.0f} catalyst — gap-fill candidate"
        )
        cat_score = max(10.0, cat_score - 20)

    factors.append(ConfluenceFactor(
        name="Catalyst Strength",
        score=cat_score,
        weight=0.15,
        passed=True,  # Catalyst alone doesn't veto, just weakens score
        reason=cat_reason,
        grade=_grade_score(cat_score),
    ))

    # ════════════════════════════════════════════════════════════════
    # COMPOSITE SCORE & GRADE
    # ════════════════════════════════════════════════════════════════
    composite = sum(f.score * f.weight for f in factors)
    composite = round(composite, 1)
    grade = _grade_score(composite)

    # Hard veto check
    vetoed = any(not f.passed for f in factors)
    veto_reason = ""
    if vetoed:
        veto_factors = [f.name for f in factors if not f.passed]
        veto_reason = f"VETOED by: {', '.join(veto_factors)}"

    # Build summary
    summary_parts = [f"{f.name}: {f.grade}({f.score:.0f})" for f in factors]
    summary = f"Grade {grade} ({composite:.0f}/100) — " + " | ".join(summary_parts)

    return ConfluenceResult(
        composite_score=composite,
        grade=grade,
        factors=factors,
        vetoed=vetoed,
        veto_reason=veto_reason,
        summary=summary,
        false_positive_flags=false_positive_flags,
    )


def should_fire_alert(result: ConfluenceResult, min_grade: str = "B") -> bool:
    """Decide whether a setup is high-enough quality to alert.

    For 90%+ win-rate targeting:
      - Only Grade A/B setups (>= 55 score) should fire.
      - Any veto (hard-fail factor) blocks the alert regardless of score.
      - false_positive_flags provide the human trader context on risks.
    """
    if result.vetoed:
        return False

    grade_hierarchy = {"A": 4, "B": 3, "C": 2, "F": 1}
    return grade_hierarchy.get(result.grade, 0) >= grade_hierarchy.get(min_grade, 3)
