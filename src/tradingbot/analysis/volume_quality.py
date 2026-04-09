"""
volume_quality.py — Distinguish healthy volume from dangerous fades.

Professional desks classify volume into *participation types* before
acting on a setup.  A high-volume pullback into support = institutional
accumulation (safe).  A low-volume drift above VWAP with contracting bars
= retail FOMO fade that will reverse hard (dangerous).

This module provides:
  1. classify_volume_profile() — returns "accumulation", "distribution",
     "climax", or "thin_fade"
  2. is_move_exhausted()       — ATR + spread check to avoid entries
     where the intraday range is already spent.
  3. compute_volume_quality_score() — single 0-100 composite for ranking.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class VolumeProfile:
    """Classification of the current volume environment."""
    classification: str   # accumulation | distribution | climax | thin_fade
    relative_volume: float
    volume_trend: str     # increasing | decreasing | flat
    bar_range_trend: str  # expanding | contracting | stable
    score: float          # 0-100 quality score
    reason: str


def classify_volume_profile(
    bars_data: list[Any],
    relative_volume: float,
    current_price: float,
    vwap: float,
) -> VolumeProfile:
    """Classify the volume context of the current move.

    Categories:
      accumulation  — Volume rising on up-bars near support/VWAP.
                      Institutions quietly buying.  SAFE to enter long.
      distribution  — Volume rising on down-bars near resistance.
                      Smart money exiting.  AVOID longs.
      climax        — Extreme volume spike (>5x) with wide-range bar.
                      Exhaustion / blow-off top.  WAIT for pullback.
      thin_fade     — Low volume drift away from VWAP with contracting
                      bars.  Retail-driven; will snap back. DANGEROUS.
    """
    if not bars_data or len(bars_data) < 5:
        return VolumeProfile(
            classification="unknown",
            relative_volume=relative_volume,
            volume_trend="unknown",
            bar_range_trend="unknown",
            score=50.0,
            reason="Insufficient bar data for volume classification",
        )

    try:
        closes = [float(b.close) for b in bars_data]
        highs = [float(b.high) for b in bars_data]
        lows = [float(b.low) for b in bars_data]
        vols = [float(b.volume) for b in bars_data]
    except AttributeError:
        return VolumeProfile(
            classification="unknown",
            relative_volume=relative_volume,
            volume_trend="unknown",
            bar_range_trend="unknown",
            score=50.0,
            reason="Bar attribute error",
        )

    # ── Volume trend (last 5 bars) ──────────────────────────────────
    recent_vols = vols[-5:]
    if len(recent_vols) >= 3:
        first_half = sum(recent_vols[:len(recent_vols) // 2])
        second_half = sum(recent_vols[len(recent_vols) // 2:])
        if second_half > first_half * 1.2:
            vol_trend = "increasing"
        elif second_half < first_half * 0.8:
            vol_trend = "decreasing"
        else:
            vol_trend = "flat"
    else:
        vol_trend = "unknown"

    # ── Bar range trend (expanding or contracting) ──────────────────
    recent_ranges = [h - l for h, l in zip(highs[-5:], lows[-5:])]
    if len(recent_ranges) >= 3:
        first_avg = sum(recent_ranges[:len(recent_ranges) // 2]) / max(1, len(recent_ranges) // 2)
        second_avg = sum(recent_ranges[len(recent_ranges) // 2:]) / max(1, len(recent_ranges) - len(recent_ranges) // 2)
        if second_avg > first_avg * 1.15:
            range_trend = "expanding"
        elif second_avg < first_avg * 0.85:
            range_trend = "contracting"
        else:
            range_trend = "stable"
    else:
        range_trend = "unknown"

    # ── Up-volume vs down-volume ratio (last 10 bars) ───────────────
    lookback = min(10, len(closes))
    up_volume = 0.0
    down_volume = 0.0
    for i in range(-lookback + 1, 0):
        if closes[i] >= closes[i - 1]:
            up_volume += vols[i]
        else:
            down_volume += vols[i]
    total_vol = up_volume + down_volume
    up_ratio = up_volume / total_vol if total_vol > 0 else 0.5

    # ── Price position relative to VWAP ─────────────────────────────
    above_vwap = current_price > vwap if vwap > 0 else True

    # ── Classification logic ────────────────────────────────────────
    score = 50.0

    # CLIMAX: Extreme volume + wide range = exhaustion
    if relative_volume >= 5.0 and range_trend == "expanding":
        classification = "climax"
        score = 25.0
        reason = (
            f"Blow-off volume ({relative_volume:.1f}x) with expanding bars — "
            "exhaustion risk, wait for pullback before entry"
        )

    # THIN FADE: Low volume drift away from VWAP with contracting bars
    elif (relative_volume < 1.2
          and range_trend == "contracting"
          and vol_trend == "decreasing"):
        classification = "thin_fade"
        score = 15.0
        reason = (
            f"Low volume ({relative_volume:.1f}x) with contracting bars — "
            "thin fade, high reversal probability"
        )

    # DISTRIBUTION: Rising volume on selling (down bars dominate)
    elif up_ratio < 0.40 and vol_trend == "increasing":
        classification = "distribution"
        score = 20.0
        reason = (
            f"Volume rising but {up_ratio:.0%} on up-bars — "
            "distribution pattern, smart money selling"
        )

    # ACCUMULATION: Volume rising with up-bar dominance near VWAP/support
    elif up_ratio >= 0.55 and vol_trend in ("increasing", "flat"):
        classification = "accumulation"
        # Higher score if price is near VWAP (institutional zone)
        vwap_proximity_bonus = 0.0
        if vwap > 0:
            vwap_dist_pct = abs(current_price - vwap) / vwap * 100
            if vwap_dist_pct < 1.0:
                vwap_proximity_bonus = 15.0
            elif vwap_dist_pct < 2.0:
                vwap_proximity_bonus = 8.0
        score = 75.0 + vwap_proximity_bonus
        reason = (
            f"Healthy accumulation — {up_ratio:.0%} up-volume, "
            f"vol trend {vol_trend}, near VWAP" if vwap_proximity_bonus > 0
            else f"Healthy accumulation — {up_ratio:.0%} up-volume, vol trend {vol_trend}"
        )

    # DEFAULT: Mixed signals
    else:
        classification = "mixed"
        score = 45.0
        reason = (
            f"Mixed volume profile — up_ratio={up_ratio:.0%}, "
            f"vol_trend={vol_trend}, range_trend={range_trend}"
        )

    # Bonus: relative volume multiplier
    if relative_volume >= 2.0:
        score = min(100.0, score + 10.0)
    elif relative_volume < 1.0:
        score = max(0.0, score - 15.0)

    return VolumeProfile(
        classification=classification,
        relative_volume=relative_volume,
        volume_trend=vol_trend,
        bar_range_trend=range_trend,
        score=score,
        reason=reason,
    )


def is_move_exhausted(
    current_price: float,
    open_price: float,
    atr: float,
    spread_pct: float,
    high_of_day: float = 0.0,
) -> tuple[bool, str]:
    """Check if the intraday move has consumed most of the expected ATR range.

    Professional rule: if price has already moved > 80% of daily ATR from
    the open, the remaining reward potential is slim — "the move is done."

    Also flags wide spreads (> 50% of remaining ATR) which eat into profits.

    Returns:
        (is_exhausted: bool, reason: str)
    """
    if atr <= 0:
        return False, "ATR unavailable — skipping exhaustion check"

    # How far price has moved from open
    move_from_open = abs(current_price - open_price)
    atr_consumed_pct = (move_from_open / atr) * 100

    # How far price is from the high of day (already given back?)
    if high_of_day > 0:
        retracement = (high_of_day - current_price) / atr * 100
    else:
        retracement = 0.0

    # Spread cost as % of remaining ATR
    remaining_atr = max(0.01, atr - move_from_open)
    spread_cost = (spread_pct / 100.0 * current_price)
    spread_vs_remaining = (spread_cost / remaining_atr) * 100

    reasons = []
    exhausted = False

    # Threshold lowered from 80% to 60%.  At 80%, a stock with ATR=$5
    # could rally $4 (8% on $50) before being flagged — way too late.
    # At 60% we catch the move earlier while still allowing healthy
    # momentum (e.g. $3 of $5 ATR = a 6% move on a $50 stock).
    if atr_consumed_pct >= 60:
        exhausted = True
        reasons.append(
            f"ATR {atr_consumed_pct:.0f}% consumed ({move_from_open:.2f} of {atr:.2f}) — move exhausted"
        )

    if spread_vs_remaining >= 50:
        exhausted = True
        reasons.append(
            f"Spread eats {spread_vs_remaining:.0f}% of remaining range — poor reward"
        )

    if retracement >= 30 and atr_consumed_pct >= 45:
        exhausted = True
        reasons.append(
            f"Price already retraced {retracement:.0f}% of ATR from HOD — momentum fading"
        )

    if exhausted:
        return True, " | ".join(reasons)

    return False, f"ATR {atr_consumed_pct:.0f}% used, {100-atr_consumed_pct:.0f}% remaining — room to run"


def compute_volume_quality_score(
    bars_data: list[Any],
    relative_volume: float,
    current_price: float,
    vwap: float,
    atr: float,
    spread_pct: float,
    open_price: float,
) -> tuple[float, str]:
    """Composite volume-quality score for ranking.

    Combines:
      - Volume profile classification (60% weight)
      - ATR exhaustion check (25% weight)
      - Spread quality (15% weight)

    Returns:
        (score: 0-100, summary: str)
    """
    profile = classify_volume_profile(bars_data, relative_volume, current_price, vwap)

    exhausted, exhaust_reason = is_move_exhausted(
        current_price, open_price, atr, spread_pct,
    )

    # Spread quality: tight = 100, wide = 0
    spread_score = max(0.0, 100.0 - spread_pct * 50)

    # ATR score: 0% used = 100, 100% used = 0
    if atr > 0:
        move = abs(current_price - open_price)
        atr_pct_used = min(1.0, move / atr)
        atr_score = (1.0 - atr_pct_used) * 100
    else:
        atr_score = 50.0

    composite = (
        profile.score * 0.60
        + atr_score * 0.25
        + spread_score * 0.15
    )

    summary_parts = [
        f"Vol: {profile.classification}({profile.score:.0f})",
        f"ATR: {'EXHAUSTED' if exhausted else f'{atr_score:.0f}pts'}",
        f"Spread: {spread_score:.0f}pts",
    ]
    summary = " | ".join(summary_parts)

    return round(composite, 1), summary
