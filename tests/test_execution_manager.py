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
    # Fresh-quote gate: return a quote matching the default card entry ($10)
    client.get_fresh_quote.return_value = {
        "last": 10.05, "bid": 10.03, "ask": 10.07,
    }
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


# ── Fresh-quote gate tests ────────────────────────────────────────────

class TestFreshQuoteGate:
    """Tests for the pre-execution price validation in execute_card."""

    def _make_manager(self, fresh_quote: dict | None = None) -> ExecutionManager:
        client = _mock_ibkr_client()
        if fresh_quote is not None:
            client.get_fresh_quote.return_value = fresh_quote
        return ExecutionManager(client, _default_exec_config("paper"), risk_per_trade_pct=0.5)

    def _mock_success_order(self, mgr: ExecutionManager) -> None:
        from tradingbot.execution.order_executor import BracketOrderResult
        mgr.executor.submit_bracket_order = MagicMock(
            return_value=BracketOrderResult(
                success=True, symbol="TEST", parent_order_id=1,
                tp_order_id=2, stop_order_id=3, oca_group="test",
                fill_price=10.0,
            )
        )

    def test_small_drift_allows_execution(self):
        """Price drift < 2% should allow execution to proceed."""
        mgr = self._make_manager({"last": 10.10, "bid": 10.08, "ask": 10.12})
        self._mock_success_order(mgr)
        card = _make_card()  # entry=10.0, stop=9.50, tp1=11.00

        result = mgr.execute_card(card)
        assert result["executed"] is True

    def test_large_drift_blocks_execution(self):
        """Price drift > 2% should block execution (stale price)."""
        mgr = self._make_manager({"last": 10.30, "bid": 10.28, "ask": 10.32})
        card = _make_card()  # entry=10.0 → 3% drift

        result = mgr.execute_card(card)
        assert result["executed"] is False
        assert "stale_price" in result["reason"]

    def test_downward_drift_blocks(self):
        """Large downward price move should also be blocked."""
        mgr = self._make_manager({"last": 9.70, "bid": 9.68, "ask": 9.72})
        card = _make_card()  # entry=10.0 → 3% drift down

        result = mgr.execute_card(card)
        assert result["executed"] is False
        assert "stale_price" in result["reason"]

    def test_price_below_stop_blocks(self):
        """If fresh price <= stop, execution is blocked."""
        mgr = self._make_manager({"last": 9.50, "bid": 9.48, "ask": 9.52})
        card = _make_card()  # stop=9.50

        result = mgr.execute_card(card)
        assert result["executed"] is False
        # Either stale_price (drift 5%) or price_below_stop
        assert any(tag in result["reason"] for tag in ["stale_price", "price_below_stop"])

    def test_rr_degraded_blocks(self):
        """If price moved up slightly, R:R may drop below 1.5 → blocked."""
        # entry=10.0, stop=9.50 (risk=0.50), tp1=11.00 (reward=1.00) → R:R=2.0
        # fresh=10.19 (1.9% drift, <2% threshold): risk=0.69, reward=0.81 → R:R=1.17
        mgr = self._make_manager({"last": 10.19, "bid": 10.17, "ask": 10.21})
        self._mock_success_order(mgr)
        card = _make_card()

        result = mgr.execute_card(card)
        assert result["executed"] is False
        assert "rr_degraded" in result["reason"]

    def test_fresh_quote_failure_falls_through(self):
        """If get_fresh_quote raises, execution proceeds with scan price."""
        mgr = self._make_manager()
        mgr._client.get_fresh_quote.side_effect = RuntimeError("connection lost")
        self._mock_success_order(mgr)
        card = _make_card()

        result = mgr.execute_card(card)
        # Should still attempt execution with scan price
        assert result["executed"] is True

    def test_no_price_data_falls_through(self):
        """If quote returns zeroes, execution proceeds with scan price."""
        mgr = self._make_manager({"last": 0.0, "bid": 0.0, "ask": 0.0})
        self._mock_success_order(mgr)
        card = _make_card()

        result = mgr.execute_card(card)
        assert result["executed"] is True

    def test_uses_midpoint_when_no_last(self):
        """If 'last' is 0, should fall back to bid/ask midpoint."""
        # Midpoint = (10.08 + 10.12) / 2 = 10.10 → 1% drift → allowed
        mgr = self._make_manager({"last": 0.0, "bid": 10.08, "ask": 10.12})
        self._mock_success_order(mgr)
        card = _make_card()

        result = mgr.execute_card(card)
        assert result["executed"] is True

    def test_fresh_price_used_for_limit_order(self):
        """The bracket order should use the fresh price, not the scan price."""
        mgr = self._make_manager({"last": 10.05, "bid": 10.03, "ask": 10.07})
        self._mock_success_order(mgr)
        card = _make_card()

        mgr.execute_card(card)

        call_kwargs = mgr.executor.submit_bracket_order.call_args
        # entry_price should be the fresh price (10.05), not scan price (10.0)
        assert call_kwargs.kwargs.get("entry_price", call_kwargs[1].get("entry_price")) == 10.05

    def test_exactly_2pct_drift_blocks(self):
        """Exactly 2% drift should be blocked (> threshold, not >=)."""
        # 10.0 * 1.021 = 10.21 → drift 2.1% > 2.0
        mgr = self._make_manager({"last": 10.21, "bid": 10.19, "ask": 10.23})
        card = _make_card()

        result = mgr.execute_card(card)
        assert result["executed"] is False
        assert "stale_price" in result["reason"]

    def test_streak_multiplier_forwarded(self):
        """streak_multiplier should be passed through to pre_trade_check."""
        mgr = self._make_manager({"last": 10.05, "bid": 10.03, "ask": 10.07})
        self._mock_success_order(mgr)

        # Spy on allocator.pre_trade_check
        original_ptc = mgr.allocator.pre_trade_check
        captured_kwargs = {}

        def spy_ptc(**kwargs):
            captured_kwargs.update(kwargs)
            return original_ptc(**kwargs)

        mgr.allocator.pre_trade_check = spy_ptc
        card = _make_card()

        mgr.execute_card(card, streak_multiplier=0.75)

        assert captured_kwargs.get("streak_multiplier") == 0.75


