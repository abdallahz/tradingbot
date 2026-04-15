"""Tests for portfolio_calculator — account simulation for accurate daily P&L."""
import pytest
from datetime import datetime, timezone, timedelta


def _ts(hour: int, minute: int = 0) -> str:
    """Helper: create a UTC ISO timestamp for today at given ET-like hour."""
    dt = datetime(2026, 4, 14, hour, minute, 0, tzinfo=timezone.utc)
    return dt.isoformat()


def _make_outcome(
    symbol: str,
    entry: float,
    stop: float,
    tp1: float,
    exit_price: float,
    status: str,
    pnl_pct: float,
    alerted_at: str | None = None,
    closed_at: str | None = None,
    hit_at: str | None = None,
) -> dict:
    return {
        "symbol": symbol,
        "entry_price": entry,
        "stop_price": stop,
        "tp1_price": tp1,
        "tp2_price": tp1 * 1.5 if tp1 else 0,
        "exit_price": exit_price,
        "status": status,
        "pnl_pct": pnl_pct,
        "alerted_at": alerted_at,
        "closed_at": closed_at,
        "hit_at": hit_at or closed_at,
    }


class TestPortfolioCalculator:
    """Core portfolio simulation tests."""

    def test_empty_outcomes(self):
        from tradingbot.risk.portfolio_calculator import calculate_portfolio_return
        result = calculate_portfolio_return([], 10_000)
        assert result["portfolio_pnl_pct"] == 0.0
        assert result["ending_capital"] == 10_000

    def test_single_trade_win(self):
        """One trade: $10 entry, $9.50 stop, exit $10.30 = +3% per share.
        Risk = $0.50/share. With $10K capital, 0.5% risk = $50 risked.
        Shares = 50/0.50 = 100 shares. Position = $1000.
        P&L = 100 × $0.30 = $30. Portfolio = +0.30%.
        """
        from tradingbot.risk.portfolio_calculator import calculate_portfolio_return
        outcomes = [_make_outcome(
            "AAPL", 10.0, 9.50, 10.50, 10.30, "tp1_hit", 3.0,
            alerted_at=_ts(13, 0), closed_at=_ts(14, 0), hit_at=_ts(14, 0),
        )]
        # tp1_hit is partial — treated as partial close in timeline
        result = calculate_portfolio_return(outcomes, 10_000, 0.5)
        assert result["portfolio_pnl_pct"] > 0
        assert result["max_concurrent"] == 1

    def test_single_trade_loss(self):
        """One trade stopped out. Entry $50, stop $48.75, exit $48.75.
        Risk = $1.25. Risk amount = $50 (0.5% of $10K).
        Shares = 50 / 1.25 = 40. Position = $2000.
        Loss = 40 × (-$1.25) = -$50. Portfolio = -0.50%.
        """
        from tradingbot.risk.portfolio_calculator import calculate_portfolio_return
        outcomes = [_make_outcome(
            "TSLA", 50.0, 48.75, 52.0, 48.75, "stopped", -2.5,
            alerted_at=_ts(13, 0), closed_at=_ts(14, 0),
        )]
        result = calculate_portfolio_return(outcomes, 10_000, 0.5)
        assert result["portfolio_pnl_pct"] == -0.5
        assert result["ending_capital"] == 9950.0

    def test_concurrent_trades_split_capital(self):
        """Two trades open at the same time = capital split.
        Portfolio return should be less than sum of individual P&Ls.
        """
        from tradingbot.risk.portfolio_calculator import calculate_portfolio_return
        outcomes = [
            _make_outcome(
                "AAPL", 50.0, 48.75, 52.0, 52.0, "tp1_hit", 4.0,
                alerted_at=_ts(13, 0), hit_at=_ts(14, 0),
            ),
            _make_outcome(
                "TSLA", 100.0, 97.50, 104.0, 97.50, "stopped", -2.5,
                alerted_at=_ts(13, 0), closed_at=_ts(14, 30),
            ),
        ]
        result = calculate_portfolio_return(outcomes, 10_000, 0.5)
        # Both trades open at same time → concurrent
        assert result["max_concurrent"] == 2
        # Sum = 4 + (-2.5) = 1.5. But portfolio should be ~0 (avg-ish) not 1.5
        assert abs(result["portfolio_pnl_pct"]) < 1.5

    def test_sequential_trades_compound(self):
        """Trade A closes before Trade B opens → capital reused."""
        from tradingbot.risk.portfolio_calculator import calculate_portfolio_return
        outcomes = [
            _make_outcome(
                "AAPL", 50.0, 48.75, 52.0, 51.5, "tp2_hit", 3.0,
                alerted_at=_ts(13, 0), closed_at=_ts(13, 30),
            ),
            _make_outcome(
                "TSLA", 100.0, 97.50, 104.0, 102.0, "tp2_hit", 2.0,
                alerted_at=_ts(14, 0), closed_at=_ts(14, 30),
            ),
        ]
        result = calculate_portfolio_return(outcomes, 10_000, 0.5)
        # Sequential: max 1 at a time
        assert result["max_concurrent"] == 1
        assert result["portfolio_pnl_pct"] > 0

    def test_expired_trade(self):
        """Expired trade uses exit_price for P&L calculation."""
        from tradingbot.risk.portfolio_calculator import calculate_portfolio_return
        outcomes = [_make_outcome(
            "NVDA", 20.0, 19.50, 21.0, 19.80, "expired", -1.0,
            alerted_at=_ts(13, 0), closed_at=_ts(19, 30),
        )]
        result = calculate_portfolio_return(outcomes, 10_000, 0.5)
        assert result["portfolio_pnl_pct"] < 0

    def test_breakeven_trade(self):
        """Breakeven trade should return ~0% portfolio P&L."""
        from tradingbot.risk.portfolio_calculator import calculate_portfolio_return
        outcomes = [_make_outcome(
            "META", 30.0, 29.25, 31.5, 30.0, "breakeven", 0.0,
            alerted_at=_ts(13, 0), closed_at=_ts(14, 0),
        )]
        result = calculate_portfolio_return(outcomes, 10_000, 0.5)
        assert result["portfolio_pnl_pct"] == 0.0

    def test_risk_based_sizing(self):
        """Wider stop → fewer shares → less capital allocated."""
        from tradingbot.risk.portfolio_calculator import calculate_portfolio_return
        # Tight stop: $0.25 → shares = 50/0.25 = 200, position = $2000
        tight = [_make_outcome(
            "AAPL", 10.0, 9.75, 10.50, 10.50, "tp2_hit", 5.0,
            alerted_at=_ts(13, 0), closed_at=_ts(14, 0),
        )]
        # Wide stop: $1.00 → shares = 50/1.00 = 50, position = $500
        wide = [_make_outcome(
            "AAPL", 10.0, 9.00, 11.00, 11.00, "tp2_hit", 10.0,
            alerted_at=_ts(13, 0), closed_at=_ts(14, 0),
        )]
        r_tight = calculate_portfolio_return(tight, 10_000, 0.5)
        r_wide = calculate_portfolio_return(wide, 10_000, 0.5)
        # Tight stop uses more capital than wide stop
        assert r_tight["max_capital_used"] > r_wide["max_capital_used"]

    def test_capital_cap_at_available(self):
        """If position size exceeds available capital, cap it."""
        from tradingbot.risk.portfolio_calculator import calculate_portfolio_return
        # Very tight stop with high risk_pct → wants huge position
        outcomes = [_make_outcome(
            "PENNY", 5.0, 4.99, 5.10, 5.10, "tp2_hit", 2.0,
            alerted_at=_ts(13, 0), closed_at=_ts(14, 0),
        )]
        # risk = 5% of 1000 = $50. stop dist = $0.01. shares = 5000. value = $25K > $1K
        result = calculate_portfolio_return(outcomes, 1_000, 5.0)
        # Position capped at $1000 available
        assert result["max_capital_used"] <= 1_000

    def test_fallback_when_no_timestamps(self):
        """Legacy data without alerted_at falls back to average P&L."""
        from tradingbot.risk.portfolio_calculator import calculate_portfolio_return
        outcomes = [
            _make_outcome("A", 10, 9.5, 11, 10.5, "tp1_hit", 5.0),
            _make_outcome("B", 20, 19.5, 21, 19.5, "stopped", -2.5),
        ]
        result = calculate_portfolio_return(outcomes, 10_000, 0.5)
        # Fallback: average of 5.0 and -2.5 = 1.25
        assert result["portfolio_pnl_pct"] == 1.25

    def test_max_concurrent_tracking(self):
        """Three trades open, one closes, then two more open."""
        from tradingbot.risk.portfolio_calculator import calculate_portfolio_return
        outcomes = [
            _make_outcome("A", 10, 9.5, 11, 11, "tp2_hit", 10.0,
                          alerted_at=_ts(13, 0), closed_at=_ts(13, 30)),
            _make_outcome("B", 20, 19.5, 21, 21, "tp2_hit", 5.0,
                          alerted_at=_ts(13, 0), closed_at=_ts(14, 0)),
            _make_outcome("C", 30, 29.5, 31, 31, "tp2_hit", 3.3,
                          alerted_at=_ts(13, 0), closed_at=_ts(14, 30)),
            _make_outcome("D", 40, 39.5, 41, 41, "tp2_hit", 2.5,
                          alerted_at=_ts(13, 45), closed_at=_ts(14, 15)),
        ]
        result = calculate_portfolio_return(outcomes, 10_000, 0.5)
        # A, B, C all open at 13:00. A closes 13:30. D opens 13:45.
        # Peak at 13:00 = 3 (A,B,C). After 13:30 = 2 (B,C). At 13:45 = 3 (B,C,D).
        assert result["max_concurrent"] == 3


