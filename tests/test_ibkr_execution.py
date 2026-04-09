"""
Tests for IBKR execution engine modules.

All tests use mocks — no real IB Gateway connection needed.
Covers:
- IBKRClient: connection, contract creation, snapshot building, universe
- CapitalAllocator: slots, PDT, position sizing, pre-trade gate
- OrderExecutor: bracket orders, trailing, morning deadline, expire, kill switch
- PositionMonitor: reconciliation, manual close detection, stale cleanup
"""
from __future__ import annotations

import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch, PropertyMock


# ═══════════════════════════════════════════════════════════════════════
# IBKRClient tests
# ═══════════════════════════════════════════════════════════════════════

class TestIBKRClientConnection:
    """Tests for IBKRClient connection management."""

    @patch("tradingbot.data.ibkr_client.IBKRClient.connect")
    def test_init_defaults(self, mock_connect):
        from tradingbot.data.ibkr_client import IBKRClient
        client = IBKRClient()
        assert client.host == "127.0.0.1"
        assert client.port == 4002
        assert client.client_id == 1
        assert client._ib is None

    @patch("tradingbot.data.ibkr_client.IBKRClient.connect")
    def test_init_custom(self, mock_connect):
        from tradingbot.data.ibkr_client import IBKRClient
        client = IBKRClient(host="10.0.0.1", port=4001, client_id=5, readonly=True)
        assert client.host == "10.0.0.1"
        assert client.port == 4001
        assert client.client_id == 5
        assert client.readonly is True

    def test_is_connected_false_when_none(self):
        from tradingbot.data.ibkr_client import IBKRClient
        client = IBKRClient.__new__(IBKRClient)
        client._ib = None
        assert client.is_connected() is False

    def test_is_connected_true_when_connected(self):
        from tradingbot.data.ibkr_client import IBKRClient
        client = IBKRClient.__new__(IBKRClient)
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = True
        client._ib = mock_ib
        assert client.is_connected() is True

    def test_disconnect_when_connected(self):
        from tradingbot.data.ibkr_client import IBKRClient
        client = IBKRClient.__new__(IBKRClient)
        mock_ib = MagicMock()
        mock_ib.isConnected.return_value = True
        client._ib = mock_ib
        client.disconnect()
        mock_ib.disconnect.assert_called_once()


class TestIBKRClientBarConversion:
    """Tests for bar data conversion."""

    def test_convert_bars_for_analysis(self):
        from tradingbot.data.ibkr_client import IBKRClient
        bars = [
            {"open": 100.0, "high": 105.0, "low": 98.0, "close": 103.0, "volume": 1000},
            {"open": 103.0, "high": 107.0, "low": 101.0, "close": 106.0, "volume": 1200},
        ]
        result = IBKRClient._convert_bars_for_analysis(bars)
        assert len(result) == 2
        assert result[0].open == 100.0
        assert result[0].high == 105.0
        assert result[0].close == 103.0
        assert result[1].volume == 1200

    def test_convert_empty_bars(self):
        from tradingbot.data.ibkr_client import IBKRClient
        result = IBKRClient._convert_bars_for_analysis([])
        assert result == []


class TestIBKRClientCoreWatchlist:
    """Tests for universe building."""

    def test_core_watchlist_has_megacaps(self):
        from tradingbot.data.ibkr_client import IBKRClient
        wl = IBKRClient._CORE_WATCHLIST
        for sym in ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "TSLA"]:
            assert sym in wl, f"{sym} missing from core watchlist"

    def test_core_watchlist_has_etfs(self):
        from tradingbot.data.ibkr_client import IBKRClient
        wl = IBKRClient._CORE_WATCHLIST
        for sym in ["SPY", "QQQ", "IWM"]:
            assert sym in wl

    def test_core_watchlist_no_meme_stocks(self):
        from tradingbot.data.ibkr_client import IBKRClient
        wl = IBKRClient._CORE_WATCHLIST
        meme = ["GME", "AMC", "SOFI", "IONQ", "QUBT", "RGTI", "SOUN", "LUNR"]
        for sym in meme:
            assert sym not in wl, f"Meme stock {sym} in watchlist"

    def test_junk_suffix_regex(self):
        from tradingbot.data.ibkr_client import _JUNK_SUFFIX
        assert _JUNK_SUFFIX.search("ABCD.WS")
        assert _JUNK_SUFFIX.search("XYZR.RT")
        assert _JUNK_SUFFIX.search("TEST.UN")
        assert not _JUNK_SUFFIX.search("AAPL")
        assert not _JUNK_SUFFIX.search("MSFT")


