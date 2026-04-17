"""Tests for the portfolio circuit breaker in TradeTracker."""
from unittest.mock import patch, MagicMock
import pytest

from tradingbot.tracking.trade_tracker import (
    TradeTracker,
    PORTFOLIO_DRAWDOWN_PCT,
    MARKET_CRASH_PCT,
    CORRELATED_RED_RATIO,
)


def _make_trade(symbol, entry, stop=None, tp1=None, tp2=None, status="open", position_size=100):
    """Helper: create a trade dict mimicking a Supabase outcome row."""
    return {
        "id": hash(symbol) % 10000,
        "symbol": symbol,
        "entry_price": entry,
        "stop_price": stop or entry * 0.975,
        "tp1_price": tp1 or entry * 1.03,
        "tp2_price": tp2 or entry * 1.05,
        "status": status,
        "side": "long",
        "position_size": position_size,
    }


class TestCircuitBreakerTriggers:
    """Unit tests for _check_circuit_breaker logic."""

    def setup_method(self):
        self.tracker = TradeTracker()

    def test_no_trigger_when_all_green(self):
        """No circuit breaker when all trades are profitable."""
        trades = [
            _make_trade("AAPL", 150.0),
            _make_trade("MSFT", 400.0),
            _make_trade("GOOG", 170.0),
        ]
        prices = {"AAPL": 152.0, "MSFT": 405.0, "GOOG": 172.0}

        with patch("tradingbot.analysis.market_guard.MarketGuard") as MockGuard:
            mock_health = MagicMock()
            mock_health.spy_change_pct = -0.1
            mock_health.qqq_change_pct = -0.2
            MockGuard.return_value.check.return_value = mock_health

            trigger = self.tracker._check_circuit_breaker(trades, prices)
            assert trigger is None

    def test_no_trigger_single_trade(self):
        """Circuit breaker requires at least 2 open trades."""
        trades = [_make_trade("AAPL", 150.0)]
        prices = {"AAPL": 140.0}  # big loss but single trade
        trigger = self.tracker._check_circuit_breaker(trades, prices)
        assert trigger is None

    def test_portfolio_drawdown_triggers(self):
        """Fire when combined unrealised loss exceeds account threshold."""
        # 3 trades each losing ~2% on 100 shares — substantial loss
        trades = [
            _make_trade("AAPL", 150.0, position_size=50),
            _make_trade("MSFT", 400.0, position_size=20),
            _make_trade("GOOG", 170.0, position_size=40),
        ]
        # All down significantly: AAPL -5%, MSFT -5%, GOOG -5%
        prices = {"AAPL": 142.5, "MSFT": 380.0, "GOOG": 161.5}
        # Losses: AAPL = 50*(142.5-150) = -375, MSFT = 20*(380-400) = -400, GOOG = 40*(161.5-170) = -340
        # Total = -1115, account = 25000 → -4.5% → triggers (-1.5% threshold)

        with patch("tradingbot.analysis.market_guard.MarketGuard") as MockGuard:
            mock_health = MagicMock()
            mock_health.spy_change_pct = 0.0
            mock_health.qqq_change_pct = 0.0
            MockGuard.return_value.check.return_value = mock_health

            trigger = self.tracker._check_circuit_breaker(trades, prices)
            assert trigger is not None
            assert "portfolio_drawdown" in trigger

    def test_market_crash_triggers(self):
        """Fire when SPY/QQQ drops beyond crash threshold."""
        trades = [
            _make_trade("AAPL", 150.0),
            _make_trade("MSFT", 400.0),
        ]
        # Trades are flat (no portfolio drawdown trigger)
        prices = {"AAPL": 150.0, "MSFT": 400.0}

        with patch("tradingbot.analysis.market_guard.MarketGuard") as MockGuard:
            mock_health = MagicMock()
            mock_health.spy_change_pct = -2.5  # SPY crash
            mock_health.qqq_change_pct = -3.0  # QQQ crash
            MockGuard.return_value.check.return_value = mock_health

            trigger = self.tracker._check_circuit_breaker(trades, prices)
            assert trigger is not None
            assert "market_crash" in trigger

    def test_correlated_red_triggers(self):
        """Fire when 75%+ of trades are losing."""
        trades = [
            _make_trade("A", 100.0, position_size=10),
            _make_trade("B", 100.0, position_size=10),
            _make_trade("C", 100.0, position_size=10),
            _make_trade("D", 100.0, position_size=10),
        ]
        # 3 out of 4 losing (75%) — hits threshold
        # Keep losses small enough to NOT trigger portfolio drawdown
        prices = {"A": 99.5, "B": 99.5, "C": 99.5, "D": 101.0}

        with patch("tradingbot.analysis.market_guard.MarketGuard") as MockGuard:
            mock_health = MagicMock()
            mock_health.spy_change_pct = -0.5
            mock_health.qqq_change_pct = -0.5
            MockGuard.return_value.check.return_value = mock_health

            trigger = self.tracker._check_circuit_breaker(trades, prices)
            assert trigger is not None
            assert "correlated_red" in trigger

    def test_correlated_red_below_threshold(self):
        """2 out of 4 losing (50%) — below 75% threshold."""
        trades = [
            _make_trade("A", 100.0, position_size=10),
            _make_trade("B", 100.0, position_size=10),
            _make_trade("C", 100.0, position_size=10),
            _make_trade("D", 100.0, position_size=10),
        ]
        prices = {"A": 99.5, "B": 99.5, "C": 101.0, "D": 101.0}

        with patch("tradingbot.analysis.market_guard.MarketGuard") as MockGuard:
            mock_health = MagicMock()
            mock_health.spy_change_pct = -0.5
            mock_health.qqq_change_pct = -0.5
            MockGuard.return_value.check.return_value = mock_health

            trigger = self.tracker._check_circuit_breaker(trades, prices)
            assert trigger is None

    def test_already_fired_prevents_refire(self):
        """Once fired, circuit breaker should not fire again."""
        self.tracker._circuit_breaker_fired = True
        trades = [
            _make_trade("AAPL", 150.0, position_size=50),
            _make_trade("MSFT", 400.0, position_size=20),
        ]
        prices = {"AAPL": 100.0, "MSFT": 300.0}  # massive loss
        trigger = self.tracker._check_circuit_breaker(trades, prices)
        assert trigger is None  # already fired, skip


