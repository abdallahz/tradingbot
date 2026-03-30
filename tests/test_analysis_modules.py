"""
test_analysis_modules.py — Unit tests for the gap-analysis modules:
    - volume_quality.py
    - confluence_engine.py
    - institutional_alert.py
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest


# ═══════════════════════════════════════════════════════════════════════
#  Volume Quality Tests
# ═══════════════════════════════════════════════════════════════════════
from tradingbot.analysis.volume_quality import (
    classify_volume_profile,
    is_move_exhausted,
    compute_volume_quality_score,
    VolumeProfile,
)


def _make_bars(
    count: int = 10,
    base_price: float = 100.0,
    base_vol: int = 10000,
    up_trend: bool = True,
    vol_increasing: bool = True,
    range_expanding: bool = False,
):
    """Helper: generate synthetic bars with controllable features."""
    bars = []
    for i in range(count):
        delta = (i * 0.5) if up_trend else -(i * 0.5)
        vol_mult = (1.0 + i * 0.10) if vol_increasing else (1.0 - i * 0.05)
        vol = max(100, int(base_vol * max(0.1, vol_mult)))
        rng = (1.0 + i * 0.1) if range_expanding else 1.0
        o = base_price + delta
        c = o + 0.3 * rng  # closes higher — up bar
        h = max(o, c) + 0.2 * rng
        l = min(o, c) - 0.2 * rng
        bars.append(SimpleNamespace(
            open=o, close=c, high=h, low=l, volume=vol,
        ))
    return bars


class TestClassifyVolumeProfile:
    def test_insufficient_bars_returns_unknown(self):
        result = classify_volume_profile([], 2.0, 105.0, 102.0)
        assert result.classification == "unknown"
        assert "Insufficient" in result.reason

    def test_few_bars_returns_unknown(self):
        bars = _make_bars(count=3)
        result = classify_volume_profile(bars, 2.0, 105.0, 102.0)
        assert result.classification == "unknown"

    def test_accumulation_detected(self):
        """Up-trending prices, increasing volume, closes above opens."""
        bars = _make_bars(count=10, up_trend=True, vol_increasing=True)
        result = classify_volume_profile(bars, 3.0, 105.0, 104.5)
        assert result.classification == "accumulation"
        assert result.score >= 70

    def test_thin_fade_detected(self):
        """Low volume, contracting range, decreasing volume."""
        bars = []
        for i in range(10):
            rng = max(0.1, 1.0 - i * 0.08)
            # Steep volume decrease so vol_trend registers as "decreasing"
            vol = max(100, int(10000 * max(0.05, (1.0 - i * 0.12))))
            o = 100.0 + i * 0.1
            # Alternate up/down closes to keep up_ratio below accumulation
            c = o - 0.05 if i % 2 else o + 0.02
            bars.append(SimpleNamespace(
                open=o, close=c, high=o + rng * 0.5, low=o - rng * 0.5, volume=vol,
            ))
        result = classify_volume_profile(bars, 0.8, 101.0, 100.0)
        assert result.classification == "thin_fade"
        assert result.score <= 30

    def test_climax_volume_detected(self):
        """Extreme relative volume with expanding bars."""
        bars = _make_bars(count=10, range_expanding=True, vol_increasing=True)
        result = classify_volume_profile(bars, 6.0, 105.0, 102.0)
        assert result.classification == "climax"
        assert result.score <= 40

    def test_attribute_error_returns_unknown(self):
        """Non-bar objects should be handled gracefully."""
        bars = [{"close": 100, "high": 101, "low": 99, "volume": 1000}] * 10
        result = classify_volume_profile(bars, 2.0, 100.0, 100.0)
        assert result.classification == "unknown"
        assert "attribute error" in result.reason.lower()

    def test_relative_volume_bonus(self):
        """High relative volume should boost score."""
        bars = _make_bars(count=10, up_trend=True, vol_increasing=True)
        lo_rv = classify_volume_profile(bars, 0.8, 105.0, 104.5)
        hi_rv = classify_volume_profile(bars, 3.0, 105.0, 104.5)
        assert hi_rv.score >= lo_rv.score


class TestIsMoveExhausted:
    def test_not_exhausted_when_plenty_of_range(self):
        is_exh, reason = is_move_exhausted(
            current_price=102.0, open_price=100.0, atr=5.0,
            spread_pct=0.05, high_of_day=102.5,
        )
        assert not is_exh

    def test_exhausted_when_atr_mostly_used(self):
        is_exh, reason = is_move_exhausted(
            current_price=104.5, open_price=100.0, atr=5.0,
            spread_pct=0.05,
        )
        assert is_exh
        assert "consumed" in reason.lower()

    def test_zero_atr_not_exhausted(self):
        is_exh, _ = is_move_exhausted(100, 100, 0, 0.1)
        assert not is_exh

    def test_wide_spread_exhaustion(self):
        """Spread eating >50% of remaining ATR should flag exhaustion."""
        is_exh, reason = is_move_exhausted(
            current_price=103.5, open_price=100.0, atr=5.0,
            spread_pct=1.0,  # 1% spread on a $103 stock → $1.04 vs $1.50 remaining
        )
        assert is_exh
        assert "spread" in reason.lower()

    def test_retracement_exhaustion(self):
        """Move from open + large retracement from HOD should flag."""
        is_exh, reason = is_move_exhausted(
            current_price=102.0, open_price=100.0, atr=3.0,
            spread_pct=0.05, high_of_day=104.5,
        )
        assert is_exh
        assert "retrace" in reason.lower()


class TestComputeVolumeQualityScore:
    def test_returns_tuple(self):
        bars = _make_bars(count=10)
        score, summary = compute_volume_quality_score(
            bars_data=bars, relative_volume=2.5, current_price=105.0,
            vwap=104.0, atr=3.0, spread_pct=0.1, open_price=100.0,
        )
        assert isinstance(score, float)
        assert 0 <= score <= 100
        assert isinstance(summary, str)
        assert "Vol:" in summary

    def test_exhausted_move_lowers_score(self):
        bars = _make_bars(count=10)
        fresh = compute_volume_quality_score(
            bars, 2.5, 101.0, 100.5, 5.0, 0.1, 100.0,
        )
        exhausted = compute_volume_quality_score(
            bars, 2.5, 104.5, 100.5, 5.0, 0.1, 100.0,
        )
        assert fresh[0] > exhausted[0]


# ═══════════════════════════════════════════════════════════════════════
#  Confluence Engine Tests
# ═══════════════════════════════════════════════════════════════════════
from tradingbot.analysis.confluence_engine import (
    evaluate_confluence,
    should_fire_alert,
    ConfluenceResult,
    _grade_score,
)


class TestGradeScore:
    def test_grade_boundaries(self):
        assert _grade_score(80) == "A"
        assert _grade_score(75) == "A"
        assert _grade_score(74.9) == "B"
        assert _grade_score(55) == "B"
        assert _grade_score(40) == "C"
        assert _grade_score(39.9) == "F"
        assert _grade_score(0) == "F"


class TestEvaluateConfluence:
    def _good_setup(self, **overrides):
        """Baseline params for a high-confidence long setup."""
        defaults = dict(
            current_price=105.0, open_price=100.0,
            ema9=104.0, ema20=103.0, vwap=102.0,
            atr=5.0, spread_pct=0.05,
            bars_data=_make_bars(10, up_trend=True, vol_increasing=True),
            relative_volume=3.0,
            spy_change_pct=0.5, qqq_change_pct=0.3,
            rsi=58.0, macd_hist=0.3,
            catalyst_score=65.0,
            patterns=["breakout"],
            gap_pct=4.0,
        )
        defaults.update(overrides)
        return defaults

    def test_strong_setup_gets_high_grade(self):
        result = evaluate_confluence(**self._good_setup())
        assert result.grade in ("A", "B")
        assert result.composite_score >= 55

    def test_five_factors_are_present(self):
        result = evaluate_confluence(**self._good_setup())
        factor_names = {f.name for f in result.factors}
        assert "Volume Profile" in factor_names
        assert "Market Trend" in factor_names
        assert "ATR Exhaustion" in factor_names
        assert "Technical Stack" in factor_names
        assert "Catalyst Strength" in factor_names
        assert len(result.factors) == 5

    def test_thin_fade_triggers_veto(self):
        """Thin-fade volume should veto the alert."""
        bars = []
        for i in range(10):
            rng = max(0.1, 1.0 - i * 0.08)
            vol = max(100, int(10000 * max(0.05, (1.0 - i * 0.12))))
            o = 100 + i * 0.1
            c = o - 0.05 if i % 2 else o + 0.02
            bars.append(SimpleNamespace(
                open=o, close=c, high=o + rng * 0.5, low=o - rng * 0.5, volume=vol,
            ))
        result = evaluate_confluence(
            **self._good_setup(
                bars_data=bars, relative_volume=0.8,
                # Keep ATR headroom so volume veto is the trigger
                current_price=101.0, open_price=100.0, atr=5.0,
            )
        )
        assert result.vetoed
        assert "Volume Profile" in result.veto_reason

    def test_market_crash_flags_false_positive(self):
        result = evaluate_confluence(
            **self._good_setup(spy_change_pct=-2.0, qqq_change_pct=-1.8)
        )
        # Should produce warning
        has_crash_warning = any("MARKET CRASH" in w for w in result.false_positive_flags)
        assert has_crash_warning
        # Market factor should have very low score
        mkt_factor = [f for f in result.factors if f.name == "Market Trend"][0]
        assert mkt_factor.score <= 15

    def test_overbought_rsi_warning(self):
        result = evaluate_confluence(**self._good_setup(rsi=78))
        has_rsi_warning = any("RSI OVERBOUGHT" in w for w in result.false_positive_flags)
        assert has_rsi_warning

    def test_gap_without_catalyst_warning(self):
        result = evaluate_confluence(
            **self._good_setup(gap_pct=12.0, catalyst_score=20.0)
        )
        has_gap_warning = any("GAP WITHOUT NEWS" in w for w in result.false_positive_flags)
        assert has_gap_warning

    def test_below_vwap_warning(self):
        result = evaluate_confluence(**self._good_setup(vwap=110.0))
        has_vwap_warning = any("BELOW VWAP" in w for w in result.false_positive_flags)
        assert has_vwap_warning

    def test_summary_includes_grade(self):
        result = evaluate_confluence(**self._good_setup())
        assert f"Grade {result.grade}" in result.summary

    def test_empty_bars_does_not_crash(self):
        result = evaluate_confluence(**self._good_setup(bars_data=[]))
        assert result.grade in ("A", "B", "C", "F")


class TestShouldFireAlert:
    def test_vetoed_never_fires(self):
        r = ConfluenceResult(composite_score=90.0, grade="A", vetoed=True, veto_reason="test")
        assert should_fire_alert(r) is False

    def test_grade_a_fires_for_b_min(self):
        r = ConfluenceResult(composite_score=80.0, grade="A", vetoed=False)
        assert should_fire_alert(r, min_grade="B") is True

    def test_grade_c_does_not_fire_for_b_min(self):
        r = ConfluenceResult(composite_score=42.0, grade="C", vetoed=False)
        assert should_fire_alert(r, min_grade="B") is False

    def test_grade_f_never_fires(self):
        r = ConfluenceResult(composite_score=20.0, grade="F", vetoed=False)
        assert should_fire_alert(r, min_grade="C") is False


# ═══════════════════════════════════════════════════════════════════════
#  Institutional Alert Tests
# ═══════════════════════════════════════════════════════════════════════
from tradingbot.analysis.institutional_alert import (
    estimate_float_data,
    compute_exit_levels,
    InstitutionalContext,
    _float_cache,
)


class TestEstimateFloatData:
    def setup_method(self):
        """Clear the float cache before each test."""
        _float_cache.clear()

    def test_market_cap_based(self):
        result = estimate_float_data("TEST", market_cap=1_000_000_000, current_price=50.0)
        assert result["float_shares_m"] > 0
        assert result["data_source"] in ("estimated", "fmp")

    def test_volume_based_fallback(self):
        result = estimate_float_data("TEST2", avg_volume=500_000, current_price=50.0)
        assert result["float_shares_m"] > 0

    def test_no_data_returns_zero(self):
        result = estimate_float_data("TEST3")
        assert result["float_shares_m"] == 0.0

    def test_cache_returns_same_result(self):
        """Second call should return cached result without API hit."""
        r1 = estimate_float_data("CACHE1", market_cap=500_000_000, current_price=25.0)
        r2 = estimate_float_data("CACHE1", market_cap=500_000_000, current_price=25.0)
        assert r1 == r2
        # Verify it's actually in the cache
        assert "CACHE1" in _float_cache

    def test_fmp_called_when_key_set(self, monkeypatch):
        """When FMP_API_KEY is set and API returns data, use it."""
        monkeypatch.setenv("FMP_API_KEY", "test_key_123")

        fake_profile = [{
            "floatShares": 25_000_000,
            "sharesOutstanding": 40_000_000,
            "mktCap": 1_000_000_000,
        }]

        class FakeResp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return fake_profile

        import tradingbot.analysis.institutional_alert as ia_mod
        monkeypatch.setattr("requests.get", lambda *a, **kw: FakeResp())
        # Need to import requests at module level for the mock to work
        import requests
        monkeypatch.setattr(requests, "get", lambda *a, **kw: FakeResp())

        _float_cache.clear()
        result = estimate_float_data("FMPTEST", current_price=25.0)
        assert result["data_source"] == "fmp"
        assert result["float_shares_m"] == 25.0  # 25M

    def test_fmp_failure_falls_back(self, monkeypatch):
        """When FMP API fails, should fall back to estimate."""
        monkeypatch.setenv("FMP_API_KEY", "test_key_123")

        import requests
        monkeypatch.setattr(requests, "get", lambda *a, **kw: (_ for _ in ()).throw(ConnectionError("test")))

        _float_cache.clear()
        result = estimate_float_data("FAILTEST", market_cap=1_000_000_000, current_price=50.0)
        assert result["data_source"] == "estimated"
        assert result["float_shares_m"] > 0


class TestComputeExitLevels:
    def test_basic_exit_levels(self):
        bars = _make_bars(count=20, base_price=100.0)
        levels = compute_exit_levels(
            bars_data=bars, current_price=110.0, atr=3.0,
            key_support=105.0, key_resistance=115.0,
        )
        assert isinstance(levels, dict)
        assert "atr_stop" in levels
        assert "structure_stop" in levels
        assert "tp1_conservative" in levels
        assert "tp2_aggressive" in levels
        assert levels["atr_stop"] < 110.0  # stop should be below entry

    def test_empty_bars(self):
        levels = compute_exit_levels(
            bars_data=[], current_price=110.0, atr=3.0,
            key_support=105.0, key_resistance=115.0,
        )
        assert isinstance(levels, dict)
        # Should still produce ATR-based stops even with no bars
        assert levels["atr_stop"] < 110.0


class TestInstitutionalContext:
    def test_default_values(self):
        ctx = InstitutionalContext()
        assert ctx.confluence_grade == "C"
        assert ctx.warnings == []
        assert ctx.risk_reward_ratio == 0.0