# ═══════════════════════════════════════════════════════════════════════
# CapitalAllocator tests
# ═══════════════════════════════════════════════════════════════════════

class TestCapitalAllocatorAlertMode:
    """In alert_only mode, all checks should pass."""

    def test_alert_mode_has_slot(self):
        from tradingbot.risk.capital_allocator import CapitalAllocator
        alloc = CapitalAllocator(mode="alert_only")
        assert alloc.has_slot("morning") is True
        assert alloc.has_slot("midday") is True

    def test_alert_mode_can_afford(self):
        from tradingbot.risk.capital_allocator import CapitalAllocator
        alloc = CapitalAllocator(mode="alert_only")
        assert alloc.can_afford(100.0, 100) is True

    def test_alert_mode_pdt_ok(self):
        from tradingbot.risk.capital_allocator import CapitalAllocator
        alloc = CapitalAllocator(mode="alert_only")
        assert alloc.pdt_ok() is True

    def test_alert_mode_position_size_zero(self):
        from tradingbot.risk.capital_allocator import CapitalAllocator
        alloc = CapitalAllocator(mode="alert_only")
        assert alloc.calculate_position_size(100, 97) == 0

    def test_alert_mode_pre_trade_check(self):
        from tradingbot.risk.capital_allocator import CapitalAllocator
        alloc = CapitalAllocator(mode="alert_only")
        ok, shares, reason = alloc.pre_trade_check(100, 97, "morning")
        assert ok is True
        assert reason == "alert_only mode"


class TestCapitalAllocatorSlots:
    """Slot management in paper/live mode."""

    def _alloc(self, **kwargs):
        from tradingbot.risk.capital_allocator import CapitalAllocator
        defaults = dict(
            mode="paper",
            max_concurrent_positions=3,
            max_morning_entries=2,
            reserve_midday_slots=1,
        )
        defaults.update(kwargs)
        alloc = CapitalAllocator(**defaults)
        alloc.update_account(100_000, 100_000, 100_000)
        return alloc

    def test_has_slot_empty(self):
        alloc = self._alloc()
        assert alloc.has_slot("morning") is True

    def test_has_slot_full(self):
        alloc = self._alloc()
        for i, sym in enumerate(["AAPL", "MSFT", "NVDA"]):
            alloc.open_position(sym, 100 + i, 10, "09:30", "morning")
        assert alloc.has_slot("morning") is False
        assert alloc.has_slot("midday") is False

    def test_morning_entry_limit(self):
        alloc = self._alloc()
        alloc.open_position("AAPL", 100, 10, "09:30", "morning")
        alloc.open_position("MSFT", 200, 10, "09:35", "morning")
        # 2 morning entries used → morning slot blocked
        assert alloc.has_slot("morning") is False
        # But midday should still have 1 slot
        assert alloc.has_slot("midday") is True

    def test_reserve_midday_slot(self):
        alloc = self._alloc()
        alloc.open_position("AAPL", 100, 10, "09:30", "morning")
        # 1 morning entry, 2 slots remaining, 1 reserved for midday
        # → only 1 morning slot left
        # Fill that second morning slot
        alloc.open_position("MSFT", 200, 10, "09:35", "morning")
        # Now morning is blocked (2/2), midday has 1 reserved slot
        assert alloc.has_slot("morning") is False
        assert alloc.has_slot("midday") is True

    def test_close_position_frees_slot(self):
        alloc = self._alloc()
        for sym in ["AAPL", "MSFT", "NVDA"]:
            alloc.open_position(sym, 100, 10, "09:30", "morning")
        assert alloc.has_slot("midday") is False
        alloc.close_position("AAPL")
        assert alloc.has_slot("midday") is True

    def test_reset_daily(self):
        alloc = self._alloc()
        alloc.open_position("AAPL", 100, 10, "09:30", "morning")
        alloc.open_position("MSFT", 200, 10, "09:35", "morning")
        alloc.reset_daily()
        assert alloc._morning_entries_today == 0


