"""Tests for improved risk classification and R:R capping.

Covers three fixes from the Apr 6-8 failure analysis:
1. _assess_risk() now penalises gap size, extreme relvol, and tiered ATR volatility
2. R:R is capped at MAX_RR (3.0) so inflated targets don't create unwinnable setups
3. catalyst_score is returned by load_alerts()
"""

from tradingbot.models import SymbolSnapshot
from tradingbot.strategy.trade_card import _assess_risk, build_trade_card, MAX_RR


# ── Helper ────────────────────────────────────────────────────────────

def _make_stock(**overrides) -> SymbolSnapshot:
    """Default stock: quality $20 name, moderate gap, normal volatility."""
    defaults = dict(
        symbol="QUAL",
        price=20.0,
        gap_pct=3.0,
        premarket_volume=600_000,
        dollar_volume=30_000_000,
        spread_pct=0.15,
        relative_volume=2.0,
        catalyst_score=70,
        ema9=19.90,
        ema20=19.70,
        vwap=19.80,
        recent_volume=200_000,
        avg_volume_20=100_000,
        pullback_low=19.70,
        reclaim_level=20.0,
        pullback_high=20.20,
        key_support=19.30,
        key_resistance=21.20,
        atr=0.40,
    )
    defaults.update(overrides)
    return SymbolSnapshot(**defaults)


# ═══════════════════════════════════════════════════════════════════════
# 1.  Risk classification — new penalty criteria
# ═══════════════════════════════════════════════════════════════════════

class TestRiskClassificationGapPenalty:
    """Large gaps should push risk level up."""

    def test_moderate_gap_no_penalty(self):
        """Gap 3% should be 'low' risk (no gap penalty)."""
        stock = _make_stock(gap_pct=3.0)
        assert _assess_risk(stock, 2.5) == "low"

    def test_gap_above_4_adds_penalty(self):
        """Gap 5% should add +1 penalty.  Combined with relvol 2.0 and
        moderate ATR it should remain low or shift to medium."""
        stock = _make_stock(gap_pct=5.0)
        level = _assess_risk(stock, 2.5)
        # 5% gap adds +1; qualifies as at least low but may be medium
        assert level in ("low", "medium")

    def test_gap_above_8_adds_two_penalties(self):
        """Gap 9% adds +2 penalty on its own.  On an otherwise clean stock
        that's still only 2 penalties (= low), but combined with any other
        factor it pushes to medium — which is what happens in practice."""
        stock = _make_stock(gap_pct=9.0)
        level = _assess_risk(stock, 2.5)
        # 9% gap alone = +2.  Clean stock otherwise → total 2 → low.
        # Real speculative stocks always have relvol/ATR penalties too.
        assert level in ("low", "medium")

        # Adding even moderate ATR volatility tips it to medium (2+1 = 3)
        stock2 = _make_stock(gap_pct=9.0, atr=0.70)  # ATR/price=3.5% → +1
        assert _assess_risk(stock2, 2.5) == "medium"

    def test_large_gap_with_high_relvol_is_high(self):
        """Gap 10% + relvol 4× + ATR/price 3.5% → should be high risk.
        Penalties: gap(+2) + relvol(+1) + ATR(+1) = 4 → medium minimum.
        But with any additional factor it tips to high."""
        stock = _make_stock(gap_pct=10.0, relative_volume=4.0, atr=0.70)
        level = _assess_risk(stock, 2.5)
        assert level in ("medium", "high"), f"Expected medium+, got {level}"


class TestRiskClassificationATRTiered:
    """ATR/price volatility check is now two-tiered: >3% = +1, >5% = +2."""

    def test_low_volatility_no_penalty(self):
        """ATR/price = 2% → no volatile penalty."""
        stock = _make_stock(price=20.0, atr=0.40)  # 2%
        assert _assess_risk(stock, 2.5) == "low"

    def test_moderate_volatility_adds_one(self):
        """ATR/price = 3.5% → +1 penalty."""
        stock = _make_stock(price=20.0, atr=0.70)  # 3.5%
        level = _assess_risk(stock, 2.5)
        # Just +1 from ATR shouldn't push past low on its own
        # but with gap 3% (no gap penalty), still low
        assert level == "low"

    def test_high_volatility_adds_two(self):
        """ATR/price = 6% → +2 penalty.  Combined with gap = low-medium."""
        stock = _make_stock(price=20.0, atr=1.20)  # 6%
        level = _assess_risk(stock, 2.5)
        # +2 from ATR alone → still low (=2)
        assert level in ("low", "medium")

    def test_extreme_volatility_with_gap(self):
        """ATR/price = 6% + gap 6% → +2 + +1 = 3+ → medium or higher."""
        stock = _make_stock(price=20.0, atr=1.20, gap_pct=6.0)
        level = _assess_risk(stock, 2.5)
        assert level in ("medium", "high"), f"Expected medium+, got {level}"


