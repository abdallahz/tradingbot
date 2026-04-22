"""Unit tests for CardBuilder filter chain.

Each filter is tested in isolation — no SessionRunner required.
Covers the happy path, rejection path, and any bypass conditions.
"""
from __future__ import annotations

import pytest
from tradingbot.app.card_builder import CardBuilder
from tradingbot.models import SymbolSnapshot


# ── Helpers ─────────────────────────────────────────────────────────

def _snap(
    symbol: str = "TEST",
    price: float = 50.0,
    gap_pct: float = 3.0,
    **overrides,
) -> SymbolSnapshot:
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
        daily_ema50=0.0,
        patterns=["above_vwap"],
        raw_bars=[],
        tech_indicators={"rsi": 55.0, "macd_hist": 0.01, "vwap": price * 0.99},
    )
    defaults.update(overrides)
    return SymbolSnapshot(**defaults)


@pytest.fixture
def builder():
    return CardBuilder(catalyst_bypass_score=70)


# ── passes_dedup ─────────────────────────────────────────────────────

class TestPassesDedup:
    def test_new_symbol_always_passes(self, builder):
        sym = _snap("AAPL")
        assert builder.passes_dedup(sym, {}, []) is True

    def test_previously_alerted_no_pullback_drops(self, builder):
        sym = _snap("AAPL", price=50.0)
        dropped = []
        result = builder.passes_dedup(sym, {"AAPL": 50.0}, dropped)
        assert result is False
        assert any("dedup" in r for _, r in dropped)

    def test_first_alert_entry_zero_passes(self, builder):
        sym = _snap("AAPL", price=50.0)
        assert builder.passes_dedup(sym, {"AAPL": 0.0}, []) is True

    def test_dropped_list_none_does_not_raise(self, builder):
        sym = _snap("AAPL", price=50.0)
        builder.passes_dedup(sym, {"AAPL": 50.0}, None)

    def test_reentry_cap_blocks_after_two_alerts(self, builder):
        # Symbol alerted twice today → third re-entry blocked regardless of setup
        sym = _snap("AAPL", price=48.0)
        dropped = []
        result = builder.passes_dedup(sym, {"AAPL": 50.0}, dropped, alert_counts={"AAPL": 2})
        assert result is False
        assert any("reentry_cap" in r for _, r in dropped)

    def test_reentry_cap_allows_first_reentry(self, builder):
        # Only 1 alert today — re-entry still evaluated (may fail pullback check
        # but cap itself does not block it)
        sym = _snap(
            "AAPL", price=48.0,
            reclaim_level=50.0,  # price == high → not a pullback
        )
        # Cap should not fire (count=1), pullback check decides the outcome
        dropped = []
        builder.passes_dedup(sym, {"AAPL": 50.0}, dropped, alert_counts={"AAPL": 1})
        assert not any("reentry_cap" in r for _, r in dropped)

    def test_below_original_stop_drops(self, builder):
        # Price still below stop level → breakdown ongoing, not a shakeout
        sym = _snap("AAPL", price=48.0)
        dropped = []
        result = builder.passes_dedup(
            sym, {"AAPL": 52.0}, dropped,
            stopped_data={"AAPL": {"hod": 54.0, "stop": 49.0}},
        )
        assert result is False
        assert any("below_stop" in r for _, r in dropped)

    def test_above_original_stop_proceeds_to_pullback_check(self, builder):
        # Price above stop — reclaim confirmed, pullback check decides
        sym = _snap("AAPL", price=50.5, reclaim_level=50.0)  # at high → not a pullback
        dropped = []
        # Reclaim check passes (50.5 > stop 49.0), but pullback won't qualify
        builder.passes_dedup(
            sym, {"AAPL": 52.0}, dropped,
            stopped_data={"AAPL": {"hod": 54.0, "stop": 49.0}},
        )
        assert not any("below_stop" in r for _, r in dropped)


# ── passes_etf_limits ────────────────────────────────────────────────

