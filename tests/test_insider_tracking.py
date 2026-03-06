"""Tests for insider and institutional tracking module."""

from tradingbot.research.insider_tracking import (
    SmartMoneyTracker,
    InsiderTracker,
    InstitutionalTracker,
    CongressionalTradingTracker,
    InsiderTrade,
    InstitutionalPosition,
)
from datetime import datetime


def test_insider_tracker_initialization():
    """Test insider tracker can be initialized."""
    tracker = InsiderTracker()
    assert tracker is not None
    assert tracker.user_agent is not None
    assert len(tracker.TRANSACTION_TYPES) > 0
    assert len(tracker.SIGNIFICANT_TITLES) > 0


def test_institutional_tracker_initialization():
    """Test institutional tracker can be initialized."""
    tracker = InstitutionalTracker()
    assert tracker is not None
    assert len(tracker.WHALE_INVESTORS) > 0
    # Check that Berkshire Hathaway is in whale list
    assert "Berkshire Hathaway" in str(tracker.WHALE_INVESTORS.values())


def test_congressional_tracker_initialization():
    """Test congressional tracker can be initialized."""
    tracker = CongressionalTradingTracker()
    assert tracker is not None
    assert len(tracker.AMOUNT_RANGES) > 0


def test_smart_money_tracker_initialization():
    """Test smart money tracker can be initialized."""
    tracker = SmartMoneyTracker()
    assert tracker is not None
    assert tracker.insider_tracker is not None
    assert tracker.institutional_tracker is not None
    assert tracker.congressional_tracker is not None


def test_smart_money_score_calculation():
    """Test smart money score calculation with mock data."""
    tracker = SmartMoneyTracker()
    
    # Create mock insider trades (all purchases)
    insider_trades = [
        InsiderTrade(
            symbol="NVDA",
            insider_name="Jensen Huang",
            insider_title="CEO",
            transaction_date=datetime(2026, 3, 5),
            transaction_type="Purchase (Open Market)",
            shares=10000,
            price_per_share=850.0,
            total_value=8500000,
            shares_owned_after=100000,
            filing_date=datetime(2026, 3, 6),
            form_type="Form 4",
            is_significant=True,
        ),
        InsiderTrade(
            symbol="NVDA",
            insider_name="Colette Kress",
            insider_title="CFO",
            transaction_date=datetime(2026, 3, 5),
            transaction_type="Purchase (Open Market)",
            shares=5000,
            price_per_share=850.0,
            total_value=4250000,
            shares_owned_after=50000,
            filing_date=datetime(2026, 3, 6),
            form_type="Form 4",
            is_significant=True,
        ),
    ]
    
    # Create mock institutional positions (all increasing)
    institutional_positions = [
        InstitutionalPosition(
            symbol="NVDA",
            institution_name="ARK Investment Management",
            institution_cik="0001336528",
            shares_held=5000000,
            market_value=4250000000,
            percent_of_portfolio=4.2,
            filing_date=datetime(2026, 2, 15),
            quarter_end=datetime(2025, 12, 31),
            change_from_prior_quarter=1000000,
            percent_change=25.0,
        ),
    ]
    
    # Calculate score
    score = tracker._calculate_smart_money_score(
        insider_trades,
        institutional_positions
    )
    
    # Score should be bullish (all buys and increases)
    assert 50 < score <= 100, f"Expected bullish score, got {score}"


def test_identify_significant_trades():
    """Test identification of significant insider trades."""
    tracker = InsiderTracker()
    
    trades = [
        InsiderTrade(
            symbol="NVDA",
            insider_name="Jensen Huang",
            insider_title="CEO",
            transaction_date=datetime(2026, 3, 5),
            transaction_type="Purchase (Open Market)",
            shares=10000,
            price_per_share=850.0,
            total_value=8500000,
            shares_owned_after=100000,
            filing_date=datetime(2026, 3, 6),
            form_type="Form 4",
            is_significant=True,
        ),
        InsiderTrade(
            symbol="NVDA",
            insider_name="John Doe",
            insider_title="Vice President",
            transaction_date=datetime(2026, 3, 5),
            transaction_type="Sale (Open Market)",
            shares=1000,
            price_per_share=850.0,
            total_value=850000,
            shares_owned_after=5000,
            filing_date=datetime(2026, 3, 6),
            form_type="Form 4",
            is_significant=False,
        ),
    ]
    
    significant = tracker.identify_significant_trades(trades)
    
    # Should identify CEO purchase as significant
    assert len(significant) > 0
    assert significant[0].insider_title == "CEO"
    assert "Purchase" in significant[0].transaction_type


def test_identify_whale_moves():
    """Test identification of significant institutional moves."""
    tracker = InstitutionalTracker()
    
    positions = [
        InstitutionalPosition(
            symbol="PLTR",
            institution_name="ARK Investment Management",
            institution_cik="0001336528",  # Cathie Wood - is a whale
            shares_held=8500000,
            market_value=340000000,
            percent_of_portfolio=4.2,
            filing_date=datetime(2026, 2, 15),
            quarter_end=datetime(2025, 12, 31),
            change_from_prior_quarter=2100000,
            percent_change=32.7,
        ),
        InstitutionalPosition(
            symbol="PLTR",
            institution_name="Small Fund LLC",
            institution_cik="0009999999",  # Not a whale
            shares_held=100000,
            market_value=4000000,
            percent_of_portfolio=0.1,
            filing_date=datetime(2026, 2, 15),
            quarter_end=datetime(2025, 12, 31),
            change_from_prior_quarter=10000,
            percent_change=11.0,
        ),
    ]
    
    whale_moves = tracker.identify_whale_moves(positions)
    
    # Should identify ARK's move as significant
    assert len(whale_moves) > 0
    assert "ARK" in whale_moves[0].institution_name


def test_get_smart_money_signals():
    """Test getting combined smart money signals."""
    tracker = SmartMoneyTracker()
    
    # This will use empty/mock data but should not crash
    signals = tracker.get_smart_money_signals(
        symbols=["NVDA"],
        days_lookback=7
    )
    
    assert "NVDA" in signals
    assert "insider_trades" in signals["NVDA"]
    assert "institutional_positions" in signals["NVDA"]
    assert "smart_money_score" in signals["NVDA"]
    assert 0 <= signals["NVDA"]["smart_money_score"] <= 100