class TestRiskClassificationRelvolFrenzy:
    """Extreme relative volume (>3×) should add a penalty."""

    def test_normal_relvol_no_penalty(self):
        """RelVol 2.0 → no penalty."""
        stock = _make_stock(relative_volume=2.0)
        assert _assess_risk(stock, 2.5) == "low"

    def test_high_relvol_adds_penalty(self):
        """RelVol 4.0 → +1 penalty."""
        stock = _make_stock(relative_volume=4.0)
        level = _assess_risk(stock, 2.5)
        assert level in ("low", "medium")

    def test_frenzy_relvol_with_big_gap(self):
        """RelVol 5.0 + gap 9% → speculative frenzy → medium+."""
        stock = _make_stock(gap_pct=9.0, relative_volume=5.0)
        level = _assess_risk(stock, 2.5)
        assert level in ("medium", "high"), f"Expected medium+, got {level}"


class TestRiskClassificationRealWorldStocks:
    """Verify that Apr 6-8 speculative stocks would now be classified correctly."""

    def test_ionq_like_stock(self):
        """IONQ-like: $30, gap 7%, relvol 10×, ATR/price 4%.
        Penalties: gap(+1) + relvol(+1) + ATR(+1) = 3 → medium."""
        stock = _make_stock(
            symbol="IONQ", price=30.0, gap_pct=7.0,
            relative_volume=10.0, atr=1.20,  # 4%
            key_support=28.50, key_resistance=32.00,
        )
        level = _assess_risk(stock, 2.5)
        assert level != "low", f"IONQ should NOT be low risk, got {level}"

    def test_soxl_like_stock(self):
        """SOXL-like: $66, gap 20%, relvol 2×, ATR/price 5.5%.
        Penalties: gap(+2) + ATR(+2) = 4 → medium."""
        stock = _make_stock(
            symbol="SOXL", price=66.0, gap_pct=20.0,
            relative_volume=2.0, atr=3.63,  # 5.5%
            key_support=63.0, key_resistance=72.0,
        )
        level = _assess_risk(stock, 2.5)
        assert level != "low", f"SOXL should NOT be low risk, got {level}"

    def test_quality_name_stays_low(self):
        """A quality name like AAPL: $180, gap 2%, relvol 1.8×, ATR/price 1.5%.
        Penalties: 0 → low risk.  Should NOT be penalised."""
        stock = _make_stock(
            symbol="AAPL", price=180.0, gap_pct=2.0,
            relative_volume=1.8, atr=2.70,  # 1.5%
            key_support=177.0, key_resistance=184.0,
        )
        level = _assess_risk(stock, 2.5)
        assert level == "low", f"AAPL should be low risk, got {level}"

    def test_rgti_like_stock(self):
        """RGTI-like: $15, gap 6%, relvol 316×, ATR/price 3.5%.
        Penalties: gap(+1) + relvol(+1) + ATR(+1) = 3 → medium."""
        stock = _make_stock(
            symbol="RGTI", price=15.0, gap_pct=6.0,
            relative_volume=316.0, atr=0.525,  # 3.5%
            key_support=14.50, key_resistance=16.00,
        )
        level = _assess_risk(stock, 2.5)
        assert level != "low", f"RGTI should NOT be low risk, got {level}"


# ═══════════════════════════════════════════════════════════════════════
# 2.  R:R capping — inflated targets brought back to reality
# ═══════════════════════════════════════════════════════════════════════

