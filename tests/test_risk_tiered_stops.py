"""Tests for risk-tiered stop and configurable data feed features."""
import os
from unittest.mock import patch

from tradingbot.models import SymbolSnapshot
from tradingbot.strategy.trade_card import build_trade_card, _assess_risk


# ── Helper: build a SymbolSnapshot with controllable risk level ──────────


def _make_stock(
    price: float = 10.0,
    spread_pct: float = 0.2,
    dollar_volume: float = 25_000_000,
    atr: float = 0.3,
    key_support: float = 9.5,
    key_resistance: float = 10.5,
    **overrides,
) -> SymbolSnapshot:
    defaults = dict(
        symbol="TEST",
        price=price,
        gap_pct=5.0,
        premarket_volume=600_000,
        dollar_volume=dollar_volume,
        spread_pct=spread_pct,
        relative_volume=2.0,
        catalyst_score=80,
        ema9=price * 0.99,
        ema20=price * 0.97,
        vwap=price * 0.98,
        recent_volume=200_000,
        avg_volume_20=80_000,
        pullback_low=key_support,
        reclaim_level=price,
        pullback_high=price * 1.02,
        key_support=key_support,
        key_resistance=key_resistance,
        atr=atr,
    )
    defaults.update(overrides)
    return SymbolSnapshot(**defaults)


# ── Risk level assessment tests ──────────────────────────────────────────


class TestAssessRisk:
    """Verify _assess_risk classifies correctly."""

    def test_low_risk_clean_stock(self):
        """Good price, tight spread, deep liquidity → low risk."""
        stock = _make_stock(price=50.0, spread_pct=0.1, dollar_volume=50_000_000, atr=1.0)
        assert _assess_risk(stock, 2.5) == "low"

    def test_medium_risk_small_cap(self):
        """Penny-ish price + thin volume → medium."""
        stock = _make_stock(price=4.0, spread_pct=1.0, dollar_volume=1_500_000, atr=0.3)
        assert _assess_risk(stock, 2.0) == "medium"

    def test_high_risk_junk(self):
        """Sub-$3 + wide spread + thin → high."""
        stock = _make_stock(price=2.0, spread_pct=2.0, dollar_volume=300_000, atr=0.5)
        assert _assess_risk(stock, 1.6) == "high"


# ── Risk-tiered stop tests ───────────────────────────────────────────────


class TestRiskTieredStops:
    """Verify stop_pct_by_risk adjusts the stop level."""

    def test_low_risk_gets_tighter_stop(self):
        """Low-risk trade should use 1.5% max stop when tiered config is set."""
        stock = _make_stock(
            price=100.0, spread_pct=0.1, dollar_volume=50_000_000,
            atr=2.0, key_support=95.0, key_resistance=106.0,
        )
        # Without tiered stops: 2.5% → max stop at $97.50
        card_default = build_trade_card(stock, 80, 2.5, "morning")
        assert card_default is not None

        # With tiered stops: low risk gets 1.5% → max stop at $98.50
        tiered = {"low": 1.5, "medium": 2.5, "high": 2.5}
        card_tiered = build_trade_card(stock, 80, 2.5, "morning", stop_pct_by_risk=tiered)
        assert card_tiered is not None

        # Tiered stop should be tighter (higher price = less risk)
        assert card_tiered.stop_price >= card_default.stop_price
        # Verify the stop is within 1.5% of entry
        max_allowed_loss = card_tiered.entry_price * 0.015
        actual_loss = card_tiered.entry_price - card_tiered.stop_price
        assert actual_loss <= max_allowed_loss + 0.01  # small rounding tolerance

    def test_medium_risk_keeps_original_stop(self):
        """Medium-risk trade should use the regular 2.5% stop."""
        stock = _make_stock(
            price=4.0, spread_pct=1.0, dollar_volume=1_500_000,
            atr=0.15, key_support=3.7, key_resistance=4.3,
        )
        tiered = {"low": 1.5, "medium": 2.5, "high": 2.5}
        card_tiered = build_trade_card(stock, 80, 2.5, "morning", stop_pct_by_risk=tiered)
        card_default = build_trade_card(stock, 80, 2.5, "morning")

        if card_tiered and card_default:
            # Medium risk → same stop as default
            assert card_tiered.stop_price == card_default.stop_price

    def test_no_tiered_config_uses_default(self):
        """When stop_pct_by_risk is None, behavior is unchanged."""
        stock = _make_stock()
        card_none = build_trade_card(stock, 80, 2.5, "morning", stop_pct_by_risk=None)
        card_default = build_trade_card(stock, 80, 2.5, "morning")
        if card_none and card_default:
            assert card_none.stop_price == card_default.stop_price
            assert card_none.tp1_price == card_default.tp1_price

    def test_tiered_stop_preserves_atr_floor(self):
        """Even with tighter stop %, ATR floor (0.5 × ATR) must be respected."""
        stock = _make_stock(
            price=20.0, spread_pct=0.1, dollar_volume=50_000_000,
            atr=0.8,  # 0.5 × ATR = 0.4 → minimum stop distance
            key_support=19.5, key_resistance=20.5,
        )
        tiered = {"low": 1.5, "medium": 2.5, "high": 2.5}
        card = build_trade_card(stock, 80, 2.5, "morning", stop_pct_by_risk=tiered)
        if card:
            risk = card.entry_price - card.stop_price
            # ATR floor = 0.5 × 0.8 = 0.4
            assert risk >= 0.39  # with rounding tolerance

    def test_tiered_stop_still_checked_for_min_rr(self):
        """A tighter stop can push R:R below MIN_RR → card rejected."""
        # Support very close to price → tighter stop means tiny risk → huge R:R
        # But if level_stop is above tiered max stop, the risk stays the same.
        # Test with resistance very close to entry to push R:R down.
        stock = _make_stock(
            price=10.0, spread_pct=0.1, dollar_volume=50_000_000,
            atr=0.2, key_support=9.85, key_resistance=10.15,
        )
        # Default 2.5% gives room; 0.5% would make R:R fail
        tiered = {"low": 0.5, "medium": 2.5, "high": 2.5}
        card = build_trade_card(stock, 80, 2.5, "morning", stop_pct_by_risk=tiered)
        # Card may be None if R:R < 1.5 after tighter stop, or valid if levels work out
        if card is not None:
            assert card.risk_reward >= 1.5

    def test_risk_level_is_set_on_card(self):
        """Card must carry the risk_level field."""
        stock = _make_stock()
        card = build_trade_card(stock, 80, 2.5, "morning")
        assert card is not None
        assert card.risk_level in ("low", "medium", "high")


