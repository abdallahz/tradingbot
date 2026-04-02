"""Market condition analyzer to recommend optimal trading session."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from tradingbot.models import SymbolSnapshot


SessionType = Literal["night_research", "morning_premarket", "midday_rescan"]


@dataclass
class MarketCondition:
    """Analysis of current market conditions."""
    average_gap: float
    gappers_count: int  # stocks with gap >= 2%
    high_volume_count: int  # stocks with rel_vol >= 1.5x
    volatility_level: Literal["low", "medium", "high"]
    recommended_session: SessionType
    recommendation_reason: str
    # ── Dynamic filter thresholds (regime-adaptive) ──
    max_vwap_distance_pct: float = 3.0   # default: reject entries > 3% from VWAP
    min_catalyst_score: int = 40          # default catalyst gate
    min_relative_volume: float = 3.0      # volume conviction for low-catalyst stocks
    max_trades_per_day: int = 8           # default daily cap


class MarketConditionAnalyzer:
    """Analyzes market conditions to recommend optimal trading session."""
    
    def analyze(
        self, 
        morning_snapshots: list[SymbolSnapshot],
        midday_snapshots: list[SymbolSnapshot] | None = None,
        catalyst_scores: dict[str, float] | None = None,
    ) -> MarketCondition:
        """
        Analyze market conditions and recommend which session to focus on.
        
        Args:
            morning_snapshots: Pre-market snapshot data
            midday_snapshots: Optional midday snapshot data
            catalyst_scores: Optional catalyst scores from night research
            
        Returns:
            MarketCondition with analysis and recommendation
        """
        # Calculate market metrics from morning data
        gaps = [abs(s.gap_pct) for s in morning_snapshots if s.gap_pct is not None]
        avg_gap = sum(gaps) / len(gaps) if gaps else 0.0
        
        gappers_count = sum(1 for g in gaps if g >= 2.0)
        high_volume_count = sum(1 for s in morning_snapshots if s.relative_volume >= 1.5)
        
        # Determine volatility level
        if avg_gap >= 3.0 and gappers_count >= 5:
            volatility = "high"
        elif avg_gap >= 1.5 and gappers_count >= 3:
            volatility = "medium"
        else:
            volatility = "low"
        
        # Recommend session based on conditions
        recommended, reason = self._make_recommendation(
            volatility=volatility,
            avg_gap=avg_gap,
            gappers_count=gappers_count,
            high_volume_count=high_volume_count,
            has_catalyst_data=bool(catalyst_scores),
            midday_available=bool(midday_snapshots),
        )
        
        return MarketCondition(
            average_gap=avg_gap,
            gappers_count=gappers_count,
            high_volume_count=high_volume_count,
            volatility_level=volatility,
            recommended_session=recommended,
            recommendation_reason=reason,
            **self._dynamic_thresholds(volatility, gappers_count),
        )
    
    def _make_recommendation(
        self,
        volatility: Literal["low", "medium", "high"],
        avg_gap: float,
        gappers_count: int,
        high_volume_count: int,
        has_catalyst_data: bool,
        midday_available: bool,
    ) -> tuple[SessionType, str]:
        """
        Determine which session is optimal based on market conditions.
        
        Logic:
        - High volatility morning → Focus on morning premarket gappers
        - Low volatility morning + strong catalysts → Focus on night research picks with patience
        - Medium volatility → Check midday for fresh setups after morning consolidation
        """
        if volatility == "high" and gappers_count >= 5:
            return (
                "morning_premarket",
                f"High volatility with {gappers_count} strong gappers. "
                "Focus on premarket setups with quick entries at pullback."
            )
        
        if volatility == "low" and avg_gap < 1.5:
            if has_catalyst_data:
                return (
                    "night_research",
                    f"Low volatility market (avg gap {avg_gap:.1f}%). "
                    "Focus on catalyst-driven picks from night research. "
                    "Wait for news-driven momentum."
                )
            else:
                return (
                    "midday_rescan",
                    f"Low premarket activity (avg gap {avg_gap:.1f}%). "
                    "Wait for midday rescan to find fresh breakouts after morning consolidation."
                )
        
        # Medium volatility - balanced approach
        if midday_available and high_volume_count >= 3:
            return (
                "midday_rescan",
                f"Moderate premarket activity with {high_volume_count} high-volume stocks. "
                "Monitor morning setups but also check midday for continuation patterns."
            )
        
        # Default to morning premarket
        return (
            "morning_premarket",
            f"Standard market conditions (avg gap {avg_gap:.1f}%, {gappers_count} gappers). "
            "Focus on morning premarket scan with standard filters."
        )

    @staticmethod
    def _dynamic_thresholds(
        volatility: Literal["low", "medium", "high"],
        gappers_count: int,
    ) -> dict:
        """Return regime-adaptive filter thresholds.

        High-volatility days → tighten filters (more noise, need conviction).
        Low-volatility days  → loosen VWAP + catalyst (fewer setups, take what's there).
        """
        if volatility == "high":
            return {
                "max_vwap_distance_pct": 2.0,   # tighter: avoid chasing extended gaps
                "min_catalyst_score": 50,         # raise bar: more noise on wild days
                "min_relative_volume": 4.0,       # demand stronger volume conviction
                "max_trades_per_day": 5,          # fewer but higher-quality slots
            }
        if volatility == "low":
            return {
                "max_vwap_distance_pct": 3.5,     # widen slightly: moves are smaller
                "min_catalyst_score": 40,          # moderate bar
                "min_relative_volume": 3.0,        # accept reasonable volume
                "max_trades_per_day": 8,           # moderate number of slots
            }
        # Medium (default)
        return {
            "max_vwap_distance_pct": 3.0,
            "min_catalyst_score": 45,
            "min_relative_volume": 3.0,
            "max_trades_per_day": 8,
        }
