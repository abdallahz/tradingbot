"""
swap_evaluator.py — Position swap evaluation engine.

Compares new trade candidates against currently open positions to decide
whether closing a weaker hold to fund a stronger new setup is warranted.

Considers ALL open positions as swap candidates — not just losers.
A winning position that is stalling under TP1 or between TP1 and TP2
is a valid swap candidate if a significantly stronger new setup appears.

Two modes:
  shadow  — log recommendations + Telegram message (no execution)
  auto    — execute the swap: market-sell weakest, enter new card
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from tradingbot.risk.position_scorer import HoldScore, PositionScorer, PositionState

if TYPE_CHECKING:
    from tradingbot.models import TradeCard

logger = logging.getLogger(__name__)


# ── Data structures ─────────────────────────────────────────────────────

@dataclass
class SwapRecommendation:
    """A recommended position swap: close one position, enter another."""

    close_symbol: str
    close_hold_score: HoldScore
    close_position: PositionState
    new_card_symbol: str
    new_card_score: float  # Ranker score of the new card
    margin: float  # new_score - weakest_hold_score
    reasons: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """Human-readable summary for Telegram / logging."""
        pos = self.close_position
        pnl_pct = (
            (pos.current_price - pos.entry_price) / pos.entry_price * 100
            if pos.entry_price > 0
            else 0.0
        )
        pnl_sign = "+" if pnl_pct >= 0 else ""

        stall_tag = " [STALLING]" if self.close_hold_score.stalling else ""

        lines = [
            f"🔄 *SWAP SIGNAL*",
            f"",
            f"*Close* `{self.close_symbol}` (hold score: {self.close_hold_score.total:.0f}/100{stall_tag})",
            f"  Entry ${pos.entry_price:.2f} → Current ${pos.current_price:.2f} ({pnl_sign}{pnl_pct:.1f}%)",
            f"  TP1 ${pos.tp1_price:.2f} | TP2 ${pos.tp2_price:.2f}",
            f"",
            f"*Enter* `{self.new_card_symbol}` (score: {self.new_card_score:.0f}/100)",
            f"  Margin: +{self.margin:.0f} points over weakest hold",
        ]
        if self.reasons:
            lines.append(f"  Reasons: {', '.join(self.reasons)}")
        return "\n".join(lines)


# ── Evaluator ───────────────────────────────────────────────────────────

class SwapEvaluator:
    """Compare new trade candidates against open positions.

    Returns a SwapRecommendation when closing the weakest open position
    and entering the new card would be an improvement above the
    configured threshold.
    """

    def __init__(
        self,
        swap_threshold: float = 20.0,
        min_hold_minutes: int = 15,
        scorer: PositionScorer | None = None,
    ) -> None:
        self.swap_threshold = swap_threshold
        self.min_hold_minutes = min_hold_minutes
        self.scorer = scorer or PositionScorer()

    def evaluate(
        self,
        new_card: "TradeCard",
        open_positions: list[PositionState],
    ) -> SwapRecommendation | None:
        """Evaluate whether any open position should be swapped for new_card.

        Returns SwapRecommendation if swap is warranted, None otherwise.
        """
        if not open_positions:
            return None

        new_score = new_card.score
        if new_score <= 0:
            return None

        # Score all open positions
        scored: list[tuple[PositionState, HoldScore]] = []
        for pos in open_positions:
            hold = self.scorer.score(pos)
            scored.append((pos, hold))
            logger.debug(
                f"[SWAP] {pos.symbol}: hold_score={hold.total:.0f} "
                f"(progress={hold.target_progress:.0f}, dir={hold.pnl_direction:.0f}, "
                f"vol={hold.volume_trend:.0f}, time={hold.time_efficiency:.0f}, "
                f"risk={hold.risk_buffer:.0f}, stalling={hold.stalling})"
            )

        # Find weakest position
        weakest_pos, weakest_hold = min(scored, key=lambda x: x[1].total)

        # Calculate swap margin
        margin = new_score - weakest_hold.total

        if margin < self.swap_threshold:
            logger.info(
                f"[SWAP] No swap: {new_card.symbol} (score={new_score:.0f}) vs "
                f"weakest {weakest_pos.symbol} (hold={weakest_hold.total:.0f}), "
                f"margin={margin:.0f} < threshold={self.swap_threshold:.0f}"
            )
            return None

        # Build reasons
        reasons: list[str] = []
        if weakest_hold.stalling:
            pnl_pct = (
                (weakest_pos.current_price - weakest_pos.entry_price)
                / weakest_pos.entry_price
                * 100
                if weakest_pos.entry_price > 0
                else 0.0
            )
            if pnl_pct > 0:
                reasons.append(f"winning but stalling ({pnl_pct:+.1f}%)")
            else:
                reasons.append(f"losing and stalling ({pnl_pct:+.1f}%)")

        if weakest_hold.target_progress < 5:
            reasons.append("minimal target progress")
        if weakest_hold.volume_trend < 6:
            reasons.append("declining volume")
        if weakest_hold.pnl_direction < 6:
            reasons.append("deteriorating P&L")
        if weakest_hold.time_efficiency < 5:
            reasons.append("slow mover")

        if not reasons:
            reasons.append(f"low hold score ({weakest_hold.total:.0f})")

        rec = SwapRecommendation(
            close_symbol=weakest_pos.symbol,
            close_hold_score=weakest_hold,
            close_position=weakest_pos,
            new_card_symbol=new_card.symbol,
            new_card_score=new_score,
            margin=margin,
            reasons=reasons,
        )

        logger.info(
            f"[SWAP] RECOMMENDED: close {weakest_pos.symbol} "
            f"(hold={weakest_hold.total:.0f}) → enter {new_card.symbol} "
            f"(score={new_score:.0f}), margin={margin:.0f}"
        )

        return rec
