"""Tests for ExecutionTracker — IBKR position management during market hours."""
from __future__ import annotations

import types
from datetime import datetime, time as dt_time
from typing import Any
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

ET = ZoneInfo("America/New_York")


# ── Fixtures ──────────────────────────────────────────────────────────────

def _make_managed_trade(symbol: str, closed: bool = False):
    """Minimal stub for ManagedTrade."""
    t = types.SimpleNamespace(
        symbol=symbol,
        closed=closed,
        entry_price=100.0,
        stop_price=97.0,
        tp1_price=105.0,
        tp2_price=110.0,
        quantity=10,
        current_stop=97.0,
        trail_stage=0,
    )
    return t


def _make_mock_mgr(open_symbols: list[str] | None = None):
    """Build a mock ExecutionManager with sensible defaults."""
    mgr = MagicMock()
    # managed_trades property → dict of open trades
    trades = {}
    for sym in (open_symbols or []):
        trades[sym] = _make_managed_trade(sym)
    mgr.executor.managed_trades = trades
    # Default return values
    mgr.check_trails.return_value = []
    mgr.check_fills.return_value = []
    mgr.morning_deadline.return_value = []
    mgr.expire_all.return_value = []
    mgr.kill_all.return_value = []
    mgr.reconcile.return_value = None
    mgr.reset_daily.return_value = None
    mgr.get_status.return_value = {"mode": "paper", "open_positions": 0}
    # _client.get_latest_prices
    mgr._client.get_latest_prices.return_value = {sym: 102.0 for sym in (open_symbols or [])}
    return mgr


# ── Factory tests ─────────────────────────────────────────────────────────

class TestCreateExecutionTracker:
    def test_returns_none_when_no_manager(self):
        from tradingbot.tracking.execution_tracker import create_execution_tracker
        assert create_execution_tracker(None) is None

    def test_returns_tracker_when_manager_present(self):
        from tradingbot.tracking.execution_tracker import (
            ExecutionTracker,
            create_execution_tracker,
        )
        mgr = _make_mock_mgr()
        tracker = create_execution_tracker(mgr)
        assert isinstance(tracker, ExecutionTracker)


# ── Tick — no open positions ──────────────────────────────────────────────

class TestTickEmpty:
    def test_tick_no_positions_returns_zeros(self):
        from tradingbot.tracking.execution_tracker import ExecutionTracker
        mgr = _make_mock_mgr(open_symbols=[])
        tracker = ExecutionTracker(mgr)
        result = tracker.tick()
        assert result["trails"] == 0
        assert result["fills"] == 0
        assert result["morning_deadline"] is False
        assert result["expired"] is False
        assert result["reconciled"] is False
        # Nothing to check — should not call check_trails
        mgr.check_trails.assert_not_called()

    def test_tick_all_closed_returns_zeros(self):
        from tradingbot.tracking.execution_tracker import ExecutionTracker
        mgr = _make_mock_mgr(open_symbols=[])
        # Add a closed trade manually
        mgr.executor.managed_trades["AAPL"] = _make_managed_trade("AAPL", closed=True)
        tracker = ExecutionTracker(mgr)
        result = tracker.tick()
        assert result["trails"] == 0


# ── Tick — trailing stops ─────────────────────────────────────────────────

class TestTickTrails:
    def test_tick_calls_check_trails_with_prices(self):
        from tradingbot.tracking.execution_tracker import ExecutionTracker
        mgr = _make_mock_mgr(open_symbols=["AAPL", "TSLA"])
        mgr.check_trails.return_value = ["AAPL: BE trail", "TSLA: +1R trail"]
        tracker = ExecutionTracker(mgr)
        result = tracker.tick()
        assert result["trails"] == 2
        mgr.check_trails.assert_called_once()
        # Price dict should have been passed
        prices_arg = mgr.check_trails.call_args[0][0]
        assert "AAPL" in prices_arg
        assert "TSLA" in prices_arg


# ── Tick — fills ──────────────────────────────────────────────────────────

