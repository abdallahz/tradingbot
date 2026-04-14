"""
ibkr_client.py — Interactive Brokers data client via ib_insync.

Drop-in replacement for alpaca_client.py. Implements the same interface
(get_premarket_snapshots, get_tradable_universe) so the scanner, ranker,
pattern detection, and all analysis modules work without changes.

Connects to IB Gateway (port 4001 live, 4002 paper) via ib_insync.
Requires IB Gateway or TWS running locally or on a VPS.

Usage:
    client = IBKRClient(host="127.0.0.1", port=4002, client_id=1)
    universe = client.get_tradable_universe()
    snapshots = client.get_premarket_snapshots(universe)
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any

from tradingbot.analysis.technical_indicators import compute_indicators
from tradingbot.analysis.pattern_detector import detect_patterns
from tradingbot.models import SymbolSnapshot

logger = logging.getLogger(__name__)
DEBUG = os.environ.get("DEBUG", "").strip() == "1"

# Suffixes that indicate warrants, rights, units — not common shares.
_JUNK_SUFFIX = re.compile(
    r"\.(WS|RT|UN)$"
    r"|(?<=\w{4})[WRZU]$",
    re.IGNORECASE,
)


class IBKRClient:
    """Interactive Brokers data client using ib_insync.

    Provides the same public interface as AlpacaClient so the rest
    of the codebase (scanner, ranker, session runner) can use either
    broker transparently.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 4002,
        client_id: int = 1,
        timeout: float = 30.0,
        readonly: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.timeout = timeout
        self.readonly = readonly
        self._ib = None

    # ── Connection management ──────────────────────────────────────────

    def connect(self) -> None:
        """Establish connection to IB Gateway / TWS.

        If the initial clientId is already in use, retry once with a
        random ID (100–999) so overlapping cron jobs don't block each other.
        """
        import random
        from ib_insync import IB
        if self._ib is not None and self._ib.isConnected():
            return
        self._ib = IB()
        try:
            self._ib.connect(
                host=self.host,
                port=self.port,
                clientId=self.client_id,
                timeout=self.timeout,
                readonly=self.readonly,
            )
        except Exception as first_err:
            fallback_id = random.randint(100, 999)
            logger.warning(
                "clientId %d in use, retrying with %d: %s",
                self.client_id, fallback_id, first_err,
            )
            self._ib = IB()
            self._ib.connect(
                host=self.host,
                port=self.port,
                clientId=fallback_id,
                timeout=self.timeout,
                readonly=self.readonly,
            )
            self.client_id = fallback_id
        # Request live market data (type 1) if subscription is active.
        # Type 4 = "delayed-frozen" acts as automatic fallback:
        #   live → delayed → frozen → delayed-frozen.
        # This prevents Error 10089 from blocking all data when live
        # subscription hasn't propagated yet.
        self._ib.reqMarketDataType(4)
        logger.info(
            f"Connected to IBKR Gateway at {self.host}:{self.port} "
            f"(clientId={self.client_id}, marketDataType=delayed-frozen-fallback)"
        )

    def disconnect(self) -> None:
        """Disconnect from IB Gateway."""
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()
            logger.info("Disconnected from IBKR Gateway")

    @property
    def ib(self):
        """Lazy-connect and return the ib_insync.IB instance."""
        if self._ib is None or not self._ib.isConnected():
            self.connect()
        return self._ib

    def is_connected(self) -> bool:
        """Check if we have an active IB Gateway connection."""
        return self._ib is not None and self._ib.isConnected()

    # ── Contract helpers ───────────────────────────────────────────────

    def _stock_contract(self, symbol: str):
        """Create a US stock contract for the given symbol."""
        from ib_insync import Stock
        return Stock(symbol, "SMART", "USD")

    def _qualify_contracts(self, symbols: list[str]) -> dict[str, Any]:
        """Qualify a batch of stock contracts with IBKR.

        Returns {symbol: Contract} for successfully qualified contracts.
        """
        from ib_insync import Stock
        contracts = [Stock(s, "SMART", "USD") for s in symbols]
        qualified = self.ib.qualifyContracts(*contracts)
        result = {}
        for c in qualified:
            if c.conId:  # successfully qualified
                result[c.symbol] = c
        logger.debug(f"Qualified {len(result)}/{len(symbols)} contracts")
        return result

    # ── Market data fetchers ───────────────────────────────────────────

    def _request_market_data(self, contract) -> dict:
        """Request market data for a single contract via streaming mode.

        Returns a dict with price, bid, ask, volume, etc.

        Uses streaming mode (snapshot=False) because delayed-data
        subscriptions don't support snapshot=True, which causes
        Error 300 (Can't find EId).  Streaming fills within ~2-3s.
        """
        import math

        def _safe(val: float) -> float:
            """Return 0.0 for NaN / inf values."""
            if val != val or (isinstance(val, float) and math.isinf(val)):
                return 0.0
            return float(val)

        def _has_price(t) -> bool:
            p = t.last if t.last == t.last else (
                t.close if t.close == t.close else None
            )
            return p is not None and not math.isnan(p)

        # Use streaming mode directly — snapshot=True fails with delayed data
        ticker = self.ib.reqMktData(contract, genericTickList="", snapshot=False)
        for _ in range(6):
            self.ib.sleep(0.5)
            if _has_price(ticker):
                break

        result = {
            "last": _safe(ticker.last),
            "bid": _safe(ticker.bid),
            "ask": _safe(ticker.ask),
            "open": _safe(ticker.open),
            "high": _safe(ticker.high),
            "low": _safe(ticker.low),
            "close": _safe(ticker.close),
            "volume": int(ticker.volume) if ticker.volume == ticker.volume and ticker.volume >= 0 else 0,
        }

        self.ib.cancelMktData(contract)
        self.ib.sleep(0.3)  # Let EId recycle before next request
        return result

    def _request_historical_bars(
        self,
        contract,
        duration: str = "5 D",
        bar_size: str = "1 day",
        what_to_show: str = "TRADES",
        use_rth: bool = True,
    ) -> list[dict]:
        """Fetch historical bars for a contract.

        Args:
            contract: Qualified IB contract
            duration: How far back (e.g. "5 D", "1 M", "1 Y")
            bar_size: Bar granularity (e.g. "1 day", "15 mins", "1 hour")
            what_to_show: "TRADES", "MIDPOINT", "BID", "ASK"
            use_rth: True = regular trading hours only

        Returns list of dicts with: date, open, high, low, close, volume
        """
        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=use_rth,
            formatDate=1,
        )
        result = []
        for bar in bars:
            result.append({
                "date": bar.date,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": int(bar.volume),
            })
        return result

    # ── Volume computation helpers ─────────────────────────────────────

    @staticmethod
    def _compute_today_volume(intraday_bars: list[dict]) -> int:
        """Sum volume from today's intraday bars.

        Separates today's 15-min bars from yesterday's by checking the
        date portion of each bar's timestamp.  Returns 0 if no intraday
        bars exist or none belong to today.
        """
        if not intraday_bars:
            return 0

        from datetime import date as dt_date

        # Determine today's date from the most recent bar
        last_bar = intraday_bars[-1]
        bar_date = last_bar.get("date")
        if bar_date is None:
            return 0

        if hasattr(bar_date, "date"):
            today = bar_date.date()
        elif isinstance(bar_date, str):
            today = dt_date.fromisoformat(bar_date[:10])
        else:
            today = bar_date

        total = 0
        for bar in intraday_bars:
            bd = bar.get("date")
            if bd is None:
                continue
            if hasattr(bd, "date"):
                bd_date = bd.date()
            elif isinstance(bd, str):
                bd_date = dt_date.fromisoformat(bd[:10])
            else:
                bd_date = bd
            if bd_date == today:
                total += int(bar.get("volume", 0))
        return total

    @staticmethod
    def _compute_relative_volume(
        today_volume: int,
        prev_day_volume: int,
        intraday_bars: list[dict] | None = None,
    ) -> float:
        """Compute time-normalised relative volume.

        Compares today's partial-day cumulative volume against yesterday's
        full-day volume, scaled by the fraction of the 390-minute trading
        session elapsed.  This avoids the pitfall of comparing a 5-hour
        partial session against a full 6.5-hour previous day.

        Falls back to intraday bar comparison when possible, and returns
        1.0 when data is insufficient.
        """
        if prev_day_volume <= 0:
            return 1.0 if today_volume > 0 else 0.0

        # If intraday bars are available, try to get prev-day volume from them
        # for a more apples-to-apples comparison (same bar count).
        if intraday_bars:
            from datetime import date as dt_date

            last_bar = intraday_bars[-1]
            bar_date = last_bar.get("date")
            if bar_date is not None:
                if hasattr(bar_date, "date"):
                    today_date = bar_date.date()
                elif isinstance(bar_date, str):
                    today_date = dt_date.fromisoformat(bar_date[:10])
                else:
                    today_date = bar_date

                today_vol = 0
                today_bar_count = 0
                prev_vol = 0
                prev_bar_count = 0

                for bar in intraday_bars:
                    bd = bar.get("date")
                    if bd is None:
                        continue
                    if hasattr(bd, "date"):
                        bd_date = bd.date()
                    elif isinstance(bd, str):
                        bd_date = dt_date.fromisoformat(bd[:10])
                    else:
                        bd_date = bd

                    vol = int(bar.get("volume", 0))
                    if bd_date == today_date:
                        today_vol += vol
                        today_bar_count += 1
                    else:
                        prev_vol += vol
                        prev_bar_count += 1

                # Compare same number of bars for fairness
                if prev_bar_count > 0 and today_bar_count > 0:
                    prev_per_bar = prev_vol / prev_bar_count
                    today_per_bar = today_vol / today_bar_count
                    if prev_per_bar > 0:
                        return today_per_bar / prev_per_bar

        # Fallback: time-of-day scaling
        from zoneinfo import ZoneInfo
        _ET = ZoneInfo("America/New_York")
        now_et = datetime.now(_ET)
        market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        elapsed_min = max((now_et - market_open).total_seconds() / 60, 1)
        day_fraction = min(elapsed_min / 390.0, 1.0)

        if day_fraction <= 0:
            return 1.0

        # Expected volume at this point in the day
        expected = prev_day_volume * day_fraction
        return today_volume / expected if expected > 0 else 1.0

    def _fetch_batch_data(self, contracts: dict[str, Any]) -> dict[str, dict]:
        """Fetch snapshot + historical data for a batch of contracts.

        Returns {symbol: {snapshot: {...}, daily_bars: [...], intraday_bars: [...]}}
        """
        batch_data: dict[str, dict] = {}

        for symbol, contract in contracts.items():
            try:
                # Snapshot (current prices)
                snapshot = self._request_market_data(contract)

                # Daily bars (5 days for prev close, volume, ATR)
                daily_bars = self._request_historical_bars(
                    contract, duration="10 D", bar_size="1 day",
                    what_to_show="TRADES", use_rth=True,
                )

                # Intraday 15-min bars (2 days for EMA, VWAP, patterns)
                intraday_bars = self._request_historical_bars(
                    contract, duration="2 D", bar_size="15 mins",
                    what_to_show="TRADES", use_rth=False,
                )

                batch_data[symbol] = {
                    "snapshot": snapshot,
                    "daily_bars": daily_bars,
                    "intraday_bars": intraday_bars,
                }

                # Rate limiting: give IBKR time to recycle EIds between symbols
                self.ib.sleep(0.5)

            except Exception as e:
                logger.warning(f"Error fetching data for {symbol}: {e}")
                continue

        return batch_data

    # ── Bar conversion (IBKR format → analysis module format) ──────────

    @staticmethod
    def _convert_bars_for_analysis(bars: list[dict]) -> list:
        """Convert IBKR bar dicts to objects with .open/.high/.low/.close/.volume/.timestamp
        attributes, matching what compute_indicators and detect_patterns expect.
        """
        class BarProxy:
            """Lightweight stand-in for Alpaca Bar objects."""
            __slots__ = ("open", "high", "low", "close", "volume", "timestamp")
            def __init__(self, d: dict):
                self.open = float(d["open"])
                self.high = float(d["high"])
                self.low = float(d["low"])
                self.close = float(d["close"])
                self.volume = int(d["volume"])
                self.timestamp = d.get("date", datetime.now())

        return [BarProxy(b) for b in bars]

    # ── Core scanning interface ────────────────────────────────────────

    def get_premarket_snapshots(self, universe: list[str]) -> list[SymbolSnapshot]:
        """Fetch snapshot data for candidate symbols from IB Gateway.

        Drop-in replacement for AlpacaClient.get_premarket_snapshots().
        """
        snapshots: list[SymbolSnapshot] = []
        BATCH_SIZE = 15  # Conservative to avoid EId exhaustion on IBKR socket

        batches = [universe[i:i + BATCH_SIZE] for i in range(0, len(universe), BATCH_SIZE)]
        for batch_idx, batch in enumerate(batches):
            if batch_idx > 0:
                self.ib.sleep(2)  # Pause between batches for EId pool recovery
            logger.info(f"Batch {batch_idx + 1}/{len(batches)}: {len(batch)} symbols")
            try:
                # Qualify contracts first (validates symbols exist on IBKR)
                contracts = self._qualify_contracts(batch)
                if not contracts:
                    logger.warning(f"Batch {batch_idx + 1}: no contracts qualified")
                    continue
                logger.info(f"Batch {batch_idx + 1}: {len(contracts)}/{len(batch)} qualified")

                # Fetch all data for this batch
                batch_data = self._fetch_batch_data(contracts)

            except Exception as e:
                logger.exception(f"Batch {batch_idx + 1} failed: {type(e).__name__}: {e}")
                continue

            drop_counts: dict[str, int] = {}

            for symbol, data in batch_data.items():
                try:
                    snap = data["snapshot"]
                    daily_bars = data["daily_bars"]
                    intraday_bars = data["intraday_bars"]

                    # Current price (prefer last trade, fall back to midpoint)
                    current_price = snap["last"]
                    if not current_price or current_price <= 0:
                        bid, ask = snap["bid"], snap["ask"]
                        current_price = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0
                    if not current_price or current_price <= 0:
                        drop_counts["no_price"] = drop_counts.get("no_price", 0) + 1
                        continue

                    # Previous close from daily bars
                    if len(daily_bars) < 2:
                        drop_counts["no_prev_close"] = drop_counts.get("no_prev_close", 0) + 1
                        continue
                    prev_close = daily_bars[-2]["close"]
                    if not prev_close or prev_close <= 0:
                        drop_counts["no_prev_close"] = drop_counts.get("no_prev_close", 0) + 1
                        continue

                    # Gap calculation
                    gap_pct = ((current_price - prev_close) / prev_close) * 100

                    # Spread
                    bid = snap["bid"] if snap["bid"] > 0 else current_price
                    ask = snap["ask"] if snap["ask"] > 0 else current_price
                    spread_pct = ((ask - bid) / current_price) * 100 if current_price > 0 else 0

                    # Volume metrics
                    # IBKR delayed data's ticker.volume can be unreliable
                    # (partial / stale), so we compute cumulative day volume
                    # from intraday bars when available.
                    snapshot_vol = snap["volume"]
                    prev_volume = daily_bars[-2]["volume"] if len(daily_bars) >= 2 else 0

                    # Compute today's cumulative volume from intraday 15-min bars
                    # (more reliable than delayed snapshot volume).
                    today_bar_vol = self._compute_today_volume(intraday_bars)
                    premarket_vol = today_bar_vol if today_bar_vol > 0 else snapshot_vol

                    # Time-normalised relative volume: compare today's
                    # cumulative volume vs yesterday's full-day volume,
                    # scaled by the fraction of the trading day elapsed.
                    relative_volume = self._compute_relative_volume(
                        today_volume=premarket_vol,
                        prev_day_volume=prev_volume,
                        intraday_bars=intraday_bars,
                    )
                    dollar_volume = (prev_volume * prev_close) if prev_volume else (premarket_vol * current_price * 5)

                    # Technical indicators from intraday bars
                    intraday_proxy = self._convert_bars_for_analysis(intraday_bars) if intraday_bars else []
                    daily_proxy = self._convert_bars_for_analysis(daily_bars) if daily_bars else []
                    symbol_bars = intraday_proxy if intraday_proxy else daily_proxy

                    tech = compute_indicators(symbol_bars, daily_bars=daily_proxy)
                    ema9 = tech.get("ema9", current_price)
                    ema20 = tech.get("ema20", current_price * 0.99)
                    vwap = tech.get("vwap", current_price)
                    patterns = detect_patterns(symbol_bars, tech)
                    atr_val = tech.get("atr", current_price * 0.02)

                    # Recent volume (per-minute proxy)
                    avg_vol_base = prev_volume if prev_volume > 0 else premarket_vol * 5
                    avg_volume_20 = avg_vol_base // 390 if avg_vol_base > 0 else 1
                    recent_volume = premarket_vol

                    # Open price & intraday change
                    open_price = snap["open"] if snap["open"] > 0 else prev_close
                    intraday_change_pct = (
                        ((current_price - open_price) / open_price) * 100
                        if open_price > 0 else 0.0
                    )

                    # Pre-market high/low
                    pm_high = snap["high"] if snap["high"] > 0 else current_price
                    pm_low = snap["low"] if snap["low"] > 0 else current_price

                    # Reclaim level (PM high)
                    if pm_high and pm_high > current_price * 0.9:
                        reclaim_level = pm_high
                    else:
                        reclaim_level = vwap if vwap > 0 else current_price

                    # Pullback low (bull invalidation)
                    bull_anchor = max(
                        prev_close,
                        ema20 if ema20 > 0 else 0.0,
                        pm_low if pm_low > 0 else 0.0,
                    )
                    pullback_low = min(bull_anchor, current_price - atr_val * 0.5)

                    # Pullback high (bear invalidation)
                    pullback_high = reclaim_level + atr_val

                    # S/R levels — same logic as Alpaca client
                    is_breakout = (
                        current_price >= reclaim_level * 0.995
                        and reclaim_level > 0
                    )

                    bar_support = tech.get("support", 0.0)
                    bar_resistance = tech.get("resistance", 0.0)
                    prev_day_high = tech.get("prev_day_high", 0.0)
                    prev_day_low = tech.get("prev_day_low", 0.0)
                    max_res_dist = atr_val * 2 if atr_val > 0 else current_price * 0.06

                    if is_breakout:
                        key_support = reclaim_level - atr_val * 0.25
                        if (bar_resistance > 0
                                and bar_resistance > current_price
                                and (bar_resistance - current_price) <= max_res_dist):
                            key_resistance = bar_resistance
                        elif (prev_day_high > 0
                                and prev_day_high > current_price
                                and (prev_day_high - current_price) <= max_res_dist):
                            key_resistance = prev_day_high
                        else:
                            key_resistance = current_price + atr_val * 2
                    else:
                        support_candidates = [
                            v for v in [
                                vwap, ema20, pm_low, prev_close,
                                bar_support, prev_day_low,
                            ]
                            if v and 0 < v < current_price
                        ]
                        if atr_val > 0:
                            support_candidates = [
                                v for v in support_candidates
                                if (current_price - v) <= atr_val * 2
                            ]
                        if len(support_candidates) >= 3:
                            support_candidates.sort()
                            key_support = support_candidates[1]
                        elif support_candidates:
                            key_support = min(support_candidates)
                        else:
                            key_support = current_price - atr_val

                        resistance_candidates = [reclaim_level]
                        if (prev_day_high > 0
                                and prev_day_high > current_price
                                and (prev_day_high - current_price) <= max_res_dist):
                            resistance_candidates.append(prev_day_high)
                        if (bar_resistance > 0
                                and bar_resistance > current_price
                                and (bar_resistance - current_price) <= max_res_dist):
                            resistance_candidates.append(bar_resistance)
                        above = [r for r in resistance_candidates if r > current_price]
                        key_resistance = min(above) if above else current_price + atr_val

                    snapshots.append(
                        SymbolSnapshot(
                            symbol=symbol,
                            price=current_price,
                            gap_pct=gap_pct,
                            premarket_volume=int(premarket_vol),
                            dollar_volume=dollar_volume,
                            spread_pct=spread_pct,
                            relative_volume=relative_volume,
                            catalyst_score=0.0,
                            ema9=ema9,
                            ema20=ema20,
                            vwap=vwap,
                            recent_volume=int(recent_volume),
                            avg_volume_20=int(avg_volume_20),
                            pullback_low=pullback_low,
                            reclaim_level=reclaim_level,
                            pullback_high=pullback_high,
                            key_support=key_support,
                            key_resistance=key_resistance,
                            atr=atr_val,
                            open_price=open_price,
                            intraday_change_pct=intraday_change_pct,
                            daily_ema50=tech.get("daily_ema50", 0.0),
                            patterns=patterns,
                            raw_bars=symbol_bars,
                            tech_indicators=tech,
                        )
                    )

                except Exception as e:
                    logger.warning(f"Error processing {symbol}: {type(e).__name__}: {e}")
                    drop_counts["exception"] = drop_counts.get("exception", 0) + 1
                    continue

            if drop_counts:
                logger.debug(f"Batch {batch_idx + 1} drops: {drop_counts}")

        logger.info(f"{len(snapshots)} snapshots ready (IBKR)")
        return snapshots

    # ── Core watchlist (same as AlpacaClient) ──────────────────────────

    _CORE_WATCHLIST: list[str] = [
        # Mega-cap Tech
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
        "AVGO", "AMD", "INTC", "MU", "SMCI", "ARM", "PLTR", "CRWD",
        # Financials
        "JPM", "GS", "V", "MA", "PYPL", "XYZ", "COIN",
        # Healthcare
        "LLY", "UNH", "MRNA", "BNTX",
        # Consumer / EV
        "WMT", "COST", "UBER", "RIVN", "LCID", "NIO",
        # Energy
        "XOM", "CVX",
        # ETFs (market context)
        "SPY", "QQQ", "IWM",
    ]

    def _run_single_scan(
        self,
        scan_code: str,
        num_rows: int = 50,
        above_price: float = 0.0,
        above_volume: int = 0,
        location: str = "STK.US.MAJOR",
    ) -> set[str]:
        """Run one TWS scanner and return matched symbols.

        Wraps ``reqScannerData`` with common filtering (junk suffix,
        dot symbols) and error handling.
        """
        from ib_insync import ScannerSubscription, TagValue

        found: set[str] = set()
        try:
            sub = ScannerSubscription(
                instrument="STK",
                locationCode=location,
                scanCode=scan_code,
                numberOfRows=num_rows,
            )
            # Server-side filters — narrows results before they reach us,
            # reducing noise and speeding up the scan.
            tag_values: list[TagValue] = []
            if above_price > 0:
                tag_values.append(TagValue("priceAbove", str(above_price)))
            if above_volume > 0:
                tag_values.append(TagValue("volumeAbove", str(above_volume)))

            results = self.ib.reqScannerData(sub, scannerSubscriptionFilterOptions=tag_values)
            for item in results:
                sym = item.contractDetails.contract.symbol
                if not _JUNK_SUFFIX.search(sym) and "." not in sym:
                    found.add(sym)
            self.ib.sleep(0.5)
        except Exception as e:
            logger.warning(f"IBKR scanner {scan_code} failed: {e}")

        return found

    def _get_scanner_symbols(self) -> list[str]:
        """Fetch today's gappers + movers using IBKR's TWS Scanner API.

        Runs 5 scanner subscriptions to build a broad candidate universe:
        1. TOP_OPEN_PERC_GAIN — stocks gapping UP from previous close (key scan)
        2. HIGH_OPEN_GAP — alternative gap scanner (overlaps with #1)
        3. MOST_ACTIVE — highest volume (catches mega-cap momentum)
        4. TOP_PERC_GAIN — biggest % gainers intraday
        5. HOT_BY_VOLUME — unusual volume vs recent average (pre-market movers)

        All scanners have server-side filters: price >= $5, volume >= 50K.
        This eliminates penny stocks and illiquid names at the IBKR server
        level, before data reaches us.

        Returns de-duplicated list of symbols (~150-300 unique).
        """
        symbols: set[str] = set()

        # ── Gap-specific scanners (highest value for gap-and-go) ──────
        # These find stocks opening above previous close — exactly what
        # the GapScanner needs.  Addresses the 68× universe gap identified
        # in backtest analysis.
        symbols |= self._run_single_scan(
            "TOP_OPEN_PERC_GAIN", num_rows=100,
            above_price=5.0, above_volume=50_000,
        )
        symbols |= self._run_single_scan(
            "HIGH_OPEN_GAP", num_rows=100,
            above_price=5.0, above_volume=50_000,
        )

        # ── Activity scanners (catch momentum in mega-caps) ───────────
        symbols |= self._run_single_scan(
            "MOST_ACTIVE", num_rows=50,
            above_price=5.0,
        )
        symbols |= self._run_single_scan(
            "TOP_PERC_GAIN", num_rows=50,
            above_price=5.0, above_volume=50_000,
        )
        symbols |= self._run_single_scan(
            "HOT_BY_VOLUME", num_rows=50,
            above_price=5.0, above_volume=50_000,
        )

        logger.info(f"IBKR scanner returned {len(symbols)} unique symbols")
        return list(symbols)

    def get_screener_symbols(self) -> list[str]:
        """Public alias matching AlpacaClient.get_screener_symbols()."""
        return self._get_scanner_symbols()

    def get_tradable_universe(self) -> list[str]:
        """Build today's scan universe — same interface as AlpacaClient."""
        dynamic = self._get_scanner_symbols()
        merged = set(dynamic) | set(self._CORE_WATCHLIST)
        universe = sorted(merged)
        logger.info(
            f"universe: {len(self._CORE_WATCHLIST)} core + "
            f"{len(dynamic)} scanner = {len(universe)} total (IBKR)"
        )
        return universe

    # ── Quote helpers (used by trade tracker and position monitor) ─────

    def get_latest_price(self, symbol: str) -> float:
        """Get the latest trade price for a single symbol."""
        contract = self._stock_contract(symbol)
        data = self._request_market_data(contract)
        return data["last"] if data["last"] > 0 else 0.0

    def get_latest_prices(self, symbols: list[str]) -> dict[str, float]:
        """Get latest prices for multiple symbols. Returns {symbol: price}."""
        prices: dict[str, float] = {}
        for symbol in symbols:
            try:
                price = self.get_latest_price(symbol)
                if price > 0:
                    prices[symbol] = price
            except Exception as e:
                logger.warning(f"Failed to get price for {symbol}: {e}")
        return prices

    # ── Account data (used by capital allocator) ──────────────────────

    def get_account_summary(self) -> dict[str, float]:
        """Get key account values from IBKR.

        Returns dict with: net_liquidation, buying_power, cash_balance,
        unrealized_pnl, realized_pnl.
        """
        account_values = self.ib.accountSummary()
        result: dict[str, float] = {}
        key_map = {
            "NetLiquidation": "net_liquidation",
            "BuyingPower": "buying_power",
            "CashBalance": "cash_balance",
            "UnrealizedPnL": "unrealized_pnl",
            "RealizedPnL": "realized_pnl",
        }
        for av in account_values:
            if av.tag in key_map and av.currency == "USD":
                result[key_map[av.tag]] = float(av.value)
        return result

    def get_positions(self) -> list[dict]:
        """Get all open positions from IBKR.

        Returns list of dicts: symbol, quantity, avg_cost, market_price,
        unrealized_pnl, market_value.
        """
        positions = self.ib.positions()
        result = []
        for pos in positions:
            result.append({
                "symbol": pos.contract.symbol,
                "quantity": int(pos.position),
                "avg_cost": float(pos.avgCost),
                "market_value": float(pos.position) * float(pos.avgCost),
            })
        return result

    def get_open_orders(self) -> list[dict]:
        """Get all open/pending orders from IBKR.

        Returns list of dicts: order_id, symbol, action, quantity,
        order_type, limit_price, aux_price (stop), status.
        """
        trades = self.ib.openTrades()
        result = []
        for trade in trades:
            order = trade.order
            contract = trade.contract
            result.append({
                "order_id": order.orderId,
                "symbol": contract.symbol,
                "action": order.action,  # BUY or SELL
                "quantity": int(order.totalQuantity),
                "order_type": order.orderType,  # LMT, STP, MKT
                "limit_price": float(order.lmtPrice) if order.lmtPrice else 0.0,
                "aux_price": float(order.auxPrice) if order.auxPrice else 0.0,
                "status": trade.orderStatus.status,
                "oca_group": order.ocaGroup or "",
            })
        return result