class TestCapitalAllocatorPositionSizing:
    """Position sizing with risk-per-trade and caps."""

    def _alloc(self, account=10_000, **kwargs):
        from tradingbot.risk.capital_allocator import CapitalAllocator
        alloc = CapitalAllocator(mode="paper", **kwargs)
        alloc.update_account(account, account, account)
        return alloc

    def test_basic_sizing(self):
        """$10K account, 0.5% risk = $50 budget. $3 stop distance → 16 shares."""
        alloc = self._alloc(10_000)
        shares = alloc.calculate_position_size(100.0, 97.0)
        # $50 / $3 = 16.67 → 16 shares
        assert shares == 16

    def test_sizing_with_streak_multiplier(self):
        """After 1 loss: 75% multiplier → $37.50 / $3 = 12 shares."""
        alloc = self._alloc(10_000)
        shares = alloc.calculate_position_size(100.0, 97.0, streak_multiplier=0.75)
        assert shares == 12

    def test_sizing_capped_by_max_notional(self):
        """$100K account → $500 risk budget. But max notional is $10K → 100 shares at $100."""
        alloc = self._alloc(100_000)
        # $500 / $3 = 166 shares at $100 = $16,600 → exceeds $10K cap
        shares = alloc.calculate_position_size(100.0, 97.0)
        assert shares <= 100  # $10K / $100 = 100

    def test_sizing_invalid_prices(self):
        alloc = self._alloc(10_000)
        assert alloc.calculate_position_size(0, 97) == 0
        assert alloc.calculate_position_size(100, 100) == 0
        assert alloc.calculate_position_size(97, 100) == 0  # stop above entry

    def test_sizing_small_account(self):
        """$1K account, 0.5% = $5 risk. $2 stop → 2 shares."""
        alloc = self._alloc(1_000)
        shares = alloc.calculate_position_size(50.0, 48.0)
        # $5 / $2 = 2.5 → 2 shares
        assert shares == 2


class TestCapitalAllocatorPDT:
    """PDT (Pattern Day Trader) counter."""

    def _alloc(self, account=10_000):
        from tradingbot.risk.capital_allocator import CapitalAllocator
        alloc = CapitalAllocator(mode="paper", pdt_protection=True, pdt_threshold=25_000)
        alloc.update_account(account, account, account)
        return alloc

    def test_pdt_ok_under_limit(self):
        alloc = self._alloc(10_000)
        assert alloc.pdt_ok() is True
        assert alloc.pdt_trades_remaining == 3

    def test_pdt_blocked_after_3_day_trades(self):
        alloc = self._alloc(10_000)
        for i in range(3):
            alloc.record_day_trade(f"SYM{i}", "09:30", "10:30")
        assert alloc.pdt_ok() is False
        assert alloc.pdt_trades_remaining == 0

    def test_pdt_not_applicable_above_25k(self):
        alloc = self._alloc(30_000)
        for i in range(5):
            alloc.record_day_trade(f"SYM{i}", "09:30", "10:30")
        assert alloc.pdt_ok() is True
        assert alloc.pdt_trades_remaining == 999

    def test_pdt_protection_disabled(self):
        from tradingbot.risk.capital_allocator import CapitalAllocator
        alloc = CapitalAllocator(mode="paper", pdt_protection=False)
        alloc.update_account(5_000, 5_000, 5_000)
        for i in range(5):
            alloc.record_day_trade(f"SYM{i}", "09:30", "10:30")
        assert alloc.pdt_ok() is True

    def test_pdt_old_trades_pruned(self):
        alloc = self._alloc(10_000)
        # Add trades from 10 days ago (should be pruned)
        from tradingbot.risk.capital_allocator import PDTRecord
        old_trade = PDTRecord("OLD", date.today() - timedelta(days=10), "09:30", "10:30")
        alloc._day_trades = [old_trade, old_trade, old_trade]
        assert alloc.pdt_ok() is True  # old trades pruned

    def test_pdt_trades_remaining_after_1(self):
        alloc = self._alloc(10_000)
        alloc.record_day_trade("AAPL", "09:30", "10:30")
        assert alloc.pdt_trades_remaining == 2