class TestTickFills:
    def test_tick_detects_fills(self):
        from tradingbot.tracking.execution_tracker import ExecutionTracker
        mgr = _make_mock_mgr(open_symbols=["AAPL"])
        mgr.check_fills.return_value = [
            {"symbol": "AAPL", "outcome": "tp1_hit", "exit_price": 105.0, "pnl_pct": 3.0}
        ]
        tracker = ExecutionTracker(mgr)
        result = tracker.tick()
        assert result["fills"] == 1

    @patch("tradingbot.tracking.execution_tracker.ExecutionTracker._record_fill_to_supabase")
    def test_fill_records_to_supabase(self, mock_record):
        from tradingbot.tracking.execution_tracker import ExecutionTracker
        mgr = _make_mock_mgr(open_symbols=["AAPL"])
        fill = {"symbol": "AAPL", "outcome": "stopped", "exit_price": 97.0, "pnl_pct": -3.0}
        mgr.check_fills.return_value = [fill]
        tracker = ExecutionTracker(mgr)
        tracker.tick()
        mock_record.assert_called_once_with(fill)


# ── Tick — morning deadline ───────────────────────────────────────────────

class TestMorningDeadline:
    def _make_time(self, hour, minute):
        return datetime(2025, 6, 15, hour, minute, tzinfo=ET)

    def test_morning_deadline_fires_after_1030(self):
        from tradingbot.tracking.execution_tracker import ExecutionTracker
        mgr = _make_mock_mgr(open_symbols=["AAPL"])
        mgr.morning_deadline.return_value = ["AAPL: sold (losing)"]
        tracker = ExecutionTracker(mgr)

        with patch("tradingbot.tracking.execution_tracker.datetime") as mock_dt:
            mock_dt.now.return_value = self._make_time(10, 35)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = tracker.tick()

        assert result["morning_deadline"] is True
        mgr.morning_deadline.assert_called_once()

    def test_morning_deadline_does_not_fire_before_1030(self):
        from tradingbot.tracking.execution_tracker import ExecutionTracker
        mgr = _make_mock_mgr(open_symbols=["AAPL"])
        tracker = ExecutionTracker(mgr)

        with patch("tradingbot.tracking.execution_tracker.datetime") as mock_dt:
            mock_dt.now.return_value = self._make_time(10, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = tracker.tick()

        assert result["morning_deadline"] is False
        mgr.morning_deadline.assert_not_called()

    def test_morning_deadline_only_fires_once(self):
        from tradingbot.tracking.execution_tracker import ExecutionTracker
        mgr = _make_mock_mgr(open_symbols=["AAPL"])
        mgr.morning_deadline.return_value = []
        tracker = ExecutionTracker(mgr)

        with patch("tradingbot.tracking.execution_tracker.datetime") as mock_dt:
            mock_dt.now.return_value = self._make_time(10, 35)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            tracker.tick()
            tracker.tick()  # Second tick

        # Should only call morning_deadline once
        assert mgr.morning_deadline.call_count == 1


# ── Tick — EOD expire ─────────────────────────────────────────────────────

class TestEODExpire:
    def _make_time(self, hour, minute):
        return datetime(2025, 6, 15, hour, minute, tzinfo=ET)

    def test_expire_fires_after_1530(self):
        from tradingbot.tracking.execution_tracker import ExecutionTracker
        mgr = _make_mock_mgr(open_symbols=["AAPL"])
        mgr.expire_all.return_value = ["AAPL: expired"]
        tracker = ExecutionTracker(mgr)

        with patch("tradingbot.tracking.execution_tracker.datetime") as mock_dt:
            mock_dt.now.return_value = self._make_time(15, 35)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = tracker.tick()

        assert result["expired"] is True
        mgr.expire_all.assert_called_once()

    def test_expire_does_not_fire_before_1530(self):
        from tradingbot.tracking.execution_tracker import ExecutionTracker
        mgr = _make_mock_mgr(open_symbols=["AAPL"])
        tracker = ExecutionTracker(mgr)

        with patch("tradingbot.tracking.execution_tracker.datetime") as mock_dt:
            mock_dt.now.return_value = self._make_time(14, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = tracker.tick()

        assert result["expired"] is False
        mgr.expire_all.assert_not_called()

    def test_expire_only_fires_once(self):
        from tradingbot.tracking.execution_tracker import ExecutionTracker
        mgr = _make_mock_mgr(open_symbols=["AAPL"])
        mgr.expire_all.return_value = []
        tracker = ExecutionTracker(mgr)

        with patch("tradingbot.tracking.execution_tracker.datetime") as mock_dt:
            mock_dt.now.return_value = self._make_time(15, 35)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            tracker.tick()
            tracker.tick()

        assert mgr.expire_all.call_count == 1


# ── Reconciliation ────────────────────────────────────────────────────────

class TestReconcile:
    def test_reconcile_called_every_tick(self):
        from tradingbot.tracking.execution_tracker import ExecutionTracker
        mgr = _make_mock_mgr(open_symbols=["AAPL"])
        tracker = ExecutionTracker(mgr)
        tracker.tick()
        mgr.reconcile.assert_called_once()

    def test_reconcile_result_reported(self):
        from tradingbot.tracking.execution_tracker import ExecutionTracker
        mgr = _make_mock_mgr(open_symbols=["AAPL"])
        recon = MagicMock()
        recon.actions_taken = ["Closed orphan AAPL"]
        mgr.reconcile.return_value = recon
        tracker = ExecutionTracker(mgr)
        result = tracker.tick()
        assert result["reconciled"] is True


# ── Daily reset ───────────────────────────────────────────────────────────

class TestDailyReset:
    def test_reset_clears_flags(self):
        from tradingbot.tracking.execution_tracker import ExecutionTracker
        mgr = _make_mock_mgr(open_symbols=["AAPL"])
        mgr.morning_deadline.return_value = []
        mgr.expire_all.return_value = []
        tracker = ExecutionTracker(mgr)

        # Simulate both flags being set
        tracker._morning_deadline_done = True
        tracker._expire_done = True

        tracker.reset_daily()

        assert tracker._morning_deadline_done is False
        assert tracker._expire_done is False
        mgr.reset_daily.assert_called_once()


# ── Kill switch ───────────────────────────────────────────────────────────

class TestKillAll:
    def test_kill_all_delegates_to_manager(self):
        from tradingbot.tracking.execution_tracker import ExecutionTracker
        mgr = _make_mock_mgr(open_symbols=["AAPL", "TSLA"])
        mgr.kill_all.return_value = ["Sold AAPL", "Sold TSLA"]
        tracker = ExecutionTracker(mgr)
        actions = tracker.kill_all()
        assert len(actions) == 2
        mgr.kill_all.assert_called_once()


# ── Status ────────────────────────────────────────────────────────────────

class TestStatus:
    def test_get_status_delegates(self):
        from tradingbot.tracking.execution_tracker import ExecutionTracker
        mgr = _make_mock_mgr()
        tracker = ExecutionTracker(mgr)
        status = tracker.get_status()
        assert status["mode"] == "paper"
        mgr.get_status.assert_called_once()


# ── Notification ──────────────────────────────────────────────────────────

class TestNotification:
    def test_notify_sends_via_notifier(self):
        from tradingbot.tracking.execution_tracker import ExecutionTracker
        mgr = _make_mock_mgr(open_symbols=["AAPL"])
        mgr.check_trails.return_value = ["AAPL: BE trail"]
        tracker = ExecutionTracker(mgr)
        mock_notifier = MagicMock()
        tracker._notifier = mock_notifier
        tracker.tick()
        mock_notifier.send_message.assert_called()

    def test_notify_swallows_exceptions(self):
        from tradingbot.tracking.execution_tracker import ExecutionTracker
        mgr = _make_mock_mgr(open_symbols=["AAPL"])
        mgr.check_trails.return_value = ["AAPL: BE trail"]
        tracker = ExecutionTracker(mgr)
        mock_notifier = MagicMock()
        mock_notifier.send_message.side_effect = Exception("network error")
        tracker._notifier = mock_notifier
        # Should not raise
        result = tracker.tick()
        assert result["trails"] == 1

    def test_notify_noop_when_no_notifier(self):
        from tradingbot.tracking.execution_tracker import ExecutionTracker
        mgr = _make_mock_mgr(open_symbols=["AAPL"])
        mgr.check_trails.return_value = ["AAPL: trail"]
        tracker = ExecutionTracker(mgr)
        tracker._notifier = None
        # Should not raise
        result = tracker.tick()
        assert result["trails"] == 1


# ── Supabase fill sync ───────────────────────────────────────────────────

class TestFillSync:
    @patch("tradingbot.web.alert_store.update_outcome_by_symbol")
    def test_record_fill_calls_update_outcome_by_symbol(self, mock_update):
        from tradingbot.tracking.execution_tracker import ExecutionTracker
        mgr = _make_mock_mgr()
        tracker = ExecutionTracker(mgr)
        fill = {
            "symbol": "AAPL",
            "outcome": "tp1_hit",
            "exit_price": 105.0,
            "pnl_pct": 3.0,
        }
        tracker._record_fill_to_supabase(fill)
        mock_update.assert_called_once()
        call_kw = mock_update.call_args[1]
        assert call_kw["symbol"] == "AAPL"
        assert call_kw["status"] == "tp1_hit"
        assert call_kw["exit_price"] == 105.0
        assert call_kw["pnl_pct"] == 3.0
        assert call_kw["closed_at"]  # should be a non-empty timestamp

    @patch("tradingbot.web.alert_store.update_outcome_by_symbol")
    def test_record_fill_maps_stopped_status(self, mock_update):
        from tradingbot.tracking.execution_tracker import ExecutionTracker
        mgr = _make_mock_mgr()
        tracker = ExecutionTracker(mgr)
        fill = {"symbol": "TSLA", "outcome": "stopped", "exit_price": 97.0, "pnl_pct": -3.0}
        tracker._record_fill_to_supabase(fill)
        mock_update.assert_called_once()
        call_kw = mock_update.call_args[1]
        assert call_kw["symbol"] == "TSLA"
        assert call_kw["status"] == "stopped"
        assert call_kw["exit_price"] == 97.0
        assert call_kw["pnl_pct"] == -3.0
        assert call_kw["closed_at"]  # should be a non-empty timestamp

    def test_record_fill_swallows_import_error(self):
        """If Supabase isn't configured, don't crash."""
        from tradingbot.tracking.execution_tracker import ExecutionTracker
        mgr = _make_mock_mgr()
        tracker = ExecutionTracker(mgr)
        fill = {"symbol": "AAPL", "outcome": "tp1_hit", "exit_price": 105.0, "pnl_pct": 3.0}
        # Patch the inner import to simulate missing Supabase
        with patch(
            "tradingbot.web.alert_store.update_outcome_by_symbol",
            side_effect=Exception("no supabase"),
        ):
            # _record_fill_to_supabase should catch the exception
            tracker._record_fill_to_supabase(fill)
            # No exception raised = success


# ── Price fetch failure ───────────────────────────────────────────────────

class TestPriceFetchFailure:
    def test_tick_continues_on_price_fetch_error(self):
        from tradingbot.tracking.execution_tracker import ExecutionTracker
        mgr = _make_mock_mgr(open_symbols=["AAPL"])
        mgr._client.get_latest_prices.side_effect = Exception("connection lost")
        tracker = ExecutionTracker(mgr)
        result = tracker.tick()
        # Should not crash, trails should be 0 (no prices)
        assert result["trails"] == 0
        mgr.check_trails.assert_not_called()


# ── update_outcome_by_symbol ──────────────────────────────────────────────

class TestUpdateOutcomeBySymbol:
    @patch("tradingbot.web.alert_store._get_supabase")
    @patch("tradingbot.web.alert_store._today_et")
    def test_finds_and_updates_outcome(self, mock_today, mock_sb_fn):
        from tradingbot.web.alert_store import update_outcome_by_symbol
        from datetime import date as dt_date

        mock_today.return_value = dt_date(2025, 6, 15)
        mock_sb = MagicMock()
        mock_sb_fn.return_value = mock_sb

        # Chain: .table().select().eq().eq().in_().limit().execute()
        mock_resp = MagicMock()
        mock_resp.data = [{"id": 42}]
        mock_sb.table.return_value.select.return_value.eq.return_value.eq.return_value.in_.return_value.limit.return_value.execute.return_value = mock_resp

        with patch("tradingbot.web.alert_store.update_outcome") as mock_update:
            ok = update_outcome_by_symbol("AAPL", "tp1_hit", exit_price=105.0, pnl_pct=3.0)

        assert ok is True
        mock_update.assert_called_once_with(
            outcome_id=42,
            status="tp1_hit",
            exit_price=105.0,
            pnl_pct=3.0,
            hit_at=None,
            closed_at=None,
        )

    @patch("tradingbot.web.alert_store._get_supabase")
    @patch("tradingbot.web.alert_store._today_et")
    def test_returns_false_when_no_open_outcome(self, mock_today, mock_sb_fn):
        from tradingbot.web.alert_store import update_outcome_by_symbol
        from datetime import date as dt_date

        mock_today.return_value = dt_date(2025, 6, 15)
        mock_sb = MagicMock()
        mock_sb_fn.return_value = mock_sb

        mock_resp = MagicMock()
        mock_resp.data = []  # No matching rows
        mock_sb.table.return_value.select.return_value.eq.return_value.eq.return_value.in_.return_value.limit.return_value.execute.return_value = mock_resp

        ok = update_outcome_by_symbol("AAPL", "stopped", exit_price=97.0, pnl_pct=-3.0)
        assert ok is False

    @patch("tradingbot.web.alert_store._get_supabase")
    def test_returns_false_when_no_supabase(self, mock_sb_fn):
        from tradingbot.web.alert_store import update_outcome_by_symbol
        mock_sb_fn.return_value = None
        ok = update_outcome_by_symbol("AAPL", "stopped")
        assert ok is False


# ── TelegramNotifier.send_message ─────────────────────────────────────────

class TestSendMessage:
    def test_send_message_delegates_to_private(self):
        notifier = MagicMock()
        notifier._enabled = True
        notifier.send_message = MagicMock(return_value=True)
        assert notifier.send_message("Hello") is True

    def test_send_message_skips_when_disabled(self):
        from tradingbot.notifications.telegram_notifier import TelegramNotifier
        # Construct with empty token → disabled
        n = TelegramNotifier(token="", chat_id="")
        assert n.send_message("Hello") is False


# ── CLI helper ────────────────────────────────────────────────────────────

class TestRunExecutionTrackerTick:
    @patch("tradingbot.tracking.execution_tracker.create_execution_tracker")
    @patch("tradingbot.execution.execution_manager.create_execution_manager")
    @patch("tradingbot.data.create_data_client")
    @patch("tradingbot.config.ConfigLoader")
    def test_returns_none_when_alert_only(self, mock_cfg_cls, mock_dc, mock_em, mock_et):
        from tradingbot.cli import _run_execution_tracker_tick
        mock_cfg = MagicMock()
        mock_cfg.broker.return_value = {}
        mock_cfg.risk.return_value = {}
        mock_cfg_cls.return_value = mock_cfg
        mock_em.return_value = None
        mock_et.return_value = None
        result = _run_execution_tracker_tick()
        assert result is None

    @patch("tradingbot.tracking.execution_tracker.create_execution_tracker")
    @patch("tradingbot.execution.execution_manager.create_execution_manager")
    @patch("tradingbot.data.create_data_client")
    @patch("tradingbot.config.ConfigLoader")
    def test_returns_tick_result_when_enabled(self, mock_cfg_cls, mock_dc, mock_em, mock_et):
        from tradingbot.cli import _run_execution_tracker_tick
        mock_cfg = MagicMock()
        mock_cfg.broker.return_value = {}
        mock_cfg.risk.return_value = {}
        mock_cfg_cls.return_value = mock_cfg

        mock_tracker = MagicMock()
        mock_tracker.tick.return_value = {"trails": 1, "fills": 0, "expired": False}
        mock_et.return_value = mock_tracker

        result = _run_execution_tracker_tick()
        assert result == {"trails": 1, "fills": 0, "expired": False}