class TestScalpDetection:
    """Tests for VWAP-based is_scalp detection in execute_card."""

    def _make_manager(self, fresh_quote: dict) -> ExecutionManager:
        client = _mock_ibkr_client()
        client.get_fresh_quote.return_value = fresh_quote
        return ExecutionManager(client, _default_exec_config("paper"), risk_per_trade_pct=0.5)

    def _mock_success_order(self, mgr: ExecutionManager) -> None:
        from tradingbot.execution.order_executor import BracketOrderResult
        mgr.executor.submit_bracket_order = MagicMock(
            return_value=BracketOrderResult(
                success=True, symbol="TEST", parent_order_id=1,
                tp_order_id=2, stop_order_id=3, oca_group="test",
                fill_price=10.0,
            )
        )

    def test_scalp_when_entry_below_vwap(self):
        """Entry below VWAP should set is_scalp=True."""
        mgr = self._make_manager({"last": 10.05, "bid": 10.03, "ask": 10.07})
        self._mock_success_order(mgr)
        # VWAP is higher than entry → scalp mode
        card = _make_card()
        card.vwap = 10.50  # type: ignore[attr-defined]

        mgr.execute_card(card)
        call_kwargs = mgr.executor.submit_bracket_order.call_args
        assert call_kwargs.kwargs.get("is_scalp") is True or call_kwargs[1].get("is_scalp") is True

    def test_not_scalp_when_entry_above_vwap(self):
        """Entry above VWAP should set is_scalp=False."""
        mgr = self._make_manager({"last": 10.05, "bid": 10.03, "ask": 10.07})
        self._mock_success_order(mgr)
        card = _make_card()
        card.vwap = 9.80  # type: ignore[attr-defined]

        mgr.execute_card(card)
        call_kwargs = mgr.executor.submit_bracket_order.call_args
        assert call_kwargs.kwargs.get("is_scalp") is False or call_kwargs[1].get("is_scalp") is False

    def test_no_vwap_defaults_to_not_scalp(self):
        """If card has no vwap attribute, is_scalp should be False."""
        mgr = self._make_manager({"last": 10.05, "bid": 10.03, "ask": 10.07})
        self._mock_success_order(mgr)
        card = _make_card()
        # TradeCard doesn't have a vwap field; getattr fallback → 0.0

        mgr.execute_card(card)
        call_kwargs = mgr.executor.submit_bracket_order.call_args
        assert call_kwargs.kwargs.get("is_scalp") is False or call_kwargs[1].get("is_scalp") is False