class TestCapitalAllocatorPreTradeCheck:
    """Combined pre-trade gate."""

    def _alloc(self, account=10_000):
        from tradingbot.risk.capital_allocator import CapitalAllocator
        alloc = CapitalAllocator(mode="paper")
        alloc.update_account(account, account, account)
        return alloc

    def test_pre_trade_passes(self):
        alloc = self._alloc(10_000)
        ok, shares, reason = alloc.pre_trade_check(100.0, 97.0, "morning")
        assert ok is True
        assert shares > 0
        assert reason == "ok"

    def test_pre_trade_blocked_by_pdt(self):
        alloc = self._alloc(10_000)
        for i in range(3):
            alloc.record_day_trade(f"SYM{i}", "09:30", "10:30")
        ok, shares, reason = alloc.pre_trade_check(100.0, 97.0, "morning")
        assert ok is False
        assert "PDT" in reason

    def test_pre_trade_blocked_by_slots(self):
        alloc = self._alloc(10_000)
        for sym in ["A", "B", "C"]:
            alloc.open_position(sym, 100, 10, "09:30", "midday")
        ok, shares, reason = alloc.pre_trade_check(100.0, 97.0, "midday")
        assert ok is False
        assert "slot" in reason.lower()

    def test_pre_trade_blocked_by_capital(self):
        """Account with $0 buying power should fail."""
        from tradingbot.risk.capital_allocator import CapitalAllocator
        alloc = CapitalAllocator(mode="paper")
        alloc.update_account(100, 0, 0)  # $100 NLV but $0 buying power
        ok, shares, reason = alloc.pre_trade_check(100.0, 97.0, "morning")
        assert ok is False


# ═══════════════════════════════════════════════════════════════════════
# OrderExecutor tests
# ═══════════════════════════════════════════════════════════════════════

def _mock_ibkr_client():
    """Create a mock IBKRClient for OrderExecutor tests."""
    client = MagicMock()
    client.ib = MagicMock()
    client.ib.isConnected.return_value = True
    client.ib.sleep = MagicMock()  # no real sleep
    return client


class TestOrderExecutorInit:
    def test_init(self):
        from tradingbot.execution.order_executor import OrderExecutor
        client = _mock_ibkr_client()
        executor = OrderExecutor(client)
        assert executor.managed_trades == {}
        assert executor.ib == client.ib


class TestManagedTrade:
    def test_defaults(self):
        from tradingbot.execution.order_executor import ManagedTrade
        t = ManagedTrade(
            symbol="AAPL", entry_price=185.0, stop_price=183.0,
            tp1_price=187.0, tp2_price=190.0, quantity=100,
            parent_order_id=1, tp_order_id=2, stop_order_id=3,
            oca_group="test", session="morning", entry_time="2026-04-09T09:30:00"
        )
        assert t.trail_stage == 0
        assert t.tp1_hit is False
        assert t.is_scalp is False
        assert t.closed is False
        assert t.current_stop == 0.0


