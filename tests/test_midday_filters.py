"""Tests for midday-specific filters added in Apr 15 2026.

Two filters tested:
1. Midday high-risk block — high risk_level trades blocked at midday
2. Midday ATR exhaustion — MOVE EXHAUSTED flagged trades blocked at midday
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tradingbot.app.session_runner import SessionRunner
from tradingbot.models import SymbolSnapshot, TradeCard, RiskState
from tradingbot.ranking.ranker import RankedCandidate


def _make_snapshot(**overrides) -> SymbolSnapshot:
    """Create a SymbolSnapshot with sensible defaults that pass all filters."""
    defaults = dict(
        symbol="TEST",
        price=50.0,
        gap_pct=3.0,
        premarket_volume=200_000,
        dollar_volume=10_000_000,
        spread_pct=0.3,
        relative_volume=2.0,
        catalyst_score=70.0,      # above midday floor of 58
        ema9=49.5,
        ema20=49.0,
        vwap=49.2,
        recent_volume=150_000,
        avg_volume_20=80_000,
        pullback_low=49.0,
        reclaim_level=50.0,
        pullback_high=51.0,
        key_support=49.5,
        key_resistance=52.0,
        atr=1.0,
        intraday_change_pct=1.0,
        open_price=48.5,
        patterns=["above_vwap"],
        raw_bars=[],
        tech_indicators={"vwap": 49.2, "rsi": 55.0, "macd_hist": 0.1},
    )
    defaults.update(overrides)
    return SymbolSnapshot(**defaults)


def _make_runner() -> SessionRunner:
    """Create a SessionRunner in mock-data mode."""
    return SessionRunner(Path.cwd(), use_real_data=False)


@dataclass
class _FakeConfluenceResult:
    composite_score: float = 70.0
    grade: str = "B"
    factors: list = field(default_factory=list)
    vetoed: bool = False
    veto_reason: str = ""
    summary: str = ""
    false_positive_flags: list[str] = field(default_factory=list)


@dataclass
class _FakeVolumeProfile:
    classification: str = "accumulation"


@dataclass
class _FakeInstitutionalContext:
    pass


def _setup_patches(runner, can_long=True):
    """Apply common patches so _build_cards proceeds without real data."""
    # Market health green
    runner._market_health = MagicMock(
        regime="green",
        size_multiplier=1.0,
        stop_buffer_multiplier=1.0,
        spy_change_pct=0.5,
        qqq_change_pct=0.6,
        reason="",
    )
    runner._market_condition = None
    runner._tuning_overrides = {}
    runner._alerts_sent_count = 0


# ── Helpers to run _build_cards with full patching ───────────────────

def _run_build_cards(
    runner,
    snapshot,
    score=70.0,
    session_tag="midday",
    false_positive_flags=None,
    confluence_grade="B",
    confluence_score=70.0,
):
    """Run _build_cards with a single candidate, mocking external deps."""
    _setup_patches(runner)

    ranked = [RankedCandidate(snapshot=snapshot, score=score)]
    dropped: list[tuple[str, str]] = []

    fake_confluence = _FakeConfluenceResult(
        composite_score=confluence_score,
        grade=confluence_grade,
        false_positive_flags=false_positive_flags or [],
    )

    with patch("tradingbot.app.session_runner.get_today_alerted_symbols", return_value={}), \
         patch("tradingbot.app.session_runner.has_valid_setup", return_value=True), \
         patch("tradingbot.app.session_runner.evaluate_confluence", return_value=fake_confluence), \
         patch("tradingbot.app.session_runner.classify_volume_profile", return_value=_FakeVolumeProfile()), \
         patch("tradingbot.app.session_runner.build_institutional_context", return_value=_FakeInstitutionalContext()), \
         patch("tradingbot.app.session_runner.generate_chart", return_value=None), \
         patch("tradingbot.app.session_runner.save_alert"):

        runner.notifier = MagicMock()
        runner.notifier.send_institutional_alert = MagicMock(return_value=True)

        cards = runner._build_cards(
            ranked=ranked,
            session_tag=session_tag,
            volume_spike=1.5,
            dropped=dropped,
        )
    return cards, dropped


# ═════════════════════════════════════════════════════════════════════
# 1. Midday high-risk block
# ═════════════════════════════════════════════════════════════════════

class TestMiddayHighRiskBlock:
    """High-risk trades should be blocked at midday but pass at morning/close."""

    def test_high_risk_blocked_at_midday(self):
        runner = _make_runner()
        # Create a high-risk snapshot: cheap, volatile, wide spread
        snap = _make_snapshot(
            symbol="LWLG",
            price=12.0,
            spread_pct=1.6,
            atr=0.8,
            dollar_volume=800_000,
            key_support=11.70,
            key_resistance=12.60,
            vwap=11.95,
            ema9=12.05,
            ema20=11.90,
        )
        cards, dropped = _run_build_cards(runner, snap, session_tag="midday")
        assert len(cards) == 0, f"High-risk trade should be blocked at midday, got {[c.symbol for c in cards]}"
        drop_reasons = [r for _, r in dropped]
        assert any("midday_high_risk" in r for r in drop_reasons), f"Expected midday_high_risk drop, got {drop_reasons}"

    def test_high_risk_passes_at_morning(self):
        runner = _make_runner()
        snap = _make_snapshot(
            symbol="LWLG",
            price=12.0,
            spread_pct=1.6,
            atr=0.8,
            dollar_volume=800_000,
            key_support=11.70,
            key_resistance=12.60,
        )
        cards, dropped = _run_build_cards(runner, snap, session_tag="morning")
        drop_reasons = [r for _, r in dropped]
        assert not any("midday_high_risk" in r for r in drop_reasons), \
            f"High-risk should NOT be blocked at morning, drops: {drop_reasons}"

    def test_high_risk_passes_at_close(self):
        runner = _make_runner()
        snap = _make_snapshot(
            symbol="LWLG",
            price=12.0,
            spread_pct=1.6,
            atr=0.8,
            dollar_volume=800_000,
            key_support=11.70,
            key_resistance=12.60,
        )
        cards, dropped = _run_build_cards(runner, snap, session_tag="close")
        drop_reasons = [r for _, r in dropped]
        assert not any("midday_high_risk" in r for r in drop_reasons), \
            f"High-risk should NOT be blocked at close, drops: {drop_reasons}"

    def test_low_risk_passes_at_midday(self):
        runner = _make_runner()
        # Quality stock: higher price, tight spread, low volatility
        snap = _make_snapshot(
            symbol="INTC",
            price=63.0,
            spread_pct=0.1,
            atr=1.5,
            dollar_volume=50_000_000,
            key_support=62.5,
            key_resistance=65.0,
        )
        cards, dropped = _run_build_cards(runner, snap, session_tag="midday")
        drop_reasons = [r for _, r in dropped]
        assert not any("midday_high_risk" in r for r in drop_reasons), \
            f"Low-risk should pass at midday, drops: {drop_reasons}"


# ═════════════════════════════════════════════════════════════════════
# 2. Midday ATR exhaustion hard block
# ═════════════════════════════════════════════════════════════════════

class TestMiddayATRExhaustion:
    """ATR-exhausted trades should be hard-blocked at midday."""

    def test_atr_exhausted_blocked_at_midday(self):
        runner = _make_runner()
        snap = _make_snapshot(symbol="SNAP", price=6.0, key_support=5.85, key_resistance=6.35,
                              vwap=5.95, ema9=6.02, ema20=5.98)
        cards, dropped = _run_build_cards(
            runner, snap,
            session_tag="midday",
            false_positive_flags=[
                "MOVE EXHAUSTED: ATR 178% consumed (0.78 of 0.44) — move exhausted | Spread eats 200% of remaining range — poor reward",
                "RSI OVERBOUGHT (84): Pullback likely before continuation",
            ],
        )
        assert len(cards) == 0, "ATR-exhausted trade should be blocked at midday"
        drop_reasons = [r for _, r in dropped]
        assert any("midday_atr_exhausted" in r for r in drop_reasons), f"Expected midday_atr_exhausted, got {drop_reasons}"

    def test_atr_exhausted_passes_at_morning(self):
        runner = _make_runner()
        snap = _make_snapshot(symbol="SNAP", price=6.0, key_support=5.85, key_resistance=6.35)
        cards, dropped = _run_build_cards(
            runner, snap,
            session_tag="morning",
            false_positive_flags=[
                "MOVE EXHAUSTED: ATR 178% consumed (0.78 of 0.44) — move exhausted",
            ],
        )
        drop_reasons = [r for _, r in dropped]
        assert not any("midday_atr_exhausted" in r for r in drop_reasons), \
            f"ATR exhaustion should NOT hard-block at morning, drops: {drop_reasons}"

    def test_no_exhaustion_passes_at_midday(self):
        runner = _make_runner()
        snap = _make_snapshot(symbol="INTC", price=63.0, key_support=62.5, key_resistance=65.0)
        cards, dropped = _run_build_cards(
            runner, snap,
            session_tag="midday",
            false_positive_flags=[
                "RSI OVERBOUGHT (71): Pullback likely before continuation",
            ],
        )
        drop_reasons = [r for _, r in dropped]
        assert not any("midday_atr_exhausted" in r for r in drop_reasons), \
            f"Non-exhausted trade should pass at midday, drops: {drop_reasons}"

    def test_atr_exhausted_passes_at_close(self):
        runner = _make_runner()
        snap = _make_snapshot(symbol="SNAP", price=6.0, key_support=5.85, key_resistance=6.35)
        cards, dropped = _run_build_cards(
            runner, snap,
            session_tag="close",
            false_positive_flags=[
                "MOVE EXHAUSTED: ATR 90% consumed (0.40 of 0.44) — move exhausted",
            ],
        )
        drop_reasons = [r for _, r in dropped]
        assert not any("midday_atr_exhausted" in r for r in drop_reasons), \
            f"ATR exhaustion should NOT hard-block at close, drops: {drop_reasons}"
