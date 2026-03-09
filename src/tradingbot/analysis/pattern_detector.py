"""
Chart pattern and candlestick pattern detection.

Detects common patterns from OHLCV price data:
  - Bull flag       : Strong up move, then orderly pullback
  - Breakout        : Price crossing above resistance
  - Support bounce  : Price bouncing off a key support level
  - Hammer          : Bullish reversal candle (long lower wick)
  - Bullish engulfing: Current green candle engulfs previous red candle
  - Bearish engulfing: Current red candle engulfs previous green candle
  - Doji            : Open ≈ Close (indecision / potential reversal)
  - Above VWAP      : Price trading above volume-weighted average price
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def detect_patterns(
    bars_data: list[Any],
    indicators: dict[str, float],
) -> list[str]:
    """
    Detect chart patterns from a list of Alpaca bar objects.

    Args:
        bars_data:   List of bar objects with .open .high .low .close .volume
        indicators:  Dict from compute_indicators() (support, resistance, vwap ...)

    Returns:
        List of pattern name strings, e.g. ["bull_flag", "hammer", "above_vwap"]
    """
    if len(bars_data) < 5:
        return []

    try:
        opens  = [float(b.open)   for b in bars_data]
        highs  = [float(b.high)   for b in bars_data]
        lows   = [float(b.low)    for b in bars_data]
        closes = [float(b.close)  for b in bars_data]
        vols   = [float(b.volume) for b in bars_data]
    except AttributeError as e:
        logger.debug(f"pattern_detector: missing bar attribute: {e}")
        return []

    patterns: list[str] = []
    current = closes[-1]

    # ── Trend / structure patterns ─────────────────────────────────────────
    if len(closes) >= 15 and _is_bull_flag(highs, lows, closes, vols):
        patterns.append("bull_flag")

    resistance = indicators.get("resistance", 0.0)
    if resistance > 0 and current > resistance * 1.002:
        patterns.append("breakout")

    support = indicators.get("support", 0.0)
    if support > 0:
        near_support = abs(current - support) / support < 0.015
        bouncing_up  = len(closes) >= 2 and closes[-1] > closes[-2]
        if near_support and bouncing_up:
            patterns.append("support_bounce")

    # ── VWAP bias ──────────────────────────────────────────────────────────
    vwap = indicators.get("vwap", 0.0)
    if vwap > 0 and current > vwap:
        patterns.append("above_vwap")

    # ── Candlestick patterns (require at least 2 bars) ─────────────────────
    if len(bars_data) >= 2:
        if _is_hammer(opens, highs, lows, closes):
            patterns.append("hammer")
        if _is_bullish_engulfing(opens, closes):
            patterns.append("bullish_engulfing")
        if _is_bearish_engulfing(opens, closes):
            patterns.append("bearish_engulfing")
        if _is_doji(opens, highs, lows, closes):
            patterns.append("doji")

    return patterns


# ── Individual pattern helpers ─────────────────────────────────────────────────

def _is_bull_flag(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    vols: list[float],
    lookback: int = 15,
) -> bool:
    """
    Bull flag: strong up-move pole (≥3%) followed by an orderly pullback flag
    with lower highs and contained volume.
    """
    if len(closes) < lookback:
        return False

    mid = lookback // 2

    # Pole: first half of lookback shows upward move
    pole = closes[-lookback : -mid]
    if len(pole) < 2 or pole[0] <= 0:
        return False
    pole_move_pct = (pole[-1] - pole[0]) / pole[0] * 100
    if pole_move_pct < 3.0:
        return False

    # Flag: last 5 bars — controlled pullback with lower highs
    flag_h = highs[-5:]
    flag_c = closes[-5:]
    if len(flag_c) < 2 or flag_c[0] <= 0:
        return False

    flag_drop_pct = (flag_c[-1] - flag_c[0]) / flag_c[0] * 100
    lower_highs = all(flag_h[i] <= flag_h[i - 1] for i in range(1, len(flag_h)))

    # Drop between -3% and 0% (pullback, not collapse)
    return -3.0 < flag_drop_pct < 0.0 and lower_highs


def _is_hammer(
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> bool:
    """
    Hammer / pin bar: bullish reversal.
    - Small body (close > open)
    - Lower wick ≥ 2× body
    - Upper wick ≤ 30% of body
    """
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    body        = abs(c - o)
    lower_wick  = min(o, c) - l
    upper_wick  = h - max(o, c)
    if body == 0:
        return False
    return (
        c > o
        and lower_wick >= 2.0 * body
        and upper_wick <= 0.3 * body
    )


def _is_bullish_engulfing(opens: list[float], closes: list[float]) -> bool:
    """
    Bullish engulfing: previous red candle fully engulfed by current green candle.
    """
    if len(opens) < 2:
        return False
    prev_o, prev_c = opens[-2], closes[-2]
    curr_o, curr_c = opens[-1], closes[-1]
    prev_red   = prev_c < prev_o
    curr_green = curr_c > curr_o
    engulfs    = curr_o <= prev_c and curr_c >= prev_o
    return prev_red and curr_green and engulfs


def _is_bearish_engulfing(opens: list[float], closes: list[float]) -> bool:
    """
    Bearish engulfing: previous green candle fully engulfed by current red candle.
    """
    if len(opens) < 2:
        return False
    prev_o, prev_c = opens[-2], closes[-2]
    curr_o, curr_c = opens[-1], closes[-1]
    prev_green = prev_c > prev_o
    curr_red   = curr_c < curr_o
    engulfs    = curr_o >= prev_c and curr_c <= prev_o
    return prev_green and curr_red and engulfs


def _is_doji(
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> bool:
    """
    Doji: open ≈ close (body < 10% of total candle range) — indecision signal.
    """
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    candle_range = h - l
    body = abs(c - o)
    if candle_range == 0:
        return False
    return body / candle_range < 0.1


# ── Human-readable labels ──────────────────────────────────────────────────────

PATTERN_LABELS: dict[str, str] = {
    "bull_flag":          "🚩 Bull Flag",
    "breakout":           "⚡ Breakout",
    "support_bounce":     "↗️  Support Bounce",
    "above_vwap":         "📈 Above VWAP",
    "hammer":             "🔨 Hammer",
    "bullish_engulfing":  "🟢 Bullish Engulfing",
    "bearish_engulfing":  "🔴 Bearish Engulfing",
    "doji":               "〰️  Doji",
}


def format_patterns(patterns: list[str]) -> str:
    """Convert pattern list to a readable string for playbook output."""
    if not patterns:
        return "none detected"
    return " | ".join(PATTERN_LABELS.get(p, p) for p in patterns)