class TestTrailingLogic:
    """Test check_and_trail without real IB connection."""

    def _setup_executor_with_trade(self, entry=100, stop=97, tp1=106):
        from tradingbot.execution.order_executor import OrderExecutor, ManagedTrade
        client = _mock_ibkr_client()
        executor = OrderExecutor(client)

        # Mock modify_stop to succeed
        from tradingbot.execution.order_executor import OrderModifyResult
        executor.modify_stop = MagicMock(
            return_value=OrderModifyResult(success=True, order_id=3, new_price=0)
        )

        trade = ManagedTrade(
            symbol="TEST", entry_price=entry, stop_price=stop,
            tp1_price=tp1, tp2_price=tp1 + 3, quantity=50,
            parent_order_id=1, tp_order_id=2, stop_order_id=3,
            oca_group="test", session="morning", entry_time="09:30",
            current_stop=stop,
        )
        executor._managed_trades["TEST"] = trade
        return executor, trade

    def test_no_trail_below_1r(self):
        executor, trade = self._setup_executor_with_trade(100, 97)
        # Price at 101 → less than 1R (3) gain → no trail
        result = executor.check_and_trail("TEST", 101.0)
        assert result is None
        assert trade.trail_stage == 0

    def test_trail_to_breakeven_at_1r(self):
        executor, trade = self._setup_executor_with_trade(100, 97)
        # Price at 103+ → 1R gain → stop to breakeven
        result = executor.check_and_trail("TEST", 103.5)
        assert result is not None
        assert "breakeven" in result.lower()
        assert trade.trail_stage == 1

    def test_trail_to_plus1r_at_2r(self):
        executor, trade = self._setup_executor_with_trade(100, 97)
        trade.trail_stage = 1  # already at BE
        # Price at 106+ → 2R gain → stop to entry + 1R ($103)
        result = executor.check_and_trail("TEST", 107.0)
        assert result is not None
        assert "+1R" in result
        assert trade.trail_stage == 2

    def test_trail_to_tp1_after_tp1_hit(self):
        executor, trade = self._setup_executor_with_trade(100, 97, 106)
        trade.trail_stage = 2
        trade.tp1_hit = True
        # TP1 hit → lock stop at TP1
        result = executor.check_and_trail("TEST", 108.0)
        assert result is not None
        assert "TP1" in result
        assert trade.trail_stage == 3

    def test_no_trail_for_scalp(self):
        executor, trade = self._setup_executor_with_trade(100, 97)
        trade.is_scalp = True
        result = executor.check_and_trail("TEST", 110.0)
        assert result is None

    def test_no_trail_for_closed(self):
        executor, trade = self._setup_executor_with_trade(100, 97)
        trade.closed = True
        result = executor.check_and_trail("TEST", 110.0)
        assert result is None

    def test_no_trail_nonexistent_symbol(self):
        from tradingbot.execution.order_executor import OrderExecutor
        executor = OrderExecutor(_mock_ibkr_client())
        result = executor.check_and_trail("NOPE", 100.0)
        assert result is None


class TestMorningDeadline:
    """Morning deadline forces exit of losers at 10:30."""

    def _setup(self):
        from tradingbot.execution.order_executor import OrderExecutor, ManagedTrade
        client = _mock_ibkr_client()
        executor = OrderExecutor(client)
        executor._market_sell = MagicMock(return_value="Market sold 50 shares @ $99.00 (morning_deadline)")
        executor.modify_stop = MagicMock(
            return_value=MagicMock(success=True)
        )

        # Morning trade: winning
        t1 = ManagedTrade(
            symbol="WIN", entry_price=100, stop_price=97,
            tp1_price=106, tp2_price=109, quantity=50,
            parent_order_id=1, tp_order_id=2, stop_order_id=3,
            oca_group="g1", session="morning", entry_time="09:30",
        )
        # Morning trade: losing
        t2 = ManagedTrade(
            symbol="LOSE", entry_price=100, stop_price=97,
            tp1_price=106, tp2_price=109, quantity=50,
            parent_order_id=4, tp_order_id=5, stop_order_id=6,
            oca_group="g2", session="morning", entry_time="09:35",
        )
        # Midday trade: should be ignored
        t3 = ManagedTrade(
            symbol="MID", entry_price=100, stop_price=97,
            tp1_price=106, tp2_price=109, quantity=50,
            parent_order_id=7, tp_order_id=8, stop_order_id=9,
            oca_group="g3", session="midday", entry_time="11:00",
        )
        executor._managed_trades = {"WIN": t1, "LOSE": t2, "MID": t3}
        return executor

    def test_winners_trailed_losers_sold(self):
        executor = self._setup()
        prices = {"WIN": 102.0, "LOSE": 98.0, "MID": 95.0}
        actions = executor.morning_deadline_check(prices)
        # WIN is winning (+2%) → trail to BE
        assert any("WIN" in a and "trailed" in a.lower() for a in actions)
        # LOSE is losing → market sell
        assert any("LOSE" in a for a in actions)
        # MID is midday → not in actions
        assert not any("MID" in a for a in actions)

    def test_flat_trade_sold(self):
        executor = self._setup()
        prices = {"WIN": 100.05, "LOSE": 100.05}  # both flat ≤ +0.1%
        actions = executor.morning_deadline_check(prices)
        # Both flat → sold (threshold is entry * 1.001)
        # 100.05 < 100.1 so both are "flat" and sold
        assert len(actions) >= 2


