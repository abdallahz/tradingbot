"""
Candlestick chart generator using mplfinance.

Generates a dark-themed OHLCV candlestick chart with:
  - EMA 9  (blue)
  - EMA 20 (orange)
  - VWAP   (purple dashed)
  - Horizontal level lines (entry / stop / tp1 / tp2) when a TradeCard is given
  - Volume subplot
  - Saved as PNG to outputs/charts/SYMBOL_YYYYMMDD_HHMM.png
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from tradingbot.models import TradeCard

# ── Style constants ────────────────────────────────────────────────────────────

_STYLE         = "nightclouds"   # dark mplfinance style
_CHART_WIDTH   = 14              # inches
_CHART_HEIGHT  = 8               # inches


# ── Public API ─────────────────────────────────────────────────────────────────

def generate_chart(
    symbol:      str,
    bars_data:   list[Any],
    indicators:  dict[str, float],
    trade_card:  "TradeCard | None" = None,
    output_dir:  "str | Path | None" = None,
) -> str | None:
    """
    Generate a candlestick PNG for *symbol* and return the file path.

    Returns None if mplfinance is not installed or chart generation fails.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")          # non-interactive — must come before pyplot
        import mplfinance as mpf       # noqa: F401
    except ImportError:
        logger.debug(
            "mplfinance not installed — skipping chart generation. "
            "Run: pip install mplfinance"
        )
        return None

    if len(bars_data) < 5:
        logger.debug(f"generate_chart({symbol}): not enough bars ({len(bars_data)})")
        return None

    try:
        return _render_chart(symbol, bars_data, indicators, trade_card, output_dir)
    except Exception as e:
        logger.warning(f"generate_chart({symbol}) failed: {e}", exc_info=True)
        return None


# ── Internal helpers ───────────────────────────────────────────────────────────

def _render_chart(
    symbol:     str,
    bars_data:  list[Any],
    indicators: dict[str, float],
    trade_card: "TradeCard | None",
    output_dir: "str | Path | None",
) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import mplfinance as mpf
    import pandas as pd

    # ── Build OHLCV DataFrame ──────────────────────────────────────────────
    df = _build_ohlcv_df(bars_data)
    if df is None or df.empty:
        raise ValueError("Could not build OHLCV DataFrame from bars_data")

    # ── Output path ────────────────────────────────────────────────────────
    if output_dir is None:
        output_dir = Path("outputs") / "charts"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    ts      = datetime.now().strftime("%Y%m%d_%H%M")
    outfile = str(output_dir / f"{symbol}_{ts}.png")

    # ── Add-plots (overlays) ───────────────────────────────────────────────
    add_plots = _build_add_plots(df, indicators)

    # ── Horizontal level lines ─────────────────────────────────────────────
    hlines_dict = _build_hlines(trade_card)

    # ── mplfinance kwargs ──────────────────────────────────────────────────
    plot_kwargs: dict[str, Any] = dict(
        type        = "candle",
        style       = _STYLE,
        title       = f"\n{symbol}  |  15-min  |  {ts[:8]}",
        ylabel      = "Price ($)",
        ylabel_lower= "Volume",
        volume      = True,
        figsize     = (_CHART_WIDTH, _CHART_HEIGHT),
        savefig     = outfile,
        warn_too_much_data = len(df) + 1,   # suppress warning
    )

    if add_plots:
        plot_kwargs["addplot"] = add_plots

    if hlines_dict:
        plot_kwargs["hlines"] = hlines_dict

    mpf.plot(df, **plot_kwargs)
    plt.close("all")

    logger.info(f"Chart saved: {outfile}")
    return outfile


def _build_ohlcv_df(bars_data: list[Any]):
    """Convert Alpaca bar objects → pandas DataFrame with DatetimeIndex."""
    try:
        import pandas as pd
    except ImportError:
        return None

    rows = []
    for b in bars_data:
        try:
            ts = b.timestamp
            if hasattr(ts, "to_pydatetime"):
                ts = ts.to_pydatetime()
            rows.append(
                {
                    "Date":   ts,
                    "Open":   float(b.open),
                    "High":   float(b.high),
                    "Low":    float(b.low),
                    "Close":  float(b.close),
                    "Volume": float(b.volume),
                }
            )
        except Exception:
            continue

    if not rows:
        return None

    df = pd.DataFrame(rows)
    df["Date"] = pd.to_datetime(df["Date"], utc=True)
    df.set_index("Date", inplace=True)
    df.sort_index(inplace=True)
    return df


def _build_add_plots(df, indicators: dict[str, float]) -> list:
    """
    Build mplfinance addplot list for EMA9, EMA20, and VWAP lines.
    Only adds series that actually have a non-zero indicator value.
    """
    try:
        import mplfinance as mpf
        import pandas as pd
    except ImportError:
        return []

    add_plots = []
    close_prices = df["Close"]

    # EMA 9
    ema9_val = indicators.get("ema9", 0.0)
    if ema9_val > 0:
        ema9_series = close_prices.ewm(span=9, adjust=False).mean()
        add_plots.append(
            mpf.make_addplot(
                ema9_series,
                color="deepskyblue",
                width=1.2,
                label="EMA 9",
            )
        )

    # EMA 20
    ema20_val = indicators.get("ema20", 0.0)
    if ema20_val > 0:
        ema20_series = close_prices.ewm(span=20, adjust=False).mean()
        add_plots.append(
            mpf.make_addplot(
                ema20_series,
                color="orange",
                width=1.2,
                label="EMA 20",
            )
        )

    # VWAP — flat horizontal line across all rows (VWAP from indicators)
    vwap_val = indicators.get("vwap", 0.0)
    if vwap_val > 0:
        import pandas as pd
        vwap_series = pd.Series(vwap_val, index=df.index)
        add_plots.append(
            mpf.make_addplot(
                vwap_series,
                color="mediumpurple",
                width=1.5,
                linestyle="--",
                label="VWAP",
            )
        )

    return add_plots


def _build_hlines(trade_card: "TradeCard | None") -> dict | None:
    """
    Build mplfinance hlines dict for entry / stop / TP1 / TP2 levels.
    Returns None if no trade_card is provided.
    """
    if trade_card is None:
        return None

    levels: list[float] = []
    colors: list[str]   = []

    def _add(price: float, color: str) -> None:
        if price and price > 0:
            levels.append(price)
            colors.append(color)

    _add(getattr(trade_card, "entry_price", 0.0),  "lime")
    _add(getattr(trade_card, "stop_price",  0.0),  "red")
    _add(getattr(trade_card, "tp1_price",   0.0),  "yellow")
    _add(getattr(trade_card, "tp2_price",   0.0),  "gold")

    if not levels:
        return None

    return dict(
        hlines=levels,
        colors=colors,
        linestyle="--",
        linewidths=0.8,
    )