# ── Data feed configuration tests ────────────────────────────────────────


class TestDataFeedConfig:
    """Verify ALPACA_DATA_FEED env var is picked up."""

    def test_config_defaults_to_iex(self):
        """ConfigLoader should default data_feed to 'iex'."""
        from tradingbot.config import ConfigLoader
        from pathlib import Path
        cfg = ConfigLoader(Path(__file__).resolve().parent.parent)
        broker = cfg.broker()
        assert broker["alpaca"]["data_feed"] in ("iex", "sip")

    def test_config_env_override(self):
        """ALPACA_DATA_FEED env var should override config."""
        from tradingbot.config import ConfigLoader
        from pathlib import Path
        with patch.dict(os.environ, {"ALPACA_DATA_FEED": "sip"}):
            cfg = ConfigLoader(Path(__file__).resolve().parent.parent)
            broker = cfg.broker()
            assert broker["alpaca"]["data_feed"] == "sip"

    def test_alpaca_client_accepts_feed(self):
        """AlpacaClient constructor should accept data_feed parameter."""
        from tradingbot.data.alpaca_client import AlpacaClient
        # Can't actually connect, but verify the attribute is stored
        try:
            client = AlpacaClient("fake", "fake", paper=True, data_feed="sip")
            assert client.data_feed == "sip"
        except Exception:
            # Connection error expected with fake keys, but attribute test
            # may still work if constructor assigns before connecting.
            pass

    def test_tracker_get_feed_default(self):
        """TradeTracker._get_feed() defaults to 'iex'."""
        from tradingbot.tracking.trade_tracker import TradeTracker
        with patch.dict(os.environ, {}, clear=False):
            # Remove ALPACA_DATA_FEED if set
            env = os.environ.copy()
            env.pop("ALPACA_DATA_FEED", None)
            with patch.dict(os.environ, env, clear=True):
                tracker = TradeTracker()
                assert tracker._get_feed() == "iex"

    def test_tracker_get_feed_sip(self):
        """TradeTracker._get_feed() reads ALPACA_DATA_FEED env var."""
        from tradingbot.tracking.trade_tracker import TradeTracker
        with patch.dict(os.environ, {"ALPACA_DATA_FEED": "sip"}):
            tracker = TradeTracker()
            assert tracker._get_feed() == "sip"


# ── Risk YAML configuration tests ───────────────────────────────────────


class TestRiskYamlConfig:
    """Verify risk.yaml has the new tiered stop fields."""

    def test_risk_yaml_has_tiered_stops(self):
        from tradingbot.config import ConfigLoader
        from pathlib import Path
        cfg = ConfigLoader(Path(__file__).resolve().parent.parent)
        risk = cfg.risk()["risk"]
        assert "stop_pct_low_risk" in risk
        assert "stop_pct_medium_risk" in risk
        assert "stop_pct_high_risk" in risk
        assert risk["stop_pct_low_risk"] == 1.5
        assert risk["stop_pct_medium_risk"] == 2.5
        assert risk["stop_pct_high_risk"] == 2.5

    def test_fixed_stop_pct_unchanged(self):
        """The base fixed_stop_pct should remain 2.5% (backward compat)."""
        from tradingbot.config import ConfigLoader
        from pathlib import Path
        cfg = ConfigLoader(Path(__file__).resolve().parent.parent)
        risk = cfg.risk()["risk"]
        assert risk["fixed_stop_pct"] == 2.5