class TestPortfolioEdgeCases:
    """Edge cases and error handling."""

    def test_zero_stop_distance_skipped(self):
        """Trade with stop == entry should be skipped (div by zero)."""
        from tradingbot.risk.portfolio_calculator import calculate_portfolio_return
        outcomes = [_make_outcome(
            "BAD", 10.0, 10.0, 11.0, 10.5, "tp2_hit", 5.0,
            alerted_at=_ts(13, 0), closed_at=_ts(14, 0),
        )]
        result = calculate_portfolio_return(outcomes, 10_000, 0.5)
        assert result["portfolio_pnl_pct"] == 0.0

    def test_missing_entry_price_skipped(self):
        from tradingbot.risk.portfolio_calculator import calculate_portfolio_return
        outcomes = [{"symbol": "X", "entry_price": 0, "stop_price": 9,
                     "tp1_price": 11, "exit_price": 10, "status": "stopped",
                     "pnl_pct": -1, "alerted_at": _ts(13, 0), "closed_at": _ts(14, 0)}]
        result = calculate_portfolio_return(outcomes, 10_000, 0.5)
        assert result["portfolio_pnl_pct"] == 0.0

    def test_partial_close_tp1_hit(self):
        """tp1_hit status: 50% sold at tp1, rest still allocated."""
        from tradingbot.risk.portfolio_calculator import calculate_portfolio_return
        outcomes = [_make_outcome(
            "AAPL", 100.0, 97.5, 105.0, None, "tp1_hit", 5.0,
            alerted_at=_ts(13, 0), hit_at=_ts(14, 0),
        )]
        result = calculate_portfolio_return(outcomes, 10_000, 0.5)
        # tp1_hit generates a partial_close event at hit_at
        # 50% sold at tp1 ($105), rest returned at cost (still allocated)
        assert result["portfolio_pnl_pct"] > 0

    def test_event_ordering_close_before_open(self):
        """At same timestamp, closes should process before opens (free capital first)."""
        from tradingbot.risk.portfolio_calculator import calculate_portfolio_return
        # Trade A closes at 14:00, Trade B opens at 14:00
        outcomes = [
            _make_outcome("A", 10, 9.5, 11, 10.5, "tp2_hit", 5.0,
                          alerted_at=_ts(13, 0), closed_at=_ts(14, 0)),
            _make_outcome("B", 20, 19.5, 21, 20.5, "tp2_hit", 2.5,
                          alerted_at=_ts(14, 0), closed_at=_ts(15, 0)),
        ]
        result = calculate_portfolio_return(outcomes, 10_000, 0.5)
        # A closes at 14:00 (frees capital), then B opens at 14:00
        # Should be sequential, max_concurrent = 1
        assert result["max_concurrent"] == 1

    def test_portfolio_pnl_pct_in_trade_stats(self):
        """get_trade_stats should include portfolio_pnl_pct key."""
        from unittest.mock import patch
        from tradingbot.web.alert_store import get_trade_stats
        with patch("tradingbot.web.alert_store.load_outcomes_for_date", return_value=[]):
            stats = get_trade_stats()
        assert "portfolio_pnl_pct" in stats
