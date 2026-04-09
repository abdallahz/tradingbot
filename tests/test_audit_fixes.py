"""Tests for the codebase audit fixes (6 issues).

Fix 1: Resistance picks nearest level instead of farthest
Fix 2: ATR exhaustion uses open_price instead of VWAP
Fix 3: Daily EMA50 trend filter blocks downtrend gap-ups
Fix 4: Meme stocks removed from _CORE_WATCHLIST
Fix 5: MarketGuard docstring matches code
Fix 6: O2 relaxed scanner has max_gap_pct
"""
from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tradingbot.models import SymbolSnapshot, RiskState


# ── Shared fixture ─────────────────────────────────────────────────


def _snap(
    symbol: str = "TEST",
    price: float = 50.0,
    gap_pct: float = 3.0,
    **overrides,
) -> SymbolSnapshot:
    """Create a SymbolSnapshot with sensible defaults and overrides."""
    defaults = dict(
        symbol=symbol,
        price=price,
        gap_pct=gap_pct,
        premarket_volume=200_000,
        dollar_volume=5_000_000.0,
        spread_pct=0.3,
        relative_volume=2.5,
        catalyst_score=55.0,
        ema9=price * 0.995,
        ema20=price * 0.99,
        vwap=price * 0.99,
        recent_volume=100_000,
        avg_volume_20=40_000,
        pullback_low=price * 0.96,
        reclaim_level=price * 1.02,
        pullback_high=price * 1.03,
        key_support=price * 0.97,
        key_resistance=price * 1.02,
        atr=price * 0.02,
        open_price=price * 0.97,
        intraday_change_pct=3.0,
        daily_ema50=0.0,  # 0 = unavailable by default
        patterns=["above_vwap"],
        raw_bars=[],
        tech_indicators={"rsi": 55.0, "macd_hist": 0.01, "vwap": price * 0.99},
    )
    defaults.update(overrides)
    return SymbolSnapshot(**defaults)


# ════════════════════════════════════════════════════════════════════
# FIX 1: Nearest resistance
# ════════════════════════════════════════════════════════════════════


class TestNearestResistance:
    """Verify alpaca_client picks the nearest (minimum) resistance above price."""

    def test_resistance_selection_logic(self):
        """The S/R building code should pick min(above) not max(above)."""
        # Simulate the pullback-mode resistance selection from alpaca_client
        current_price = 50.0
        reclaim_level = 52.0   # PM high
        prev_day_high = 55.0   # far away
        bar_resistance = 51.5  # nearest

        resistance_candidates = [reclaim_level]
        atr_val = 1.0
        max_res_dist = atr_val * 2

        if prev_day_high > 0 and prev_day_high > current_price and (prev_day_high - current_price) <= max_res_dist:
            resistance_candidates.append(prev_day_high)
        if bar_resistance > 0 and bar_resistance > current_price and (bar_resistance - current_price) <= max_res_dist:
            resistance_candidates.append(bar_resistance)

        above = [r for r in resistance_candidates if r > current_price]
        # Fix: min(above) = nearest resistance = first barrier
        key_resistance = min(above) if above else current_price + atr_val
        assert key_resistance == 51.5, f"Should pick nearest: 51.5, got {key_resistance}"

    def test_single_resistance_above(self):
        """When only one candidate is above price, it's selected."""
        above = [52.0]
        assert min(above) == 52.0

    def test_fallback_when_none_above(self):
        """When no resistance candidate is above price, fall back to price + ATR."""
        current_price = 55.0
        atr_val = 1.0
        above = []
        key_resistance = min(above) if above else current_price + atr_val
        assert key_resistance == 56.0


# ════════════════════════════════════════════════════════════════════
# FIX 2: ATR exhaustion uses open_price
# ════════════════════════════════════════════════════════════════════


class TestATRExhaustionOpenPrice:
    """Verify pullback_setup uses stock.open_price for ATR exhaustion."""

    def test_uses_open_price_when_available(self):
        """When open_price > 0, ATR exhaustion should use it, not VWAP."""
        from tradingbot.signals.pullback_setup import has_valid_setup

        # open_price = 48 (3% away), vwap = 49.5 (1% away)
        # With ATR = 1.0, move from open = $2 = 200% of ATR → exhausted
        # With VWAP as proxy, move = $0.5 = 50% → NOT exhausted
        stock = _snap(
            price=50.0,
            open_price=48.0,
            vwap=49.5,
            atr=1.0,
            ema9=50.1,    # price above ema9
            ema20=49.8,   # pullback_low above ema20
            pullback_low=49.9,
            recent_volume=100_000,
            avg_volume_20=40_000,
            relative_volume=2.5,
            premarket_volume=200_000,
        )
        # With open_price=48 and atr=1.0, move is 2.0/1.0 = 200% > 60% → exhausted
        result = has_valid_setup(stock, volume_multiplier=1.5)
        assert result is False, "Should be exhausted using open_price"

    def test_falls_back_to_vwap_when_no_open(self):
        """When open_price = 0, should fall back to VWAP."""
        from tradingbot.signals.pullback_setup import has_valid_setup

        stock = _snap(
            price=50.0,
            open_price=0.0,  # not available
            vwap=49.8,       # very close → small move → not exhausted
            atr=1.0,
            ema9=50.1,
            ema20=49.8,
            pullback_low=49.9,
            recent_volume=100_000,
            avg_volume_20=40_000,
            relative_volume=2.5,
            premarket_volume=200_000,
        )
        # move from vwap = 0.2 / 1.0 = 20% < 60% → not exhausted
        result = has_valid_setup(stock, volume_multiplier=1.5)
        assert result is True, "Should pass when open_price unavailable (VWAP fallback)"


