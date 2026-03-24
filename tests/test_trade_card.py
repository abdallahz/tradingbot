from tradingbot.models import SymbolSnapshot
from tradingbot.strategy.trade_card import build_trade_card


def test_trade_card_long_prices():
    stock = SymbolSnapshot(
        symbol="TEST",
        price=10,
        gap_pct=5,
        premarket_volume=600000,
        dollar_volume=25000000,
        spread_pct=0.2,
        relative_volume=2.0,
        catalyst_score=80,
        ema9=9.9,
        ema20=9.7,
        vwap=9.8,
        recent_volume=200000,
        avg_volume_20=80000,
        pullback_low=9.7,
        reclaim_level=10.0,
        pullback_high=10.2,
        key_support=9.5,
        key_resistance=10.5,
    )
    card = build_trade_card(stock, "long", 80, 2.5, "morning")
    assert card.entry_price > card.stop_price
    assert card.tp2_price > card.tp1_price > card.entry_price


def test_trade_card_rejects_missing_levels():
    """Card must be None when key_support/key_resistance are unset (0.0)."""
    stock = SymbolSnapshot(
        symbol="NOLVL",
        price=10,
        gap_pct=5,
        premarket_volume=600000,
        dollar_volume=25000000,
        spread_pct=0.2,
        relative_volume=2.0,
        catalyst_score=80,
        ema9=9.9,
        ema20=9.7,
        vwap=9.8,
        recent_volume=200000,
        avg_volume_20=80000,
        pullback_low=9.7,
        reclaim_level=10.0,
        pullback_high=10.2,
        key_support=0.0,
        key_resistance=0.0,
    )
    assert build_trade_card(stock, "long", 80, 2.5, "morning") is None
    assert build_trade_card(stock, "short", 80, 2.5, "morning") is None


def test_trade_card_rejects_wrong_side_levels():
    """Card must be None when resistance is below entry (long)."""
    stock = SymbolSnapshot(
        symbol="WRONG",
        price=10,
        gap_pct=5,
        premarket_volume=600000,
        dollar_volume=25000000,
        spread_pct=0.2,
        relative_volume=2.0,
        catalyst_score=80,
        ema9=9.9,
        ema20=9.7,
        vwap=9.8,
        recent_volume=200000,
        avg_volume_20=80000,
        pullback_low=9.7,
        reclaim_level=10.0,
        pullback_high=10.2,
        key_support=9.5,
        key_resistance=9.8,   # below entry — should reject
    )
    assert build_trade_card(stock, "long", 80, 2.5, "morning") is None