class TestPassesETFLimits:
    def test_non_etf_always_passes(self, builder):
        sym = _snap("AAPL")
        assert builder.passes_etf_limits(sym, 99, set(), []) is True

    def test_etf_below_cap_passes(self, builder):
        sym = _snap("SPY")
        assert builder.passes_etf_limits(sym, 0, set(), []) is True

    def test_etf_at_cap_drops(self, builder):
        b = CardBuilder(max_etf_alerts=3)
        sym = _snap("SPY")
        dropped = []
        assert b.passes_etf_limits(sym, 3, set(), dropped) is False
        assert any("etf_concentration_cap" in r for _, r in dropped)

    def test_etf_family_duplicate_drops(self, builder):
        sym = _snap("SQQQ")
        from tradingbot.data.etf_metadata import get_etf_family
        family = get_etf_family("SQQQ")
        if family:
            dropped = []
            assert builder.passes_etf_limits(sym, 0, {family}, dropped) is False
            assert any("etf_family_dup" in r for _, r in dropped)


# ── passes_intraday_extension ────────────────────────────────────────

class TestPassesIntradayExtension:
    def test_flat_or_down_always_passes(self, builder):
        sym = _snap(intraday_change_pct=0.0)
        assert builder.passes_intraday_extension(sym, []) is True

        sym = _snap(intraday_change_pct=-2.0)
        assert builder.passes_intraday_extension(sym, []) is True

    def test_under_limit_passes(self, builder):
        sym = _snap(intraday_change_pct=5.9)
        assert builder.passes_intraday_extension(sym, []) is True

    def test_over_limit_drops(self, builder):
        b = CardBuilder(max_intraday_change=6.0)
        # reclaim_level == price → stock is still at its high (not pulling back),
        # so evaluate_pullback_reentry returns qualifies=False and the drop fires.
        sym = _snap(intraday_change_pct=7.0, patterns=[], reclaim_level=50.0)
        dropped = []
        assert b.passes_intraday_extension(sym, dropped) is False
        assert any("intraday_extended" in r for _, r in dropped)

    def test_tuning_override_respected(self, builder):
        sym = _snap(intraday_change_pct=7.0, patterns=[])
        dropped = []
        # Override max to 10% — should pass
        result = builder.passes_intraday_extension(
            sym, dropped, tuning_overrides={"max_intraday_change_pct": 10.0}
        )
        assert result is True
        assert dropped == []


# ── passes_vwap_distance ─────────────────────────────────────────────

class TestPassesVWAPDistance:
    def test_no_vwap_passes(self, builder):
        sym = _snap(vwap=0.0)
        assert builder.passes_vwap_distance(sym, []) is True

    def test_within_morning_limit_passes(self, builder):
        sym = _snap(price=50.0, vwap=49.0)  # 2% — below 3% morning limit
        assert builder.passes_vwap_distance(sym, [], session_tag="morning") is True

    def test_exceeds_morning_limit_drops(self, builder):
        b = CardBuilder(vwap_distance_morning=3.0)
        sym = _snap(price=50.0, vwap=48.0)  # 4.1% distance
        dropped = []
        assert b.passes_vwap_distance(sym, dropped, session_tag="morning") is False
        assert any("vwap_extended" in r for _, r in dropped)

    def test_midday_uses_wider_limit(self, builder):
        b = CardBuilder(vwap_distance_midday=5.0)
        sym = _snap(price=50.0, vwap=47.65)  # 4.93% — passes midday (5%) but not morning (3%)
        assert b.passes_vwap_distance(sym, [], session_tag="midday") is True
        dropped = []
        assert b.passes_vwap_distance(sym, dropped, session_tag="morning") is False

    def test_tuning_override_respected(self, builder):
        sym = _snap(price=50.0, vwap=45.0)  # 10% distance — normally rejected
        result = builder.passes_vwap_distance(
            sym, [], session_tag="midday",
            tuning_overrides={"max_vwap_distance_pct": 15.0},
        )
        assert result is True