class TestExpireAll:
    """3:30 PM expire — all positions closed."""

    def test_expire_closes_all_open(self):
        from tradingbot.execution.order_executor import OrderExecutor, ManagedTrade
        client = _mock_ibkr_client()
        executor = OrderExecutor(client)
        executor._market_sell = MagicMock(return_value="Market sold (expire)")

        for sym in ["A", "B"]:
            executor._managed_trades[sym] = ManagedTrade(
                symbol=sym, entry_price=100, stop_price=97,
                tp1_price=106, tp2_price=109, quantity=50,
                parent_order_id=1, tp_order_id=2, stop_order_id=3,
                oca_group="g", session="morning", entry_time="09:30",
            )
        # Already closed trade — should be skipped
        closed = ManagedTrade(
            symbol="C", entry_price=100, stop_price=97,
            tp1_price=106, tp2_price=109, quantity=50,
            parent_order_id=4, tp_order_id=5, stop_order_id=6,
            oca_group="g2", session="morning", entry_time="09:30",
        )
        closed.closed = True
        executor._managed_trades["C"] = closed

        actions = executor.expire_all()
        assert len(actions) == 2  # A and B, not C
        assert executor._market_sell.call_count == 2


class TestKillAll:
    """Kill switch — cancel everything and flatten."""

    def test_kill_cancels_and_sells(self):
        from tradingbot.execution.order_executor import OrderExecutor, ManagedTrade
        client = _mock_ibkr_client()
        executor = OrderExecutor(client)
        executor._market_sell = MagicMock(return_value="Killed")

        executor._managed_trades["AAPL"] = ManagedTrade(
            symbol="AAPL", entry_price=185, stop_price=183,
            tp1_price=188, tp2_price=190, quantity=100,
            parent_order_id=1, tp_order_id=2, stop_order_id=3,
            oca_group="g", session="morning", entry_time="09:30",
        )

        actions = executor.kill_all()
        client.ib.reqGlobalCancel.assert_called_once()
        assert any("AAPL" in a for a in actions)


class TestOrderExecutorStatus:
    """get_status for Telegram /status command."""

    def test_status_open_and_closed(self):
        from tradingbot.execution.order_executor import OrderExecutor, ManagedTrade
        executor = OrderExecutor(_mock_ibkr_client())

        t1 = ManagedTrade(
            symbol="OPEN", entry_price=100, stop_price=97,
            tp1_price=106, tp2_price=109, quantity=50,
            parent_order_id=1, tp_order_id=2, stop_order_id=3,
            oca_group="g1", session="morning", entry_time="09:30",
        )
        t2 = ManagedTrade(
            symbol="CLOSED", entry_price=100, stop_price=97,
            tp1_price=106, tp2_price=109, quantity=50,
            parent_order_id=4, tp_order_id=5, stop_order_id=6,
            oca_group="g2", session="morning", entry_time="09:35",
        )
        t2.closed = True
        t2.actual_exit_price = 104.0
        t2.close_reason = "tp1_hit"

        executor._managed_trades = {"OPEN": t1, "CLOSED": t2}
        status = executor.get_status()
        assert status["open_count"] == 1
        assert status["closed_count"] == 1
        assert status["open_trades"][0]["symbol"] == "OPEN"
        assert status["closed_today"][0]["symbol"] == "CLOSED"


# ═══════════════════════════════════════════════════════════════════════
# PositionMonitor tests
# ═══════════════════════════════════════════════════════════════════════

