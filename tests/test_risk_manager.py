from tradingbot.models import RiskState
from tradingbot.risk.risk_manager import RiskManager


def test_daily_loss_blocks_new_trades():
    manager = RiskManager(max_trades_per_day=3, daily_loss_lockout_pct=1.5, max_consecutive_losses=2)
    state = RiskState(daily_pnl_pct=-1.6)
    assert manager.allow_new_trade(state) is False