# ── passes_catalyst_gate ─────────────────────────────────────────────

class TestPassesCatalystGate:
    def test_high_catalyst_passes(self, builder):
        sym = _snap(catalyst_score=60.0)
        assert builder.passes_catalyst_gate(sym, can_long=True, dropped=[]) is True

    def test_low_catalyst_low_volume_drops(self, builder):
        sym = _snap(catalyst_score=20.0, relative_volume=1.0)
        dropped = []
        assert builder.passes_catalyst_gate(sym, can_long=True, dropped=dropped) is False
        assert any("low_catalyst" in r for _, r in dropped)

    def test_low_catalyst_strong_volume_with_setup_passes(self, builder):
        # Strong volume compensates for weak catalyst
        sym = _snap(catalyst_score=20.0, relative_volume=4.0, premarket_volume=200_000)
        assert builder.passes_catalyst_gate(sym, can_long=True, dropped=[]) is True

    def test_strong_volume_without_setup_drops(self, builder):
        sym = _snap(catalyst_score=20.0, relative_volume=4.0, premarket_volume=200_000)
        dropped = []
        assert builder.passes_catalyst_gate(sym, can_long=False, dropped=dropped) is False


# ── passes_gap_fade_check ────────────────────────────────────────────

class TestPassesGapFadeCheck:
    def test_no_gap_passes(self, builder):
        sym = _snap(gap_pct=-1.0)
        assert builder.passes_gap_fade_check(sym, []) is True

    def test_gap_above_vwap_passes(self, builder):
        sym = _snap(price=50.0, vwap=49.0, gap_pct=3.0)
        assert builder.passes_gap_fade_check(sym, []) is True

    def test_gap_below_vwap_drops(self, builder):
        sym = _snap(price=47.0, vwap=49.0, gap_pct=3.0)
        dropped = []
        assert builder.passes_gap_fade_check(sym, dropped) is False
        assert any("gap_fade" in r for _, r in dropped)

    def test_relaxed_mode_bypasses(self, builder):
        sym = _snap(price=47.0, vwap=49.0, gap_pct=3.0)
        assert builder.passes_gap_fade_check(sym, [], relaxed=True) is True


# ── passes_trend_filter ──────────────────────────────────────────────

class TestPassesTrendFilter:
    def test_no_ema50_passes(self, builder):
        sym = _snap(price=50.0, daily_ema50=0.0)
        assert builder.passes_trend_filter(sym, []) is True

    def test_price_above_ema50_passes(self, builder):
        sym = _snap(price=55.0, daily_ema50=52.0)
        assert builder.passes_trend_filter(sym, []) is True

    def test_price_below_ema50_drops(self, builder):
        sym = _snap(price=48.0, daily_ema50=52.0, catalyst_score=50.0)
        dropped = []
        assert builder.passes_trend_filter(sym, dropped) is False
        assert any("daily_downtrend" in r for _, r in dropped)

    def test_high_catalyst_bypasses_downtrend(self, builder):
        b = CardBuilder(catalyst_bypass_score=70)
        sym = _snap(price=48.0, daily_ema50=52.0, catalyst_score=75.0)
        assert b.passes_trend_filter(sym, []) is True

    def test_moderate_catalyst_does_not_bypass(self, builder):
        b = CardBuilder(catalyst_bypass_score=70)
        sym = _snap(price=48.0, daily_ema50=52.0, catalyst_score=65.0)
        assert b.passes_trend_filter(sym, []) is False

    def test_relaxed_mode_bypasses_all(self, builder):
        sym = _snap(price=48.0, daily_ema50=52.0, catalyst_score=10.0)
        assert builder.passes_trend_filter(sym, [], relaxed=True) is True

    def test_dropped_none_does_not_raise(self, builder):
        sym = _snap(price=48.0, daily_ema50=52.0, catalyst_score=10.0)
        builder.passes_trend_filter(sym, None)
