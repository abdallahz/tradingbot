"""EarningsFilter — blocks entries within N calendar days of earnings.

Uses the same Nasdaq earnings calendar API as the night research job.
Results are cached per calendar day — multiple calls within the same day
(e.g. O2 + O3 both calling _build_cards) cost only one network round-trip.
"""
from __future__ import annotations

import json
import logging
import urllib.request as _urlreq
from datetime import date, timedelta

log = logging.getLogger(__name__)

DEFAULT_BLOCK_DAYS = 2    # Block if earnings are today, tomorrow, or day after
_LOOKAHEAD_DAYS = 7       # How many days ahead to scan the calendar
_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


class EarningsFilter:
    """Fetches and caches the Nasdaq earnings calendar for the current session.

    Usage:
        ef = EarningsFilter()
        ef.load(symbols)              # once per day — no-op on repeat calls
        blocked, days = ef.is_blocked("NVDA")
    """

    def __init__(self, block_days: int = DEFAULT_BLOCK_DAYS) -> None:
        self.block_days = block_days
        self._earnings_map: dict[str, date] = {}
        self._fetch_date: date | None = None  # date when _earnings_map was built

    def load(self, symbols: list[str]) -> None:
        """Fetch upcoming earnings for the given symbol universe.

        Caches results for the current calendar day.  Calling load() again
        on the same day with more symbols extends the map (e.g. O2/O3 combined).
        """
        today = date.today()

        # Reset on new day (handles worker loops that run across midnight)
        if self._fetch_date is not None and self._fetch_date != today:
            self._earnings_map.clear()
            self._fetch_date = None

        # Only fetch symbols not already in the map
        new_symbols = [s for s in symbols if s not in self._earnings_map]
        if not new_symbols and self._fetch_date == today:
            return  # All symbols already loaded

        self._fetch_date = today
        symbol_set = set(new_symbols)
        fetched: dict[str, date] = {}

        for i in range(_LOOKAHEAD_DAYS):
            d = today + timedelta(days=i)
            url = f"https://api.nasdaq.com/api/calendar/earnings?date={d.isoformat()}"
            try:
                req = _urlreq.Request(url, headers=_HEADERS)
                with _urlreq.urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read())
                    rows = data.get("data", {}).get("rows") or []
                    for r in rows:
                        sym = r.get("symbol", "")
                        if sym in symbol_set and sym not in fetched:
                            fetched[sym] = d
            except Exception as exc:
                log.debug(f"[EARNINGS] Calendar fetch failed for {d}: {exc}")

        self._earnings_map.update(fetched)
        if fetched:
            log.info(
                f"[EARNINGS] {len(fetched)} symbols have upcoming earnings "
                f"in next {_LOOKAHEAD_DAYS}d: {sorted(fetched)}"
            )

    def days_to_earnings(self, symbol: str) -> int | None:
        """Return calendar days until next earnings, or None if not found."""
        d = self._earnings_map.get(symbol)
        if d is None:
            return None
        return max(0, (d - date.today()).days)

    def is_blocked(self, symbol: str) -> tuple[bool, int]:
        """Return (True, days_away) if earnings are within block_days, else (False, -1)."""
        days = self.days_to_earnings(symbol)
        if days is None:
            return False, -1
        if days <= self.block_days:
            return True, days
        return False, -1
