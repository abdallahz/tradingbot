"""
Phase 7 demo — candlestick chart generation + pattern detection.
Uses synthetic bar data (no Alpaca credentials needed).
"""
import sys
sys.path.insert(0, "src")

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from tradingbot.analysis.pattern_detector import detect_patterns, format_patterns, score_confluence, MIN_CONFLUENCE_SCORE
from tradingbot.analysis.chart_generator import generate_chart
from tradingbot.models import TradeCard


# ── Minimal bar stub matching Alpaca bar object shape ─────────────────────────

@dataclass
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def make_bars() -> list[Bar]:
    """
    Synthetic 30-bar 15-min series that deliberately contains:
      - A strong up-move pole  (bars 0-14, +4 %)
      - A controlled pullback flag (bars 15-19, -1.5 %, lower highs)
      - A hammer candle at bar 25
      - Trading above VWAP throughout
    """
    base_time = datetime(2026, 3, 9, 9, 30, tzinfo=timezone.utc)
    price = 100.0
    bars: list[Bar] = []

    # Pole: strong up move
    for i in range(15):
        o = price
        price += 0.3          # +0.3 each bar → total +4.5%
        c = price
        bars.append(Bar(
            timestamp = base_time + timedelta(minutes=15 * i),
            open   = round(o, 2),
            high   = round(c + 0.15, 2),
            low    = round(o - 0.05, 2),
            close  = round(c, 2),
            volume = 50_000 - i * 500,
        ))

    # Flag: orderly pullback with lower highs
    for i in range(5):
        o = price
        price -= 0.22         # gentle drop
        c = price
        bars.append(Bar(
            timestamp = base_time + timedelta(minutes=15 * (15 + i)),
            open   = round(o, 2),
            high   = round(o + 0.05 - i * 0.02, 2),   # progressively lower highs
            low    = round(c - 0.08, 2),
            close  = round(c, 2),
            volume = 25_000,
        ))

    # Neutral consolidation
    for i in range(5):
        o = price
        c = price + 0.05
        bars.append(Bar(
            timestamp = base_time + timedelta(minutes=15 * (20 + i)),
            open   = round(o, 2),
            high   = round(o + 0.1, 2),
            low    = round(o - 0.1, 2),
            close  = round(c, 2),
            volume = 20_000,
        ))

    # Hammer candle
    o = price
    bars.append(Bar(
        timestamp = base_time + timedelta(minutes=15 * 25),
        open   = round(o, 2),
        high   = round(o + 0.05, 2),   # tiny upper wick
        low    = round(o - 0.6, 2),    # long lower wick (> 2× body)
        close  = round(o + 0.1, 2),    # bullish close
        volume = 35_000,
    ))

    # Resume up move
    for i in range(4):
        o = price
        price += 0.2
        c = price
        bars.append(Bar(
            timestamp = base_time + timedelta(minutes=15 * (26 + i)),
            open   = round(o, 2),
            high   = round(c + 0.1, 2),
            low    = round(o - 0.05, 2),
            close  = round(c, 2),
            volume = 40_000,
        ))

    return bars


def make_indicators(bars: list[Bar]) -> dict:
    closes = [b.close for b in bars]
    avg = sum(closes) / len(closes)
    return {
        "ema9":       round(avg + 0.5, 2),
        "ema20":      round(avg - 0.3, 2),
        "vwap":       round(avg, 2),
        "rsi":        58.4,
        "support":    closes[0] * 0.995,
        "resistance": closes[-1] * 0.985,   # slightly below current → breakout
    }


# ── Run the demo ───────────────────────────────────────────────────────────────

bars = make_bars()
indicators = make_indicators(bars)

# 1. Pattern detection
patterns = detect_patterns(bars, indicators)
print("\n=== Pattern Detection ===")
print(f"Raw list : {patterns}")
print(f"Formatted: {format_patterns(patterns)}")

# ── Confluence scoring ─────────────────────────────────────────────────────────
confluence = score_confluence(patterns, side="long")
print(f"\n=== Confluence Score (long) ===")
print(f"Score : {confluence:.0f} / 100")
print(f"Gate  : {'✅ PASS (alert fires)' if confluence >= MIN_CONFLUENCE_SCORE else '❌ BLOCKED (dropped)'}")

# Show what happens with a bearish signal present
bad_patterns = ["above_vwap", "bearish_engulfing"]
bad_confluence = score_confluence(bad_patterns, side="long")
print(f"\n  Scenario — bearish engulfing present:")
print(f"  Patterns : {format_patterns(bad_patterns)}")
print(f"  Score    : {bad_confluence:.0f}  → {'✅ PASS' if bad_confluence >= MIN_CONFLUENCE_SCORE else '❌ BLOCKED — alert dropped'}")

# Show low-volume breakout filtering
print(f"\n=== Volume Confirmation ===")
low_vol_bars = make_bars()
# Crush final bar volume to simulate drift
from dataclasses import replace as dc_replace
low_vol_bars[-1] = dc_replace(low_vol_bars[-1], volume=5_000)  # << avg is ~30k
# Force price above resistance by pushing close high enough
avg_close = sum(b.close for b in low_vol_bars) / len(low_vol_bars)
low_vol_indicators = dict(indicators)
low_vol_indicators["resistance"] = low_vol_bars[-1].close * 0.99  # just below close
low_vol_patterns = detect_patterns(low_vol_bars, low_vol_indicators)
print(f"  Low-vol  bar   volume : {low_vol_bars[-1].volume:,}")
print(f"  Average  bar   volume : ~{int(sum(b.volume for b in low_vol_bars[-20:]) / 20):,}")
print(f"  Breakout detected     : {'NO — volume too low, filtered out' if 'breakout' not in low_vol_patterns else 'YES'}")
print(f"  Patterns fired        : {format_patterns(low_vol_patterns)}")

# 2. Build a sample TradeCard
card = TradeCard(
    symbol          = "DEMO",
    side            = "long",
    score           = 87.0,
    entry_price     = bars[-1].close,
    stop_price      = bars[-1].close * 0.97,
    tp1_price       = bars[-1].close * 1.03,
    tp2_price       = bars[-1].close * 1.06,
    invalidation_price = bars[-1].close * 0.95,
    session_tag     = "morning",
    reason          = ["bull_flag", "above_vwap", "hammer"],
    patterns        = patterns,
)

# 3. Chart generation
print("\n=== Chart Generation ===")
path = generate_chart(
    symbol     = "DEMO",
    bars_data  = bars,
    indicators = indicators,
    trade_card = card,
    output_dir = "outputs/charts",
)

if path:
    print(f"Chart saved to: {path}")
else:
    print("Chart generation skipped (mplfinance issue or not installed)")

print("\n=== Sample Playbook Entry ===")
print(f"- **{card.symbol}** ({card.side.upper()}) | score={card.score:.0f} | "
      f"entry=${card.entry_price:.2f} | tp1=${card.tp1_price:.2f} | "
      f"tp2=${card.tp2_price:.2f} | stop=${card.stop_price:.2f}")
print(f"  **Patterns:** {format_patterns(card.patterns)}")
if path:
    print(f"  ![DEMO Chart]({path})")