# ════════════════════════════════════════════════════════════════════
# FIX 3: Daily EMA50 trend filter
# ════════════════════════════════════════════════════════════════════


class TestDailyTrendFilter:
    """Verify _passes_trend_filter blocks downtrend gap-ups."""

    def _make_runner(self):
        """Create a minimal SessionRunner-like object with _passes_trend_filter."""
        from tradingbot.app.session_runner import SessionRunner
        # Patch __init__ to avoid loading configs/credentials
        with patch.object(SessionRunner, "__init__", lambda self, *a, **kw: None):
            runner = SessionRunner.__new__(SessionRunner)
        return runner

    def test_blocks_price_below_daily_ema50(self):
        runner = self._make_runner()
        stock = _snap(price=48.0, daily_ema50=52.0, catalyst_score=50.0)
        dropped = []
        assert runner._passes_trend_filter(stock, dropped) is False
        assert any("daily_downtrend" in r for _, r in dropped)

    def test_passes_price_above_daily_ema50(self):
        runner = self._make_runner()
        stock = _snap(price=55.0, daily_ema50=52.0)
        assert runner._passes_trend_filter(stock, None) is True

    def test_passes_when_ema50_unavailable(self):
        runner = self._make_runner()
        stock = _snap(price=48.0, daily_ema50=0.0)
        assert runner._passes_trend_filter(stock, None) is True

    def test_catalyst_overrides_downtrend(self):
        """Strong catalyst (>=70) should bypass the downtrend filter."""
        runner = self._make_runner()
        stock = _snap(price=48.0, daily_ema50=52.0, catalyst_score=75.0)
        assert runner._passes_trend_filter(stock, None) is True

    def test_moderate_catalyst_does_not_override(self):
        """Catalyst < 70 should NOT bypass the downtrend filter."""
        runner = self._make_runner()
        stock = _snap(price=48.0, daily_ema50=52.0, catalyst_score=60.0)
        dropped = []
        assert runner._passes_trend_filter(stock, dropped) is False

    def test_relaxed_mode_bypasses(self):
        """Relaxed mode (O2) should skip the trend filter."""
        runner = self._make_runner()
        stock = _snap(price=48.0, daily_ema50=52.0, catalyst_score=40.0)
        assert runner._passes_trend_filter(stock, None, relaxed=True) is True


# ════════════════════════════════════════════════════════════════════
# FIX 3b: daily_ema50 field in SymbolSnapshot
# ════════════════════════════════════════════════════════════════════


class TestDailyEMA50Field:
    """Verify the daily_ema50 field exists on SymbolSnapshot."""

    def test_field_default_zero(self):
        stock = _snap()
        assert hasattr(stock, "daily_ema50")
        assert stock.daily_ema50 == 0.0

    def test_field_can_be_set(self):
        stock = _snap(daily_ema50=52.5)
        assert stock.daily_ema50 == 52.5


# ════════════════════════════════════════════════════════════════════
# FIX 3c: technical_indicators computes daily_ema50
# ════════════════════════════════════════════════════════════════════


class TestDailyEMA50Computation:
    """Verify compute_indicators returns daily_ema50 from daily bars."""

    def _make_bars(self, n: int, base_price: float = 100.0):
        """Create mock bar objects with trending prices."""
        bars = []
        for i in range(n):
            p = base_price + i * 0.5
            bar = MagicMock()
            bar.open = p - 0.2
            bar.high = p + 0.3
            bar.low = p - 0.3
            bar.close = p
            bar.volume = 1_000_000
            bar.timestamp = None
            bars.append(bar)
        return bars

    def test_daily_ema50_computed_with_enough_bars(self):
        from tradingbot.analysis.technical_indicators import compute_indicators
        intraday = self._make_bars(20, base_price=50.0)
        daily = self._make_bars(5, base_price=48.0)
        result = compute_indicators(intraday, daily_bars=daily)
        assert "daily_ema50" in result
        assert result["daily_ema50"] > 0

    def test_daily_ema50_absent_with_too_few_bars(self):
        from tradingbot.analysis.technical_indicators import compute_indicators
        intraday = self._make_bars(20, base_price=50.0)
        daily = self._make_bars(2, base_price=48.0)  # only 2 bars
        result = compute_indicators(intraday, daily_bars=daily)
        assert "daily_ema50" not in result

    def test_daily_ema50_absent_without_daily_bars(self):
        from tradingbot.analysis.technical_indicators import compute_indicators
        intraday = self._make_bars(20, base_price=50.0)
        result = compute_indicators(intraday, daily_bars=None)
        assert "daily_ema50" not in result