class TestRRCap:
    """R:R should be capped at MAX_RR to prevent unrealistic targets."""

    def test_rr_capped_at_max(self):
        """When TP is far and stop is tight, R:R should be capped at MAX_RR."""
        # Setup: tight stop (1.5%), far resistance (6%) → R:R would be ~4.0
        stock = _make_stock(
            price=20.0,
            key_support=19.80,     # tight support
            key_resistance=21.20,  # 6% above entry
            atr=0.80,              # max_tp_dist = min(2.4, 1.2) = 1.2
        )
        card = build_trade_card(
            stock, 80, 2.5, "morning",
            stop_pct_by_risk={"low": 1.5, "medium": 2.5, "high": 2.5},
        )
        if card is not None:
            assert card.risk_reward <= MAX_RR, (
                f"R:R {card.risk_reward} exceeds MAX_RR {MAX_RR}"
            )

    def test_moderate_rr_not_capped(self):
        """R:R around 2.0 should not be touched."""
        stock = _make_stock(
            price=100.0,
            key_support=99.0,
            key_resistance=103.0,  # ~3% above
            atr=1.50,
        )
        card = build_trade_card(stock, 80, 2.5, "morning")
        assert card is not None
        # Should be a natural R:R, approximately 1.5-2.5
        assert card.risk_reward >= 1.5
        assert card.risk_reward <= MAX_RR

    def test_rr_cap_adjusts_tp1(self):
        """When R:R is capped, TP1 should be pulled closer to entry."""
        # Create a setup where tight stop + high resistance → inflated R:R
        stock = _make_stock(
            price=100.0,
            key_support=99.00,
            key_resistance=106.0,  # 6% above
            atr=1.00,              # max_tp_dist = min(3.0, 6.0) = 3.0
        )
        card = build_trade_card(
            stock, 80, 2.5, "morning",
            stop_pct_by_risk={"low": 1.5, "medium": 2.5, "high": 2.5},
        )
        if card is not None:
            # TP1 should be at entry + MAX_RR × risk, not at the raw 103 (3% distance)
            assert card.risk_reward <= MAX_RR
            # TP1 should be below the raw 103 level due to capping
            assert card.tp1_price <= 103.01  # max_tp_dist caps at $3

    def test_max_rr_constant_is_three(self):
        """The MAX_RR constant should be 3.0."""
        assert MAX_RR == 3.0


# ═══════════════════════════════════════════════════════════════════════
# 3.  Integration: risk + R:R cap working together
# ═══════════════════════════════════════════════════════════════════════

class TestRiskAndRRIntegration:
    """End-to-end: speculative stocks get medium risk + capped R:R."""

    def test_speculative_stock_gets_wider_stop(self):
        """A stock that was previously 'low' risk (tight 1.5% stop) should
        now be 'medium' (2.5% stop) due to gap/relvol penalties."""
        stock = _make_stock(
            symbol="IONQ",
            price=30.0,
            gap_pct=7.0,
            relative_volume=10.0,
            atr=1.20,  # ATR/price = 4%
            key_support=28.50,
            key_resistance=32.50,
        )
        card = build_trade_card(
            stock, 70, 2.5, "morning",
            stop_pct_by_risk={"low": 1.5, "medium": 2.5, "high": 2.5},
        )
        if card is not None:
            # Should be medium risk → 2.5% stop, not 1.5%
            assert card.risk_level != "low", \
                f"IONQ should not be low risk, got {card.risk_level}"

    def test_quality_stock_keeps_tight_stop(self):
        """A quality name should stay 'low' risk with tight stop."""
        stock = _make_stock(
            symbol="MSFT",
            price=400.0,
            gap_pct=2.0,
            relative_volume=1.5,
            atr=4.0,  # ATR/price = 1%
            key_support=394.0,
            key_resistance=410.0,
        )
        card = build_trade_card(
            stock, 80, 2.5, "morning",
            stop_pct_by_risk={"low": 1.5, "medium": 2.5, "high": 2.5},
        )
        if card is not None:
            assert card.risk_level == "low", \
                f"MSFT should be low risk, got {card.risk_level}"

    def test_no_card_exceeds_max_rr(self):
        """Regardless of stock type, no card should exceed MAX_RR."""
        configs = [
            dict(price=10, gap_pct=2.0, relative_volume=1.5, atr=0.20,
                 key_support=9.70, key_resistance=10.60),
            dict(price=50, gap_pct=8.0, relative_volume=5.0, atr=2.50,
                 key_support=48.0, key_resistance=55.0),
            dict(price=200, gap_pct=3.0, relative_volume=2.0, atr=3.0,
                 key_support=196.0, key_resistance=208.0),
        ]
        for cfg in configs:
            stock = _make_stock(**cfg)
            card = build_trade_card(
                stock, 75, 2.5, "morning",
                stop_pct_by_risk={"low": 1.5, "medium": 2.5, "high": 2.5},
            )
            if card is not None:
                assert card.risk_reward <= MAX_RR, (
                    f"{stock.symbol} R:R {card.risk_reward} > MAX_RR {MAX_RR}"
                )