class TestEmergencyClose:
    """Tests for _emergency_close_all."""

    @patch("tradingbot.tracking.trade_tracker.TradeTracker._send_circuit_breaker_alert")
    @patch("tradingbot.web.alert_store.update_outcome")
    def test_closes_all_trades(self, mock_update, mock_telegram):
        tracker = TradeTracker()
        trades = [
            _make_trade("AAPL", 150.0),
            _make_trade("MSFT", 400.0),
        ]
        prices = {"AAPL": 145.0, "MSFT": 395.0}

        closed = tracker._emergency_close_all(trades, prices, "test_trigger")
        assert closed == 2
        assert tracker._circuit_breaker_fired is True
        assert mock_update.call_count == 2

        # Check that all calls used "emergency_closed" status
        for call in mock_update.call_args_list:
            assert call[1]["status"] == "emergency_closed"
            assert call[1]["closed_at"] is not None

    @patch("tradingbot.tracking.trade_tracker.TradeTracker._send_circuit_breaker_alert")
    @patch("tradingbot.web.alert_store.update_outcome")
    def test_blends_pnl_for_tp1_hit(self, mock_update, mock_telegram):
        """If a trade was already at tp1_hit, blend the PnL."""
        tracker = TradeTracker()
        # TP1 was 155 (half sold there), now emergency close at 148
        trades = [
            _make_trade("AAPL", 150.0, tp1=155.0, status="tp1_hit"),
        ]
        prices = {"AAPL": 148.0}

        closed = tracker._emergency_close_all(trades, prices, "test")
        assert closed == 1

        call_kwargs = mock_update.call_args[1]
        pnl = call_kwargs["pnl_pct"]
        # half @ TP1: (155-150)/150 = +3.33%
        # half @ 148: (148-150)/150 = -1.33%
        # blended: (3.33 + (-1.33)) / 2 ≈ +1.0%
        assert 0.9 < pnl < 1.1


class TestTickIntegration:
    """Test that tick() properly invokes circuit breaker."""

    @patch("tradingbot.web.alert_store.update_outcome")
    @patch("tradingbot.web.alert_store.load_open_outcomes")
    @patch("tradingbot.web.alert_store.seed_outcomes_for_today", return_value=0)
    def test_tick_returns_circuit_breaker_info(self, mock_seed, mock_load, mock_update):
        """When circuit breaker fires, tick() returns trigger info."""
        mock_load.return_value = [
            _make_trade("AAPL", 150.0, position_size=50),
            _make_trade("MSFT", 400.0, position_size=20),
        ]

        tracker = TradeTracker()

        with patch.object(tracker, "_fetch_quotes", return_value={"AAPL": 130.0, "MSFT": 350.0}):
            with patch.object(tracker, "_send_circuit_breaker_alert"):
                with patch("tradingbot.analysis.market_guard.MarketGuard") as MockGuard:
                    mock_health = MagicMock()
                    mock_health.spy_change_pct = 0.0
                    mock_health.qqq_change_pct = 0.0
                    MockGuard.return_value.check.return_value = mock_health

                    result = tracker.tick()

        assert "circuit_breaker" in result
        assert result["updates"] == 2
