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


def test_trade_card_breakout_long():
    """Breakout scenario: price at PM high, resistance = extension target.

    Simulates RGTI-like setup: $15 stock at PM high, ATR ~ $0.30.
    Breakout mode in alpaca_client would set:
      key_support  = PM_high - 0.25*ATR ≈ $14.93
      key_resistance = price + 2*ATR     ≈ $15.60
    R:R should be well above MIN_RR (1.5).
    """
    stock = SymbolSnapshot(
        symbol="RGTI",
        price=15.0,
        gap_pct=6.0,
        premarket_volume=885000,
        dollar_volume=50000000,
        spread_pct=0.15,
        relative_volume=316.0,
        catalyst_score=58.0,
        ema9=14.90,
        ema20=14.70,
        vwap=14.80,
        recent_volume=885000,
        avg_volume_20=2800,
        pullback_low=14.70,
        reclaim_level=15.0,
        pullback_high=15.30,
        key_support=14.93,       # PM high - 0.25*ATR (breakout support)
        key_resistance=15.60,    # price + 2*ATR (breakout extension)
        atr=0.30,
    )
    card = build_trade_card(stock, "long", 58, 2.5, "morning")
    assert card is not None, "Breakout setup should produce a valid card"
    assert card.entry_price == 15.0
    assert card.tp1_price == 15.60
    assert card.stop_price < card.entry_price
    assert card.risk_reward >= 1.5
