from __future__ import annotations

"""
Enhanced Technical Indicators using the free `ta` library.
Falls back to simple pandas calculations if `ta` is not installed.

Install:  pip install ta
Docs:     https://technical-analysis-library-in-python.readthedocs.io/
"""

import logging
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# Try importing `ta` library (free, pure Python)
try:
    import ta
    TA_AVAILABLE = True
except ImportError:
    TA_AVAILABLE = False
    logger.debug("'ta' library not installed. Using basic indicators. Run: pip install ta")


def compute_indicators(bars_data: list[Any]) -> dict[str, float]:
    """
    Compute technical indicators from a list of OHLCV bar objects.

    Returns a dict with:
      - ema9, ema20, ema50         : Exponential moving averages
      - vwap                       : Volume-weighted average price
      - rsi                        : RSI(14) - overbought/oversold
      - macd, macd_signal          : MACD crossover
      - atr                        : Average True Range (volatility)
      - bb_upper, bb_lower         : Bollinger Bands
      - support, resistance        : Key price levels from recent bars
    """
    if not bars_data or len(bars_data) < 2:
        return {}

    try:
        closes = [float(b.close) for b in bars_data]
        highs  = [float(b.high)  for b in bars_data]
        lows   = [float(b.low)   for b in bars_data]
        vols   = [float(b.volume) for b in bars_data]

        df = pd.DataFrame({
            "close":  closes,
            "high":   highs,
            "low":    lows,
            "volume": vols,
        })

        result: dict[str, float] = {}

        if TA_AVAILABLE and len(df) >= 14:
            # ── EMA ──────────────────────────────────────────────────
            result["ema9"]  = float(ta.trend.ema_indicator(df["close"], window=9).iloc[-1])
            result["ema20"] = float(ta.trend.ema_indicator(df["close"], window=20).iloc[-1])
            result["ema50"] = float(ta.trend.ema_indicator(df["close"], window=min(50, len(df))).iloc[-1])

            # ── RSI ───────────────────────────────────────────────────
            result["rsi"] = float(ta.momentum.rsi(df["close"], window=14).iloc[-1])

            # ── MACD ──────────────────────────────────────────────────
            macd_obj = ta.trend.MACD(df["close"])
            result["macd"]        = float(macd_obj.macd().iloc[-1])
            result["macd_signal"] = float(macd_obj.macd_signal().iloc[-1])
            result["macd_hist"]   = float(macd_obj.macd_diff().iloc[-1])

            # ── ATR (volatility) ──────────────────────────────────────
            result["atr"] = float(
                ta.volatility.average_true_range(df["high"], df["low"], df["close"], window=14).iloc[-1]
            )

            # ── Bollinger Bands ───────────────────────────────────────
            bb = ta.volatility.BollingerBands(df["close"], window=20)
            result["bb_upper"] = float(bb.bollinger_hband().iloc[-1])
            result["bb_lower"] = float(bb.bollinger_lband().iloc[-1])
            result["bb_mid"]   = float(bb.bollinger_mavg().iloc[-1])

            # ── Volume indicators ─────────────────────────────────────
            if len(df) >= 20:
                result["obv"] = float(ta.volume.on_balance_volume(df["close"], df["volume"]).iloc[-1])

        else:
            # ── Fallback: pure pandas ─────────────────────────────────
            result["ema9"]  = float(df["close"].ewm(span=9,  adjust=False).mean().iloc[-1])
            result["ema20"] = float(df["close"].ewm(span=20, adjust=False).mean().iloc[-1])
            result["ema50"] = float(df["close"].ewm(span=min(50, len(df)), adjust=False).mean().iloc[-1])

        # ── VWAP (always compute manually) ───────────────────────────
        typical_price = (df["high"] + df["low"] + df["close"]) / 3
        result["vwap"] = float(
            (typical_price * df["volume"]).sum() / df["volume"].sum()
            if df["volume"].sum() > 0
            else closes[-1]
        )

        # ── Support / Resistance (last 10 bars) ───────────────────────
        window = min(10, len(df))
        result["support"]    = float(df["low"].iloc[-window:].min())
        result["resistance"] = float(df["high"].iloc[-window:].max())

        return result

    except Exception as e:
        logger.debug(f"Technical indicator computation failed: {e}")
        return {}


def interpret_signals(indicators: dict[str, float], current_price: float) -> list[str]:
    """
    Interpret technical indicators into actionable signals.
    Returns a list of signal strings (e.g. "rsi_oversold", "macd_bullish_cross").
    """
    signals: list[str] = []
    if not indicators:
        return signals

    ema9   = indicators.get("ema9",  current_price)
    ema20  = indicators.get("ema20", current_price)
    rsi    = indicators.get("rsi",   50.0)
    macd   = indicators.get("macd",  0.0)
    macd_s = indicators.get("macd_signal", 0.0)
    macd_h = indicators.get("macd_hist",   0.0)
    vwap   = indicators.get("vwap",  current_price)
    bb_lower = indicators.get("bb_lower", 0.0)
    bb_upper = indicators.get("bb_upper", float("inf"))
    support    = indicators.get("support",    current_price * 0.97)
    resistance = indicators.get("resistance", current_price * 1.03)

    # EMA trend
    if current_price > ema9 > ema20:
        signals.append("ema_bullish_alignment")
    elif current_price < ema9 < ema20:
        signals.append("ema_bearish_alignment")

    # RSI
    if rsi < 35:
        signals.append("rsi_oversold")
    elif rsi > 70:
        signals.append("rsi_overbought")

    # MACD crossover
    if macd > macd_s and macd_h > 0:
        signals.append("macd_bullish_cross")
    elif macd < macd_s and macd_h < 0:
        signals.append("macd_bearish_cross")

    # VWAP
    if current_price > vwap:
        signals.append("above_vwap")
    else:
        signals.append("below_vwap")

    # Bollinger Bands
    if current_price <= bb_lower:
        signals.append("bb_oversold")
    elif current_price >= bb_upper:
        signals.append("bb_overbought")

    # Near support / resistance
    if abs(current_price - support) / current_price < 0.01:
        signals.append("near_support")
    if abs(current_price - resistance) / current_price < 0.01:
        signals.append("near_resistance")

    return signals