class TestPositionMonitorReconciliation:
    """Test reconciliation without real IB Gateway."""

    def _setup(self):
        from tradingbot.execution.order_executor import OrderExecutor, ManagedTrade
        from tradingbot.execution.position_monitor import PositionMonitor
        from tradingbot.risk.capital_allocator import CapitalAllocator

        client = _mock_ibkr_client()
        allocator = CapitalAllocator(mode="paper")
        allocator.update_account(100_000, 100_000, 100_000)
        executor = OrderExecutor(client)
        monitor = PositionMonitor(client, executor, allocator)

        return client, allocator, executor, monitor

    def test_reconcile_matched(self):
        client, allocator, executor, monitor = self._setup()

        # Set up managed trade
        from tradingbot.execution.order_executor import ManagedTrade
        executor._managed_trades["AAPL"] = ManagedTrade(
            symbol="AAPL", entry_price=185, stop_price=183,
            tp1_price=188, tp2_price=190, quantity=100,
            parent_order_id=1, tp_order_id=2, stop_order_id=3,
            oca_group="g", session="morning", entry_time="09:30",
        )
        allocator.open_position("AAPL", 185, 100, "09:30", "morning")

        # Mock IBKR returns matching position
        client.get_positions.return_value = [{"symbol": "AAPL", "quantity": 100, "avg_cost": 185.0}]
        client.get_open_orders.return_value = [
            {"order_id": 2, "symbol": "AAPL", "action": "SELL"},
            {"order_id": 3, "symbol": "AAPL", "action": "SELL"},
        ]
        client.get_account_summary.return_value = {
            "net_liquidation": 100_000,
            "buying_power": 100_000,
            "cash_balance": 100_000,
        }

        result = monitor.reconcile()
        assert result.matched == 1
        assert result.manual_closes_detected == 0
        assert result.quantity_mismatches == 0

    def test_reconcile_manual_close_detected(self):
        client, allocator, executor, monitor = self._setup()

        from tradingbot.execution.order_executor import ManagedTrade
        executor._managed_trades["AAPL"] = ManagedTrade(
            symbol="AAPL", entry_price=185, stop_price=183,
            tp1_price=188, tp2_price=190, quantity=100,
            parent_order_id=1, tp_order_id=2, stop_order_id=3,
            oca_group="g", session="morning", entry_time="09:30",
        )
        allocator.open_position("AAPL", 185, 100, "09:30", "morning")

        # IBKR shows NO positions (user manually sold in TWS)
        client.get_positions.return_value = []
        client.get_open_orders.return_value = []
        client.get_account_summary.return_value = {
            "net_liquidation": 100_000,
            "buying_power": 100_000,
            "cash_balance": 100_000,
        }

        result = monitor.reconcile()
        assert result.manual_closes_detected == 1
        assert executor._managed_trades["AAPL"].closed is True
        assert executor._managed_trades["AAPL"].close_reason == "manual_close_detected"

    def test_reconcile_quantity_mismatch(self):
        client, allocator, executor, monitor = self._setup()

        from tradingbot.execution.order_executor import ManagedTrade
        executor._managed_trades["AAPL"] = ManagedTrade(
            symbol="AAPL", entry_price=185, stop_price=183,
            tp1_price=188, tp2_price=190, quantity=100,
            parent_order_id=1, tp_order_id=2, stop_order_id=3,
            oca_group="g", session="morning", entry_time="09:30",
        )

        # IBKR shows 50 shares (partial fill or manual partial close)
        client.get_positions.return_value = [{"symbol": "AAPL", "quantity": 50, "avg_cost": 185.0}]
        client.get_open_orders.return_value = []
        client.get_account_summary.return_value = {
            "net_liquidation": 100_000, "buying_power": 100_000, "cash_balance": 100_000,
        }

        result = monitor.reconcile()
        assert result.quantity_mismatches == 1
        assert executor._managed_trades["AAPL"].quantity == 50  # updated to match IBKR

    def test_reconcile_orphaned_position(self):
        client, allocator, executor, monitor = self._setup()

        # No managed trades, but IBKR shows a position (manual buy)
        client.get_positions.return_value = [{"symbol": "TSLA", "quantity": 25, "avg_cost": 250.0}]
        client.get_open_orders.return_value = []
        client.get_account_summary.return_value = {
            "net_liquidation": 100_000, "buying_power": 100_000, "cash_balance": 100_000,
        }

        result = monitor.reconcile()
        assert result.orphaned_ibkr_positions == 1

    def test_reconcile_stale_trade(self):
        """Trade with no position AND no orders → detected as manual close
        (position check runs first in reconciliation loop)."""
        client, allocator, executor, monitor = self._setup()

        from tradingbot.execution.order_executor import ManagedTrade
        executor._managed_trades["GONE"] = ManagedTrade(
            symbol="GONE", entry_price=50, stop_price=48,
            tp1_price=53, tp2_price=55, quantity=100,
            parent_order_id=10, tp_order_id=11, stop_order_id=12,
            oca_group="g", session="morning", entry_time="09:30",
        )

        # IBKR shows no position and no orders for GONE
        client.get_positions.return_value = []
        client.get_open_orders.return_value = []
        client.get_account_summary.return_value = {
            "net_liquidation": 100_000, "buying_power": 100_000, "cash_balance": 100_000,
        }

        result = monitor.reconcile()
        # Position gone → caught by manual_close check first
        assert result.manual_closes_detected == 1
        assert executor._managed_trades["GONE"].closed is True
        assert executor._managed_trades["GONE"].close_reason == "manual_close_detected"

    def test_reconcile_error_handling(self):
        client, allocator, executor, monitor = self._setup()
        client.get_positions.side_effect = ConnectionError("Gateway offline")

        result = monitor.reconcile()
        assert len(result.actions_taken) == 1
        assert "ERROR" in result.actions_taken[0]


