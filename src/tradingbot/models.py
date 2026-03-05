from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Side = Literal["long", "short"]


@dataclass
class SymbolSnapshot:
    symbol: str
    price: float
    gap_pct: float
    premarket_volume: int
    dollar_volume: float
    spread_pct: float
    relative_volume: float
    catalyst_score: float
    ema9: float
    ema20: float
    vwap: float
    recent_volume: int
    avg_volume_20: int
    pullback_low: float
    reclaim_level: float
    pullback_high: float


@dataclass
class TradeCard:
    symbol: str
    side: Side
    score: float
    entry_price: float
    stop_price: float
    tp1_price: float
    tp2_price: float
    invalidation_price: float
    session_tag: Literal["morning", "midday"]
    reason: list[str] = field(default_factory=list)


@dataclass
class RiskState:
    trades_taken: int = 0
    consecutive_losses: int = 0
    daily_pnl_pct: float = 0.0
    locked_out: bool = False


@dataclass
class WatchlistRun:
    generated_at: datetime
    run_type: Literal["morning", "midday"]
    cards: list[TradeCard]
    dropped: list[tuple[str, str]]


@dataclass
class NightResearchResult:
    """Top catalyst-driven picks from night research."""
    symbol: str
    catalyst_score: float
    reasons: list[str]


@dataclass
class ThreeOptionWatchlist:
    """Watchlist with 3 trading approaches and market-based recommendation."""
    generated_at: datetime
    run_type: Literal["morning", "midday"]
    
    # Option 1: Night research catalyst picks
    night_research_picks: list[NightResearchResult]
    
    # Option 2: Relaxed filters (more opportunities)
    relaxed_filter_cards: list[TradeCard]
    
    # Option 3: Strict filters (high probability)
    strict_filter_cards: list[TradeCard]
    
    # Market analysis and recommendation
    recommended_option: Literal["night_research", "relaxed_filters", "strict_filters"]
    recommendation_reason: str
    market_volatility: Literal["low", "medium", "high"]
    average_gap: float
    gappers_count: int
