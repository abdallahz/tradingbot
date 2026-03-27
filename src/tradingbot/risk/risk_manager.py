from __future__ import annotations

from tradingbot.models import RiskState


class RiskManager:
    def __init__(
        self,
        max_trades_per_day: int,
        daily_loss_lockout_pct: float,
        max_consecutive_losses: int,
    ) -> None:
        self.max_trades_per_day = max_trades_per_day
        self.daily_loss_lockout_pct = daily_loss_lockout_pct
        self.max_consecutive_losses = max_consecutive_losses

    def allow_new_trade(self, state: RiskState) -> bool:
        if state.locked_out:
            return False
        if state.trades_taken >= self.max_trades_per_day:
            return False
        if state.daily_pnl_pct <= -abs(self.daily_loss_lockout_pct):
            return False
        if state.consecutive_losses >= self.max_consecutive_losses:
            return False
        return True

    def streak_size_multiplier(self, state: RiskState) -> float:
        """Return a position-size multiplier based on consecutive losses.

        Instead of an all-or-nothing lockout, this gradually scales down
        position sizes after each loss to preserve capital while still
        allowing recovery trades:
            0 losses → 1.00  (full size)
            1 loss   → 0.75
            2 losses → 0.50
            3 losses → 0.35  (minimum before hard lockout)

        After a win, consecutive_losses resets to 0 → back to full size.
        """
        losses = state.consecutive_losses
        if losses <= 0:
            return 1.0
        if losses == 1:
            return 0.75
        if losses == 2:
            return 0.50
        return 0.35  # 3+ losses: stay alive but very small

    def update_after_result(self, state: RiskState, pnl_pct: float) -> RiskState:
        state.daily_pnl_pct += pnl_pct
        state.trades_taken += 1
        if pnl_pct < 0:
            state.consecutive_losses += 1
        else:
            state.consecutive_losses = 0

        if (
            state.daily_pnl_pct <= -abs(self.daily_loss_lockout_pct)
            or state.consecutive_losses >= self.max_consecutive_losses
            or state.trades_taken >= self.max_trades_per_day
        ):
            state.locked_out = True
        return state
