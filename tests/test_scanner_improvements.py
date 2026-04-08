"""Tests for scanner improvements: leveraged ETF block, momentum scanner, quality gap threshold."""
from __future__ import annotations

import pytest

from tradingbot.models import SymbolSnapshot
from tradingbot.scanner.gap_scanner import GapScanner
from tradingbot.scanner.momentum_scanner import MomentumScanner
from tradingbot.data.etf_metadata import (
    get_leverage_factor,
    is_leveraged_etf,
    is_etf,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _snap(
    symbol: str = "TEST",
    price: float = 50.0,
    gap_pct: float = 2.0,
    premarket_volume: int = 100_000,
    dollar_volume: float = 1_000_000.0,
    spread_pct: float = 0.5,
    relative_volume: float = 2.0,
    catalyst_score: float = 60.0,
    vwap: float = 49.0,
    open_price: float = 48.0,
    intraday_change_pct: float = 4.0,
) -> SymbolSnapshot:
    """Create a SymbolSnapshot with sensible defaults for testing."""
    return SymbolSnapshot(
        symbol=symbol,
        price=price,
        gap_pct=gap_pct,
        premarket_volume=premarket_volume,
        dollar_volume=dollar_volume,
        spread_pct=spread_pct,
        relative_volume=relative_volume,
        catalyst_score=catalyst_score,
        ema9=price * 0.99,
        ema20=price * 0.98,
        vwap=vwap,
        recent_volume=50_000,
        avg_volume_20=30_000,
        pullback_low=price * 0.97,
        reclaim_level=price * 1.01,
        pullback_high=price * 1.03,
        open_price=open_price,
        intraday_change_pct=intraday_change_pct,
    )


# ═══════════════════════════════════════════════════════════════════
# Fix 1: Block bull leveraged ETFs
# ═══════════════════════════════════════════════════════════════════

class TestLeveragedETFBlock:
    """Verify that leveraged ETFs (bull AND inverse) are correctly identified."""

    def test_tqqq_is_leveraged(self):
        assert is_leveraged_etf("TQQQ") is True
        assert get_leverage_factor("TQQQ") == 3

    def test_soxl_is_leveraged(self):
        assert is_leveraged_etf("SOXL") is True
        assert get_leverage_factor("SOXL") == 3

    def test_sqqq_is_leveraged_inverse(self):
        assert is_leveraged_etf("SQQQ") is True
        assert get_leverage_factor("SQQQ") == -3

    def test_spy_is_not_leveraged(self):
        """SPY is a 1x ETF — should NOT be blocked."""
        assert is_leveraged_etf("SPY") is False
        assert get_leverage_factor("SPY") == 1

    def test_qqq_is_not_leveraged(self):
        assert is_leveraged_etf("QQQ") is False

    def test_aapl_is_not_leveraged(self):
        """Individual stocks return leverage=1 and are not leveraged."""
        assert is_leveraged_etf("AAPL") is False
        assert get_leverage_factor("AAPL") == 1

    def test_all_leveraged_etfs_detected(self):
        """Every entry in LEVERAGED_ETFS with abs(lev) > 1 must be flagged."""
        from tradingbot.data.etf_metadata import LEVERAGED_ETFS
        for sym, lev in LEVERAGED_ETFS.items():
            if abs(lev) > 1:
                assert is_leveraged_etf(sym), f"{sym} (lev={lev}) should be leveraged"

    def test_1x_inverse_etfs_not_flagged_as_leveraged(self):
        """1x inverse ETFs (SH, PSQ, DOG) have lev=-1, abs=1 → not 'leveraged'."""
        for sym in ["SH", "PSQ", "DOG"]:
            assert is_leveraged_etf(sym) is False, f"{sym} is 1x inverse, not leveraged"


# ═══════════════════════════════════════════════════════════════════
# Fix 2: Intraday Momentum Scanner
# ═══════════════════════════════════════════════════════════════════

class TestMomentumScanner:
    """Verify MomentumScanner filters work correctly."""

    def setup_method(self):
        self.scanner = MomentumScanner(
            price_min=5.0,
            price_max=2000.0,
            min_intraday_change_pct=1.5,
            min_relative_volume=1.3,
            min_dollar_volume=500_000.0,
            max_spread_pct=2.0,
            require_above_vwap=True,
        )

    def test_strong_intraday_runner_passes(self):
        """AAPL up 3% from open, above VWAP, good volume → should pass."""
        snap = _snap("AAPL", price=205, open_price=199, intraday_change_pct=3.0,
                      relative_volume=1.8, dollar_volume=5_000_000, vwap=202)
        result = self.scanner.run([snap])
        assert len(result.candidates) == 1
        assert result.candidates[0].symbol == "AAPL"

    def test_flat_stock_rejected(self):
        """Stock with only 0.5% intraday change → too small."""
        snap = _snap("FLAT", intraday_change_pct=0.5)
        result = self.scanner.run([snap])
        assert len(result.candidates) == 0
        assert any("intraday_change_too_small" in r for _, r in result.dropped)

    def test_low_volume_rejected(self):
        """Good move but low volume → no confirmation."""
        snap = _snap("LOWVOL", intraday_change_pct=3.0, relative_volume=0.8)
        result = self.scanner.run([snap])
        assert len(result.candidates) == 0
        assert any("low_relative_volume" in r for _, r in result.dropped)

    def test_below_vwap_rejected(self):
        """Stock above open but below VWAP → sellers absorbing."""
        snap = _snap("FADE", price=50, intraday_change_pct=2.0, vwap=51)
        result = self.scanner.run([snap])
        assert len(result.candidates) == 0
        assert any("below_vwap" in r for _, r in result.dropped)

    def test_below_vwap_allowed_when_disabled(self):
        """If require_above_vwap=False, below-VWAP stocks can pass."""
        scanner = MomentumScanner(require_above_vwap=False)
        snap = _snap("FADE", price=50, intraday_change_pct=2.0, vwap=51)
        result = scanner.run([snap])
        assert len(result.candidates) == 1

    def test_penny_stock_rejected(self):
        """Price below $5 floor → rejected."""
        snap = _snap("CHEAP", price=3.5, intraday_change_pct=10.0)
        result = self.scanner.run([snap])
        assert len(result.candidates) == 0
        assert any("price_out_of_range" in r for _, r in result.dropped)

    def test_low_dollar_volume_rejected(self):
        snap = _snap("ILLIQUID", intraday_change_pct=3.0, dollar_volume=100_000)
        result = self.scanner.run([snap])
        assert len(result.candidates) == 0

    def test_wide_spread_rejected(self):
        snap = _snap("WIDE", intraday_change_pct=3.0, spread_pct=3.5)
        result = self.scanner.run([snap])
        assert len(result.candidates) == 0

    def test_multiple_candidates_ranked(self):
        """Multiple passing stocks are all returned."""
        snaps = [
            _snap("AAPL", intraday_change_pct=3.0, relative_volume=2.0, vwap=48),
            _snap("MSFT", intraday_change_pct=2.5, relative_volume=1.5, vwap=48),
            _snap("FLAT", intraday_change_pct=0.3, relative_volume=1.5, vwap=48),
        ]
        result = self.scanner.run(snaps)
        assert len(result.candidates) == 2
        symbols = {c.symbol for c in result.candidates}
        assert symbols == {"AAPL", "MSFT"}


# ═══════════════════════════════════════════════════════════════════
# Fix 3: Quality symbol lower gap threshold
# ═══════════════════════════════════════════════════════════════════

class TestQualityGapThreshold:
    """Verify GapScanner applies lower gap threshold for quality symbols."""

    def setup_method(self):
        self.scanner = GapScanner(
            price_min=5.0,
            price_max=2000.0,
            min_gap_pct=0.5,
            min_premarket_volume=50_000,
            min_dollar_volume=500_000.0,
            max_spread_pct=2.0,
            min_gap_pct_quality=0.2,
            quality_symbols={"AAPL", "MSFT", "NVDA", "GOOGL"},
        )

    def test_quality_symbol_passes_with_small_gap(self):
        """AAPL with 0.3% gap should pass (quality threshold = 0.2%)."""
        snap = _snap("AAPL", gap_pct=0.3)
        result = self.scanner.run([snap])
        assert len(result.candidates) == 1

    def test_non_quality_symbol_rejected_with_small_gap(self):
        """RGTI with 0.3% gap should be rejected (standard threshold = 0.5%)."""
        snap = _snap("RGTI", gap_pct=0.3)
        result = self.scanner.run([snap])
        assert len(result.candidates) == 0

    def test_non_quality_symbol_passes_with_large_gap(self):
        """Any symbol with 1% gap passes the standard threshold."""
        snap = _snap("RGTI", gap_pct=1.0)
        result = self.scanner.run([snap])
        assert len(result.candidates) == 1

    def test_quality_symbol_rejected_below_quality_threshold(self):
        """AAPL with 0.1% gap is below even the quality threshold."""
        snap = _snap("AAPL", gap_pct=0.1)
        result = self.scanner.run([snap])
        assert len(result.candidates) == 0

    def test_no_quality_symbols_uses_standard_threshold(self):
        """Without quality_symbols set, all use the standard threshold."""
        scanner = GapScanner(
            price_min=5.0, price_max=2000.0, min_gap_pct=0.5,
            min_premarket_volume=50_000, min_dollar_volume=500_000.0,
            max_spread_pct=2.0,
        )
        snap = _snap("AAPL", gap_pct=0.3)
        result = scanner.run([snap])
        assert len(result.candidates) == 0  # 0.3 < 0.5

    def test_backward_compatible_without_new_params(self):
        """GapScanner works without the new optional params (backward compat)."""
        scanner = GapScanner(
            price_min=5.0, price_max=2000.0, min_gap_pct=0.5,
            min_premarket_volume=50_000, min_dollar_volume=500_000.0,
            max_spread_pct=2.0,
        )
        snap = _snap("TEST", gap_pct=1.0)
        result = scanner.run([snap])
        assert len(result.candidates) == 1


# ═══════════════════════════════════════════════════════════════════
# SymbolSnapshot new fields
# ═══════════════════════════════════════════════════════════════════

class TestSymbolSnapshotNewFields:
    """Verify new open_price and intraday_change_pct fields."""

    def test_default_values(self):
        """New fields default to 0.0 for backward compat."""
        snap = SymbolSnapshot(
            "TEST", 100, 2.0, 100000, 1000000, 0.5, 2.0, 60,
            99, 98, 99.5, 50000, 30000, 97, 101, 103,
        )
        assert snap.open_price == 0.0
        assert snap.intraday_change_pct == 0.0

    def test_explicit_values(self):
        snap = _snap("AAPL", open_price=195.0, intraday_change_pct=3.5)
        assert snap.open_price == 195.0
        assert snap.intraday_change_pct == 3.5