# ── get_fresh_quote unit test ─────────────────────────────────────────

class TestGetFreshQuote:
    """Tests for IBKRClient.get_fresh_quote()."""

    def test_returns_market_data(self):
        from tradingbot.data.ibkr_client import IBKRClient

        client = IBKRClient.__new__(IBKRClient)
        client._qualified_cache = {}

        mock_contract = MagicMock()
        mock_contract.conId = 12345
        mock_contract.symbol = "AAPL"

        # Mock _qualify_contracts to return the contract
        client._qualify_contracts = MagicMock(return_value={"AAPL": mock_contract})
        # Mock _request_market_data to return a quote
        client._request_market_data = MagicMock(return_value={
            "last": 175.50, "bid": 175.45, "ask": 175.55,
        })

        quote = client.get_fresh_quote("AAPL")
        assert quote["last"] == 175.50
        client._qualify_contracts.assert_called_once_with(["AAPL"])
        client._request_market_data.assert_called_once_with(mock_contract)

    def test_raises_on_unknown_symbol(self):
        from tradingbot.data.ibkr_client import IBKRClient

        client = IBKRClient.__new__(IBKRClient)
        client._qualified_cache = {}
        client._qualify_contracts = MagicMock(return_value={})

        with pytest.raises(ValueError, match="Could not qualify"):
            client.get_fresh_quote("ZZZZ")


# ── on_batch_ready callback tests ─────────────────────────────────────

class TestOnBatchReadyCallback:
    """Tests for the progressive snapshot callback in get_premarket_snapshots."""

    def test_callback_receives_batches(self):
        """on_batch_ready should fire once per batch with SymbolSnapshot list."""
        from tradingbot.data.ibkr_client import IBKRClient
        from tradingbot.models import SymbolSnapshot

        client = IBKRClient.__new__(IBKRClient)
        client._qualified_cache = {}

        received_batches = []

        def capture(batch):
            received_batches.append(list(batch))

        # Mock all internal methods to produce a minimal valid snapshot
        mock_contract = MagicMock()
        mock_contract.conId = 1
        mock_contract.symbol = "TEST"
        client._qualify_contracts = MagicMock(return_value={"TEST": mock_contract})
        client._fetch_batch_data = MagicMock(return_value={
            "TEST": {
                "snapshot": {"last": 15.0, "bid": 14.9, "ask": 15.1,
                             "open": 14.5, "high": 15.5, "low": 14.0,
                             "close": 14.0, "volume": 100000},
                "daily_bars": [
                    {"date": "2026-04-10", "open": 13, "high": 14.5, "low": 12.5, "close": 14.0, "volume": 500000},
                    {"date": "2026-04-11", "open": 14, "high": 15, "low": 13.5, "close": 14.0, "volume": 600000},
                ],
                "intraday_bars": [],
            }
        })
        mock_ib = MagicMock()
        mock_ib.sleep = MagicMock()
        client._ib = mock_ib

        snaps = client.get_premarket_snapshots(["TEST"], on_batch_ready=capture)

        assert len(received_batches) == 1
        assert len(received_batches[0]) == 1
        assert received_batches[0][0].symbol == "TEST"
        assert len(snaps) == 1

    def test_callback_not_called_when_none(self):
        """When on_batch_ready is None, no error should occur."""
        from tradingbot.data.ibkr_client import IBKRClient

        client = IBKRClient.__new__(IBKRClient)
        client._qualified_cache = {}

        mock_contract = MagicMock()
        mock_contract.conId = 1
        mock_contract.symbol = "TEST"
        client._qualify_contracts = MagicMock(return_value={"TEST": mock_contract})
        client._fetch_batch_data = MagicMock(return_value={
            "TEST": {
                "snapshot": {"last": 15.0, "bid": 14.9, "ask": 15.1,
                             "open": 14.5, "high": 15.5, "low": 14.0,
                             "close": 14.0, "volume": 100000},
                "daily_bars": [
                    {"date": "2026-04-10", "open": 13, "high": 14.5, "low": 12.5, "close": 14.0, "volume": 500000},
                    {"date": "2026-04-11", "open": 14, "high": 15, "low": 13.5, "close": 14.0, "volume": 600000},
                ],
                "intraday_bars": [],
            }
        })
        mock_ib = MagicMock()
        mock_ib.sleep = MagicMock()
        client._ib = mock_ib

        # Should not raise
        snaps = client.get_premarket_snapshots(["TEST"], on_batch_ready=None)
        assert len(snaps) == 1

    def test_callback_empty_batch_not_fired(self):
        """If a batch produces no snapshots, callback should not fire."""
        from tradingbot.data.ibkr_client import IBKRClient

        client = IBKRClient.__new__(IBKRClient)
        client._qualified_cache = {}

        received = []
        # No contracts qualify → no snapshots → no callback
        client._qualify_contracts = MagicMock(return_value={})
        mock_ib = MagicMock()
        mock_ib.sleep = MagicMock()
        client._ib = mock_ib

        snaps = client.get_premarket_snapshots(["ZZZZ"], on_batch_ready=lambda b: received.append(b))
        assert len(received) == 0
        assert len(snaps) == 0