class TestPositionMonitorHealth:
    """Health status endpoint."""

    def test_health_status(self):
        from tradingbot.execution.order_executor import OrderExecutor, ManagedTrade
        from tradingbot.execution.position_monitor import PositionMonitor
        from tradingbot.risk.capital_allocator import CapitalAllocator

        client = _mock_ibkr_client()
        allocator = CapitalAllocator(mode="paper")
        allocator.update_account(10_000, 10_000, 10_000)  # under $25K for PDT
        executor = OrderExecutor(client)
        monitor = PositionMonitor(client, executor, allocator)

        health = monitor.get_health_status()
        assert health["managed_open"] == 0
        assert health["account_value"] == 10_000
        assert health["pdt_remaining"] == 3


# ═══════════════════════════════════════════════════════════════════════
# BracketOrderResult tests
# ═══════════════════════════════════════════════════════════════════════

class TestBracketOrderResult:
    def test_success(self):
        from tradingbot.execution.order_executor import BracketOrderResult
        r = BracketOrderResult(success=True, symbol="AAPL", parent_order_id=1)
        assert r.success is True
        assert r.error == ""

    def test_failure(self):
        from tradingbot.execution.order_executor import BracketOrderResult
        r = BracketOrderResult(success=False, symbol="AAPL", error="Contract not found")
        assert r.success is False
        assert "Contract" in r.error


# ═══════════════════════════════════════════════════════════════════════
# Config integration tests
# ═══════════════════════════════════════════════════════════════════════

class TestExecutionConfig:
    """Verify execution config in risk.yaml."""

    def test_execution_config_exists(self):
        import yaml
        with open("config/risk.yaml") as f:
            cfg = yaml.safe_load(f)
        assert "execution" in cfg
        exec_cfg = cfg["execution"]
        assert exec_cfg["mode"] == "alert_only"
        assert exec_cfg["max_concurrent_positions"] == 3
        assert exec_cfg["max_morning_entries"] == 2
        assert exec_cfg["pdt_protection"] is True
        assert exec_cfg["kill_switch_enabled"] is True

    def test_execution_mode_alert_only_by_default(self):
        import yaml
        with open("config/risk.yaml") as f:
            cfg = yaml.safe_load(f)
        assert cfg["execution"]["mode"] == "alert_only"

    def test_broker_ibkr_config(self):
        import yaml
        with open("config/broker.yaml") as f:
            cfg = yaml.safe_load(f)
        assert "ibkr" in cfg
        assert cfg["ibkr"]["port"] == 4002  # paper by default
        assert cfg["ibkr"]["host"] == "127.0.0.1"


class TestExecutionPlanStatus:
    """Verify the plan doc steps are updated."""

    def test_plan_steps_1_2_done(self):
        with open("docs/EXECUTION_ENGINE_PLAN.md", encoding="utf-8") as f:
            content = f.read()
        assert "Done" in content and "approved" in content  # Step 1
        assert "DUP749086" in content                        # Paper account ID