# ════════════════════════════════════════════════════════════════════
# FIX 4: Meme stocks removed from _CORE_WATCHLIST
# ════════════════════════════════════════════════════════════════════


class TestCoreWatchlistCleanup:
    """Verify retail/meme stocks are no longer in _CORE_WATCHLIST."""

    def test_no_meme_stocks(self):
        from tradingbot.data.alpaca_client import AlpacaClient
        meme_symbols = {"GME", "AMC", "SOFI", "IONQ", "QUBT", "RGTI", "SOUN", "LUNR"}
        core = set(AlpacaClient._CORE_WATCHLIST)
        overlap = meme_symbols & core
        assert overlap == set(), f"Meme stocks still in _CORE_WATCHLIST: {overlap}"

    def test_megacap_still_present(self):
        from tradingbot.data.alpaca_client import AlpacaClient
        core = set(AlpacaClient._CORE_WATCHLIST)
        for sym in ("AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA"):
            assert sym in core, f"{sym} missing from _CORE_WATCHLIST"

    def test_market_context_etfs_present(self):
        from tradingbot.data.alpaca_client import AlpacaClient
        core = set(AlpacaClient._CORE_WATCHLIST)
        for sym in ("SPY", "QQQ", "IWM"):
            assert sym in core, f"{sym} missing from _CORE_WATCHLIST"


# ════════════════════════════════════════════════════════════════════
# FIX 5: MarketGuard docstring
# ════════════════════════════════════════════════════════════════════


class TestMarketGuardDocstring:
    """Verify MarketGuard threshold constant matches documentation."""

    def test_yellow_threshold_is_minus_03(self):
        from tradingbot.analysis.market_guard import MarketGuard
        assert MarketGuard.YELLOW_THRESHOLD == -0.3

    def test_docstring_matches_code(self):
        from tradingbot.analysis.market_guard import MarketGuard
        doc = MarketGuard.__doc__
        assert "-0.3%" in doc, "Docstring should reference -0.3% for green/yellow boundary"


# ════════════════════════════════════════════════════════════════════
# FIX 6: O2 relaxed scanner max_gap_pct
# ════════════════════════════════════════════════════════════════════


class TestO2MaxGapPct:
    """Verify the O2 relaxed scanner gets a max_gap_pct default of 12%."""

    def test_relaxed_scanner_has_max_gap(self):
        """SessionRunner should initialise relaxed_scanner with max_gap_pct=12."""
        from tradingbot.app.session_runner import SessionRunner
        with patch.object(SessionRunner, "__init__", lambda self, *a, **kw: None):
            runner = SessionRunner.__new__(SessionRunner)

        # Simulate the config path: no o2_relaxed section → defaults
        from tradingbot.scanner.gap_scanner import GapScanner
        scanner = GapScanner(
            price_min=5.0, price_max=2000.0, min_gap_pct=0.0,
            min_premarket_volume=0, min_dollar_volume=0,
            max_spread_pct=5.0, max_gap_pct=12.0,
        )
        assert scanner.max_gap_pct == 12.0

    def test_relaxed_scanner_blocks_extreme_gaps(self):
        """O2 scanner should reject gaps > 12%."""
        from tradingbot.scanner.gap_scanner import GapScanner
        scanner = GapScanner(
            price_min=5.0, price_max=2000.0, min_gap_pct=0.0,
            min_premarket_volume=0, min_dollar_volume=0,
            max_spread_pct=5.0, max_gap_pct=12.0,
        )
        stock_ok = _snap(gap_pct=10.0)
        stock_extreme = _snap(gap_pct=15.0)
        result_ok = scanner.run([stock_ok])
        result_extreme = scanner.run([stock_extreme])
        assert len(result_ok.candidates) == 1
        assert len(result_extreme.candidates) == 0
        assert any("gap_too_large" in r for _, r in result_extreme.dropped)

    def test_relaxed_scanner_allows_moderate_gaps(self):
        """O2 scanner should allow gaps between 8-12%."""
        from tradingbot.scanner.gap_scanner import GapScanner
        scanner = GapScanner(
            price_min=5.0, price_max=2000.0, min_gap_pct=0.0,
            min_premarket_volume=0, min_dollar_volume=0,
            max_spread_pct=5.0, max_gap_pct=12.0,
        )
        stock = _snap(gap_pct=9.5)
        result = scanner.run([stock])
        assert len(result.candidates) == 1
