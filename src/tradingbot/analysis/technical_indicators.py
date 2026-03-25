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


def compute_indicators(bars_data: list[Any], daily_bars: list[Any] | None = None) -> dict[str, float]:
    """
    Compute technical indicators from a list of OHLCV bar objects.

    Parameters
    ----------
    bars_data : list[Any]
        Intraday (15-min) bars for EMA / MACD / RSI / VWAP computation.
    daily_bars : list[Any] | None
        Daily bars (5-day).  Used for:
        - ``atr_daily``: proper daily ATR for stop/target sizing
        - ``support`` / ``resistance``: prior-day and multi-day S/R levels
        When *None* the function falls back to intraday-derived values.

    Returns a dict with:
      - ema9, ema20, ema50         : Exponential moving averages
      - vwap                       : Volume-weighted average price (today only)
      - rsi                        : RSI(14) - overbought/oversold
      - macd, macd_signal          : MACD crossover
      - atr                        : Daily ATR (preferred) or intraday ATR fallback
      - atr_intraday               : Raw 15-min ATR (always present if TA available)
      - bb_upper, bb_lower         : Bollinger Bands
      - support, resistance        : Key daily price levels
      - prev_day_high, prev_day_low: Prior session high/low
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

            # ── Intraday ATR (15-min bars) ────────────────────────────
            atr_intraday = float(
                ta.volatility.average_true_range(df["high"], df["low"], df["close"], window=14).iloc[-1]
            )
            result["atr_intraday"] = atr_intraday

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

        # ── VWAP — today's session only ──────────────────────────────
        # Bars carry a .timestamp attribute.  Identify today's date and
        # restrict the VWAP calculation to only today's bars so it resets
        # at the start of each trading day (standard VWAP behaviour).
        try:
            bar_dates = [getattr(b, "timestamp", None) for b in bars_data]
            if bar_dates and bar_dates[-1] is not None:
                last_date = bar_dates[-1].date() if hasattr(bar_dates[-1], "date") else None
                if last_date is not None:
                    today_mask = pd.Series([
                        (getattr(b, "timestamp", None) is not None
                         and getattr(b, "timestamp").date() == last_date)
                        for b in bars_data
                    ])
                    df_today = df[today_mask.values]
                else:
                    df_today = df
            else:
                df_today = df
        except Exception:
            df_today = df

        if len(df_today) > 0 and df_today["volume"].sum() > 0:
            tp = (df_today["high"] + df_today["low"] + df_today["close"]) / 3
            result["vwap"] = float((tp * df_today["volume"]).sum() / df_today["volume"].sum())
        else:
            # Fallback: full-range VWAP (better than nothing)
            typical_price = (df["high"] + df["low"] + df["close"]) / 3
            result["vwap"] = float(
                (typical_price * df["volume"]).sum() / df["volume"].sum()
                if df["volume"].sum() > 0
                else closes[-1]
            )

        # ── Daily ATR & Support/Resistance from daily bars ───────────
        if daily_bars and len(daily_bars) >= 2:
            d_closes = [float(b.close) for b in daily_bars]
            d_highs  = [float(b.high)  for b in daily_bars]
            d_lows   = [float(b.low)   for b in daily_bars]
            d_df = pd.DataFrame({"close": d_closes, "high": d_highs, "low": d_lows})

            # Daily ATR (window = min(14, available bars))
            if TA_AVAILABLE and len(d_df) >= 2:
                win = min(14, len(d_df))
                atr_daily = float(
                    ta.volatility.average_true_range(
                        d_df["high"], d_df["low"], d_df["close"], window=win
                    ).iloc[-1]
                )
                result["atr"] = atr_daily
            else:
                # Fallback: average daily range
                result["atr"] = float((d_df["high"] - d_df["low"]).mean())

            # Support: lowest low over last 5 daily bars
            # Resistance: highest high over last 5 daily bars
            sw = min(5, len(d_df))
            result["support"]    = float(d_df["low"].iloc[-sw:].min())
            result["resistance"] = float(d_df["high"].iloc[-sw:].max())

            # Prior-day high/low (the bar before the most recent one)
            if len(d_df) >= 2:
                result["prev_day_high"] = float(d_df["high"].iloc[-2])
                result["prev_day_low"]  = float(d_df["low"].iloc[-2])
        else:
            # No daily bars — use intraday-derived values
            if "atr_intraday" in result:
                # Scale 15-min ATR → approximate daily by √26 (26 bars/day)
                result["atr"] = result["atr_intraday"] * (26 ** 0.5)
            else:
                result["atr"] = closes[-1] * 0.02

            # Intraday S/R fallback: use full available range (not just 10 bars)
            window = min(len(df), 52)  # ≈ 2 full trading days of 15-min bars
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
