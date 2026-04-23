"""Tests for EarningsFilter — earnings calendar blocking logic."""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch, MagicMock
import json

import pytest

from tradingbot.scanner.earnings_filter import EarningsFilter


def _make_response(rows: list[dict]) -> bytes:
    """Build a Nasdaq-API-shaped JSON response."""
    return json.dumps({"data": {"rows": rows}}).encode()


class TestEarningsFilterIsBlocked:
    def test_no_earnings_not_blocked(self):
        ef = EarningsFilter()
        blocked, days = ef.is_blocked("AAPL")
        assert blocked is False
        assert days == -1

    def test_earnings_today_is_blocked(self):
        ef = EarningsFilter()
        ef._earnings_map["AAPL"] = date.today()
        ef._fetch_date = date.today()
        blocked, days = ef.is_blocked("AAPL", gap_pct=0.0)
        assert blocked is True
        assert days == 0

    def test_earnings_today_bmo_large_gap_passes(self):
        # Gap >= 3% on earnings day → assume BMO report, allow entry
        ef = EarningsFilter()
        ef._earnings_map["MBLY"] = date.today()
        ef._fetch_date = date.today()
        blocked, days = ef.is_blocked("MBLY", gap_pct=5.0)
        assert blocked is False
        assert days == 0

    def test_earnings_today_small_gap_still_blocked(self):
        # Gap < 3% on earnings day → could be AMC, block
        ef = EarningsFilter()
        ef._earnings_map["NEE"] = date.today()
        ef._fetch_date = date.today()
        blocked, days = ef.is_blocked("NEE", gap_pct=1.5)
        assert blocked is True
        assert days == 0

    def test_earnings_tomorrow_is_blocked(self):
        ef = EarningsFilter()
        ef._earnings_map["NVDA"] = date.today() + timedelta(days=1)
        ef._fetch_date = date.today()
        blocked, days = ef.is_blocked("NVDA")
        assert blocked is True
        assert days == 1

    def test_earnings_in_two_days_is_blocked(self):
        ef = EarningsFilter()
        ef._earnings_map["AMD"] = date.today() + timedelta(days=2)
        ef._fetch_date = date.today()
        blocked, days = ef.is_blocked("AMD")
        assert blocked is True
        assert days == 2

    def test_earnings_in_three_days_not_blocked(self):
        ef = EarningsFilter()
        ef._earnings_map["MSFT"] = date.today() + timedelta(days=3)
        ef._fetch_date = date.today()
        blocked, days = ef.is_blocked("MSFT")
        assert blocked is False

    def test_custom_block_days(self):
        ef = EarningsFilter(block_days=5)
        ef._earnings_map["TSLA"] = date.today() + timedelta(days=4)
        ef._fetch_date = date.today()
        blocked, _ = ef.is_blocked("TSLA")
        assert blocked is True


class TestEarningsFilterLoad:
    def _mock_urlopen(self, symbol_date_pairs: list[tuple[str, date]]):
        """Return a context-manager mock that serves per-date API responses."""
        today = date.today()

        def side_effect(req, timeout=8):
            url = req.full_url
            date_str = url.split("date=")[1]
            d = date.fromisoformat(date_str)
            rows = [
                {"symbol": sym}
                for sym, sym_date in symbol_date_pairs
                if sym_date == d
            ]
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=MagicMock(
                read=MagicMock(return_value=_make_response(rows))
            ))
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        return side_effect

    def test_load_populates_map(self):
        tomorrow = date.today() + timedelta(days=1)
        ef = EarningsFilter()
        side_effect = self._mock_urlopen([("AAPL", tomorrow)])

        with patch("tradingbot.scanner.earnings_filter._urlreq.urlopen", side_effect=side_effect):
            ef.load(["AAPL", "MSFT"])

        assert "AAPL" in ef._earnings_map
        assert ef._earnings_map["AAPL"] == tomorrow
        assert "MSFT" not in ef._earnings_map

    def test_load_only_fetches_once_per_day(self):
        ef = EarningsFilter()
        ef._fetch_date = date.today()
        ef._earnings_map["AAPL"] = date.today()

        call_count = {"n": 0}
        def side_effect(req, timeout=8):
            call_count["n"] += 1
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=MagicMock(
                read=MagicMock(return_value=_make_response([]))
            ))
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        with patch("tradingbot.scanner.earnings_filter._urlreq.urlopen", side_effect=side_effect):
            ef.load(["AAPL"])  # AAPL already in map, today already fetched

        assert call_count["n"] == 0  # no network calls made

    def test_load_resets_on_new_day(self):
        yesterday = date.today() - timedelta(days=1)
        ef = EarningsFilter()
        ef._fetch_date = yesterday  # stale
        ef._earnings_map["STALE"] = yesterday

        with patch("tradingbot.scanner.earnings_filter._urlreq.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=MagicMock(
                read=MagicMock(return_value=_make_response([]))
            ))
            cm.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = cm
            ef.load(["AAPL"])

        # Stale entry cleared
        assert "STALE" not in ef._earnings_map

    def test_load_tolerates_api_failure(self):
        ef = EarningsFilter()
        with patch("tradingbot.scanner.earnings_filter._urlreq.urlopen", side_effect=Exception("timeout")):
            ef.load(["AAPL"])  # should not raise
        # Map empty but filter functional
        blocked, _ = ef.is_blocked("AAPL")
        assert blocked is False