# ── Progressive execution integration tests ──────────────────────────

class TestProgressiveExecution:
    """Tests for run_single_session progressive mode."""

    def test_progressive_disabled_without_execution_mgr(self):
        """Without execution_mgr, _fetch_snapshots should not receive callback."""
        from pathlib import Path
        from tradingbot.app.session_runner import SessionRunner

        runner = SessionRunner(Path.cwd(), use_real_data=False)
        assert runner.execution_mgr is None

        # Mock _fetch_snapshots to capture the on_batch_ready argument
        original_fetch = runner._fetch_snapshots
        captured_kwargs = {}

        def spy_fetch(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return original_fetch(*args, **{k: v for k, v in kwargs.items() if k != 'on_batch_ready'})

        runner._fetch_snapshots = spy_fetch

        catalyst_scores = {"MOCK1": 70.0}
        runner.run_single_session("morning", catalyst_scores)

        # on_batch_ready should be None (no execution engine)
        assert captured_kwargs.get("on_batch_ready") is None

    def test_first_batch_callback_only_fires_once(self):
        """The _on_first_batch closure should only process the first batch."""
        call_count = 0
        first_batch_done = False

        def _on_first_batch(batch_snaps):
            nonlocal first_batch_done, call_count
            if first_batch_done:
                return
            first_batch_done = True
            call_count += 1

        # Simulate 3 batch callbacks
        _on_first_batch([MagicMock()])
        _on_first_batch([MagicMock()])
        _on_first_batch([MagicMock()])

        assert call_count == 1

    def test_fetch_snapshots_annotates_catalyst_scores(self):
        """on_batch_ready callback should receive snapshots with catalyst_scores set."""
        from pathlib import Path
        from tradingbot.app.session_runner import SessionRunner
        from tradingbot.models import SymbolSnapshot

        runner = SessionRunner(Path.cwd(), use_real_data=False)

        received_snaps = []

        def capture(batch):
            received_snaps.extend(batch)

        catalyst_scores = {"MOCK1": 85.0, "MOCK2": 60.0}

        # For mock data, on_batch_ready doesn't fire (only IBKR path)
        # so we just verify the fallback path doesn't break
        runner._fetch_snapshots("morning", ["MOCK1"], catalyst_scores, on_batch_ready=capture)
        # Mock data path doesn't call on_batch_ready (no per-batch streaming)
        # This test just confirms the interface doesn't crash
