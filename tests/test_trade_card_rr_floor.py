from tradingbot.models import SymbolSnapshot
from tradingbot.strategy.trade_card import build_trade_card

def test_trade_card_rr_floor():
    stock = SymbolSnapshot(
        symbol="LOWRR",
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
    )
    # Use a stop very close to entry to force low R:R
    card = build_trade_card(stock, "long", 80, 0.05, "morning")
    assert card is None, "Trade card with R:R below floor should be dropped"
