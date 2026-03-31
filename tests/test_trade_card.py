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
    card = build_trade_card(stock, 80, 2.5, "morning")
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
    assert build_trade_card(stock, 80, 2.5, "morning") is None


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
    assert build_trade_card(stock, 80, 2.5, "morning") is None


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
    card = build_trade_card(stock, 58, 2.5, "morning")
    assert card is not None, "Breakout setup should produce a valid card"
    assert card.entry_price == 15.0
    assert card.tp1_price == 15.60
    assert card.stop_price < card.entry_price
    assert card.risk_reward >= 1.5


def _make_stock(**overrides) -> SymbolSnapshot:
    """Helper: default stock snapshot with optional overrides."""
    defaults = dict(
        symbol="TEST",
        price=10.0,
        gap_pct=5.0,
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
        key_resistance=10.8,
        atr=0.30,
    )
    defaults.update(overrides)
    return SymbolSnapshot(**defaults)


# ── ATR minimum stop distance ───────────────────────────────────────

class TestATRMinimumStopDistance:
    """Stop should never be tighter than 0.5 * ATR (noise floor)."""

    def test_stop_widened_when_too_tight(self):
        """Support very close to entry → level_stop barely below entry.
        0.5*ATR = 0.15, but level stop only 0.10 from entry → should widen."""
        stock = _make_stock(
            price=10.0,
            key_support=9.95,   # very close support
            key_resistance=10.80,
            atr=0.30,           # 0.5*ATR = 0.15
        )
        card = build_trade_card(stock, 80, 2.5, "morning")
        assert card is not None
        # Stop should be at least 0.15 below entry (0.5*ATR)
        risk = card.entry_price - card.stop_price
        assert risk >= 0.15, f"Stop too tight: risk={risk:.4f}, min=0.15"

    def test_stop_not_widened_when_already_wide(self):
        """Support far from entry → level stop already > 0.5*ATR → no widening."""
        stock = _make_stock(
            price=20.0,
            key_support=19.70,  # level stop = 19.70 - 0.50 = 19.20, risk = 0.80
            key_resistance=22.00,
            atr=1.00,           # 0.5*ATR = 0.50, well below the 0.80 risk
        )
        # max_tp_dist = min(3.0, 1.2)=1.2 → tp1=21.2, R:R=1.2/0.80=1.5 ✓
        card = build_trade_card(stock, 80, 6.0, "morning")
        assert card is not None
        # Risk should be level-derived (~0.80), not ATR-min (0.50)
        risk = card.entry_price - card.stop_price
        assert risk >= 0.75, f"Stop should be level-derived, got risk={risk:.4f}"
        assert risk <= 0.85, f"Stop should not be widened, got risk={risk:.4f}"

    def test_atr_widening_respects_max_cap(self):
        """ATR very large → widened stop would breach fixed_stop_pct cap.
        In that case, don't widen beyond the cap."""
        stock = _make_stock(
            price=10.0,
            key_support=9.92,   # tight support
            key_resistance=10.80,
            atr=1.00,           # 0.5*ATR = 0.50, but 2.5% cap = $0.25
        )
        card = build_trade_card(stock, 80, 2.5, "morning")
        assert card is not None
        risk = card.entry_price - card.stop_price
        # Should not exceed fixed_stop_pct (2.5% of $10 = $0.25)
        assert risk <= 0.26  # tiny rounding tolerance


# ── Stop buffer multiplier (market guard) ────────────────────────────

class TestStopBufferMultiplier:
    """stop_buffer_multiplier should widen the ATR buffer in weak markets."""

    def test_green_market_normal_buffer(self):
        """multiplier=1.0 → default ATR buffer."""
        stock = _make_stock(
            price=10.0,
            key_support=9.50,
            key_resistance=10.80,
            atr=0.40,
        )
        card_normal = build_trade_card(stock, 80, 2.5, "morning", stop_buffer_multiplier=1.0)
        assert card_normal is not None
        assert card_normal.stop_price < card_normal.entry_price

    def test_yellow_market_widens_stop(self):
        """multiplier=1.5 → wider stop gives more breathing room."""
        stock = _make_stock(
            price=10.0,
            key_support=9.50,
            key_resistance=10.80,
            atr=0.40,
        )
        card_normal = build_trade_card(stock, 80, 2.5, "morning", stop_buffer_multiplier=1.0)
        card_yellow = build_trade_card(stock, 80, 2.5, "morning", stop_buffer_multiplier=1.5)
        assert card_normal is not None
        assert card_yellow is not None
        # Yellow market stop should be lower (wider buffer)
        assert card_yellow.stop_price <= card_normal.stop_price

    def test_buffer_multiplier_affects_atr_buffer_only(self):
        """The multiplier scales the ATR buffer, not the fixed_stop_pct cap."""
        stock = _make_stock(
            price=10.0,
            key_support=9.50,
            key_resistance=10.80,
            atr=0.40,
        )
        card = build_trade_card(stock, 80, 2.5, "morning", stop_buffer_multiplier=1.5)
        assert card is not None
        # Stop should still respect the fixed_stop_pct upper bound
        max_risk = card.entry_price * 0.025
        actual_risk = card.entry_price - card.stop_price
        assert actual_risk <= max_risk + 0.01  # small rounding tolerance
