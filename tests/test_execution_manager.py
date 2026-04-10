"""Tests for ExecutionManager and its integration with session_runner."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from tradingbot.execution.execution_manager import ExecutionManager, create_execution_manager
from tradingbot.models import TradeCard


# ── Fixtures ──────────────────────────────────────────────────────────

def _make_card(**overrides) -> TradeCard:
    """Build a minimal TradeCard for testing."""
    defaults = dict(
        symbol="TEST",
        score=75.0,
        entry_price=10.0,
        stop_price=9.50,
        tp1_price=11.00,
        tp2_price=12.00,
        invalidation_price=9.40,
        session_tag="morning",
    )
    defaults.update(overrides)
    return TradeCard(**defaults)  # type: ignore[arg-type]


def _mock_ibkr_client():
    """Create a mock IBKRClient with the minimum API surface."""
    client = MagicMock()
    client.get_account_summary.return_value = {
        "net_liquidation": 100_000.0,
        "buying_power": 200_000.0,
        "cash_balance": 100_000.0,
    }
    client.is_connected.return_value = True
    client.get_positions.return_value = []
    client.get_open_orders.return_value = []
    return client


def _default_exec_config(mode: str = "paper") -> dict:
    return {
        "mode": mode,
        "max_concurrent_positions": 3,
        "max_morning_entries": 2,
        "reserve_midday_slots": 1,
        "max_notional_per_trade": 10_000.0,
        "pdt_protection": True,
        "pdt_threshold": 25_000.0,
        "midday_use_market_order": True,
        "entry_order_buffer_pct": 0.1,
    }


# ── create_execution_manager factory tests ────────────────────────────

class TestCreateExecutionManager:
    def test_alert_only_returns_none(self):
        """alert_only mode should not create an ExecutionManager."""
        client = _mock_ibkr_client()
        risk_cfg = {"risk": {"risk_per_trade_pct": 0.5}, "execution": {"mode": "alert_only"}}
        result = create_execution_manager(client, risk_cfg)
        assert result is None

    def test_env_override_alert_only(self, monkeypatch):
        """EXECUTION_MODE env var should override config."""
        monkeypatch.setenv("EXECUTION_MODE", "alert_only")
        client = _mock_ibkr_client()
        risk_cfg = {"risk": {"risk_per_trade_pct": 0.5}, "execution": {"mode": "paper"}}
        result = create_execution_manager(client, risk_cfg)
        assert result is None

    def test_non_ibkr_client_returns_none(self):
        """Execution requires IBKRClient — AlpacaClient should fall back."""
        # Mock an object that is NOT an IBKRClient instance
        fake_alpaca = MagicMock(spec=[])
        # Patch isinstance check
        risk_cfg = {"risk": {"risk_per_trade_pct": 0.5}, "execution": {"mode": "paper"}}
        result = create_execution_manager(fake_alpaca, risk_cfg)
        assert result is None

    def test_paper_mode_with_ibkr_creates_manager(self):
        """Paper mode + IBKRClient should create an ExecutionManager."""
        from tradingbot.data.ibkr_client import IBKRClient

        client = _mock_ibkr_client()
        client.__class__ = IBKRClient  # Make isinstance check pass
        risk_cfg = {"risk": {"risk_per_trade_pct": 0.5}, "execution": {"mode": "paper"}}
        result = create_execution_manager(client, risk_cfg)
        assert result is not None
        assert isinstance(result, ExecutionManager)

    def test_env_override_paper_mode(self, monkeypatch):
        """EXECUTION_MODE=paper env var should enable execution."""
        from tradingbot.data.ibkr_client import IBKRClient

        monkeypatch.setenv("EXECUTION_MODE", "paper")
        client = _mock_ibkr_client()
        client.__class__ = IBKRClient
        risk_cfg = {"risk": {"risk_per_trade_pct": 0.5}, "execution": {"mode": "alert_only"}}
        result = create_execution_manager(client, risk_cfg)
        assert result is not None


# ── ExecutionManager.execute_card tests ───────────────────────────────

class TestExecuteCard:
    def _make_manager(self) -> ExecutionManager:
        client = _mock_ibkr_client()
        mgr = ExecutionManager(client, _default_exec_config("paper"), risk_per_trade_pct=0.5)
        return mgr

    def test_execute_card_basic(self):
        """A valid card should go through pre-trade check and attempt submission."""
        mgr = self._make_manager()
        card = _make_card()

        # Mock the OrderExecutor to return a success result
        from tradingbot.execution.order_executor import BracketOrderResult
        mock_result = BracketOrderResult(
            success=True,
            symbol="TEST",
            parent_order_id=1,
            tp_order_id=2,
            stop_order_id=3,
            oca_group="bracket_TEST_123",
            fill_price=10.02,
            filled_quantity=10,
        )
        mgr.executor.submit_bracket_order = MagicMock(return_value=mock_result)

        result = mgr.execute_card(card)
        assert result["executed"] is True
        assert result["shares"] > 0
        assert result["reason"] == "ok"
        assert mgr.executor.submit_bracket_order.called

    def test_execute_card_insufficient_capital(self):
        """Zero account value should block execution."""
        mgr = self._make_manager()
        mgr.allocator.update_account(0.0, 0.0, 0.0)  # broke
        card = _make_card()

        result = mgr.execute_card(card)
        assert result["executed"] is False
        assert result["shares"] == 0

    def test_execute_card_rejected_order(self):
        """If IBKR rejects the order, result should show not executed."""
        mgr = self._make_manager()
        card = _make_card()

        from tradingbot.execution.order_executor import BracketOrderResult
        mock_result = BracketOrderResult(
            success=False, symbol="TEST", error="Contract not found"
        )
        mgr.executor.submit_bracket_order = MagicMock(return_value=mock_result)

        result = mgr.execute_card(card)
        assert result["executed"] is False
        assert "order rejected" in result["reason"]

    def test_execute_card_records_position(self):
        """After successful execution, position should be booked."""
        mgr = self._make_manager()
        card = _make_card()

        from tradingbot.execution.order_executor import BracketOrderResult
        mock_result = BracketOrderResult(
            success=True, symbol="TEST", parent_order_id=1,
            tp_order_id=2, stop_order_id=3, oca_group="test",
            fill_price=10.0, filled_quantity=10,
        )
        mgr.executor.submit_bracket_order = MagicMock(return_value=mock_result)

        result = mgr.execute_card(card)
        assert result["executed"] is True
        assert mgr.allocator.open_position_count == 1
        assert "TEST" in mgr.allocator.open_symbols

    def test_execute_card_sets_position_size_on_card(self):
        """Execution should annotate the card with the actual share count."""
        mgr = self._make_manager()
        card = _make_card()

        from tradingbot.execution.order_executor import BracketOrderResult
        mock_result = BracketOrderResult(
            success=True, symbol="TEST", parent_order_id=1,
            tp_order_id=2, stop_order_id=3, oca_group="test",
        )
        mgr.executor.submit_bracket_order = MagicMock(return_value=mock_result)

        mgr.execute_card(card)
        assert card.position_size > 0

    def test_max_concurrent_blocks_extra_trade(self):
        """After max morning entries, additional morning trades should be blocked."""
        mgr = self._make_manager()
        # max_morning_entries=2, reserve_midday_slots=1, max_concurrent=3
        # So morning can only fill 2 slots (the 3rd is reserved for midday).

        from tradingbot.execution.order_executor import BracketOrderResult
        mock_result = BracketOrderResult(
            success=True, symbol="", parent_order_id=1,
            tp_order_id=2, stop_order_id=3, oca_group="test",
        )
        mgr.executor.submit_bracket_order = MagicMock(return_value=mock_result)

        for i, sym in enumerate(["AAA", "BBB"]):
            mock_result.symbol = sym
            card = _make_card(symbol=sym)
            result = mgr.execute_card(card)
            assert result["executed"] is True, f"Trade {i+1} should succeed"

        # 3rd morning trade should be blocked (morning limit reached)
        card3 = _make_card(symbol="CCC")
        result3 = mgr.execute_card(card3)
        assert result3["executed"] is False

    def test_midday_uses_market_order(self):
        """Midday session should use market order when configured."""
        mgr = self._make_manager()
        card = _make_card(session_tag="midday")

        from tradingbot.execution.order_executor import BracketOrderResult
        mock_result = BracketOrderResult(
            success=True, symbol="TEST", parent_order_id=1,
            tp_order_id=2, stop_order_id=3, oca_group="test",
        )
        mgr.executor.submit_bracket_order = MagicMock(return_value=mock_result)

        mgr.execute_card(card)

        call_kwargs = mgr.executor.submit_bracket_order.call_args
        assert call_kwargs[1]["use_market_order"] is True or call_kwargs.kwargs.get("use_market_order") is True


# ── ExecutionManager delegate tests ───────────────────────────────────

class TestDelegates:
    def _make_manager(self) -> ExecutionManager:
        client = _mock_ibkr_client()
        return ExecutionManager(client, _default_exec_config("paper"))

    def test_check_trails(self):
        mgr = self._make_manager()
        mgr.executor.check_and_trail = MagicMock(return_value=None)
        actions = mgr.check_trails({"TEST": 11.0})
        assert isinstance(actions, list)

    def test_morning_deadline(self):
        mgr = self._make_manager()
        mgr.executor.morning_deadline_check = MagicMock(return_value=["action1"])
        actions = mgr.morning_deadline({"TEST": 10.5})
        assert actions == ["action1"]

    def test_expire_all(self):
        mgr = self._make_manager()
        mgr.executor.expire_all = MagicMock(return_value=["expired1"])
        actions = mgr.expire_all()
        assert actions == ["expired1"]

    def test_kill_all(self):
        mgr = self._make_manager()
        mgr.executor.kill_all = MagicMock(return_value=["killed1"])
        actions = mgr.kill_all()
        assert actions == ["killed1"]

    def test_reconcile(self):
        mgr = self._make_manager()
        from tradingbot.execution.position_monitor import ReconciliationResult
        mgr.monitor.reconcile = MagicMock(return_value=ReconciliationResult(matched=2))
        result = mgr.reconcile()
        assert result.matched == 2

    def test_get_status(self):
        mgr = self._make_manager()
        mgr.executor.get_status = MagicMock(return_value={
            "open_count": 0, "closed_count": 0,
            "open_trades": [], "closed_today": [],
        })
        mgr.monitor.get_health_status = MagicMock(return_value={
            "connected": True, "managed_open": 0,
        })
        status = mgr.get_status()
        assert status["mode"] == "paper"
        assert "account_value" in status

    def test_reset_daily(self):
        mgr = self._make_manager()
        mgr.reset_daily()
        # Should sync account and reset allocator
        assert mgr.allocator._morning_entries_today == 0

    def test_error_in_delegate_is_caught(self):
        """Delegates should catch exceptions and not re-raise."""
        mgr = self._make_manager()
        mgr.executor.expire_all = MagicMock(side_effect=RuntimeError("IB disconnected"))
        actions = mgr.expire_all()
        assert len(actions) == 1
        assert "ERROR" in actions[0]


# ── session_runner _maybe_execute integration ─────────────────────────

class TestMaybeExecute:
    def test_no_execution_when_manager_is_none(self):
        """When execution_mgr is None, _maybe_execute should be a no-op."""
        from pathlib import Path
        from tradingbot.app.session_runner import SessionRunner

        runner = SessionRunner(Path.cwd(), use_real_data=False)
        assert runner.execution_mgr is None

        card = _make_card()
        # Should not raise
        runner._maybe_execute(card)

    def test_execution_called_when_manager_exists(self):
        """When execution_mgr exists, execute_card should be called."""
        from pathlib import Path
        from tradingbot.app.session_runner import SessionRunner

        runner = SessionRunner(Path.cwd(), use_real_data=False)
        runner.execution_mgr = MagicMock()
        runner.execution_mgr.execute_card.return_value = {
            "executed": True, "symbol": "TEST", "shares": 10, "reason": "ok",
        }

        card = _make_card()
        runner._maybe_execute(card)

        runner.execution_mgr.execute_card.assert_called_once_with(card)

    def test_execution_error_is_logged_not_raised(self):
        """If execute_card raises, _maybe_execute should not propagate."""
        from pathlib import Path
        from tradingbot.app.session_runner import SessionRunner

        runner = SessionRunner(Path.cwd(), use_real_data=False)
        runner.execution_mgr = MagicMock()
        runner.execution_mgr.execute_card.side_effect = RuntimeError("boom")

        card = _make_card()
        # Should not raise
        runner._maybe_execute(card)
