from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical.screener import ScreenerClient
from alpaca.data.requests import (
    StockBarsRequest, StockLatestQuoteRequest, StockSnapshotRequest,
    MostActivesRequest, MarketMoversRequest,
)
from alpaca.data.timeframe import TimeFrame
import re

from tradingbot.analysis.technical_indicators import compute_indicators, interpret_signals
from tradingbot.analysis.pattern_detector import detect_patterns
from tradingbot.models import SymbolSnapshot

logger = logging.getLogger(__name__)

# Enable debug logging with environment variable: DEBUG=1
DEBUG = os.environ.get("DEBUG", "").strip() == "1"


# Suffixes that indicate warrants, rights, units — not common shares.
# Only flag W/R/Z/U suffixes on tickers with 5+ chars to avoid false positives
# on legitimate short tickers like MU, NR, etc.
_JUNK_SUFFIX = re.compile(
    r"\.(WS|RT|UN)$"       # dot-separated: VLN.WS, GAB.RT
    r"|(?<=\w{4})[WRZU]$",  # 5+ char tickers ending in W/R/Z/U: RMSGW, HUBCZ
    re.IGNORECASE,
)


class AlpacaClient:
    def __init__(self, api_key: str, api_secret: str, paper: bool = True, data_feed: str = "iex") -> None:
        self.client = StockHistoricalDataClient(api_key, api_secret)
        self.screener = ScreenerClient(api_key, api_secret)
        self.paper = paper
        self.data_feed = data_feed  # "iex" (free) or "sip" (paid)

    def _fetch_batch(self, symbols: list[str]) -> tuple[dict, dict, Any, Any]:
        """
        Fetch quotes, snapshots, daily bars, and intraday bars for a batch of symbols.
        Feed is configurable: 'iex' (free) or 'sip' (paid). Raises on failure so the caller can log and skip.
        """
        feed = self.data_feed
        quote_request = StockLatestQuoteRequest(symbol_or_symbols=symbols, feed=feed)
        quotes = self.client.get_stock_latest_quote(quote_request)

        snapshot_request = StockSnapshotRequest(symbol_or_symbols=symbols, feed=feed)
        snapshot_data = self.client.get_stock_snapshot(snapshot_request)

        end = datetime.now()
        start = end - timedelta(days=5)
        bars_request = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Day,  # type: ignore[arg-type]
            start=start,
            end=end,
            feed=feed,
        )
        bars = self.client.get_stock_bars(bars_request)

        intraday_start = end - timedelta(days=2)
        try:
            from alpaca.data.timeframe import TimeFrameUnit
            intraday_tf = TimeFrame(15, TimeFrameUnit.Minute)  # type: ignore[call-arg]
            intraday_bars = self.client.get_stock_bars(
                StockBarsRequest(
                    symbol_or_symbols=symbols,
                    timeframe=intraday_tf,
                    start=intraday_start,
                    end=end,
                    feed=feed,
                )
            )
        except Exception as e:
            logger.warning(f"Intraday bars fetch failed: {e}, falling back to daily bars")
            intraday_bars = bars

        return quotes, snapshot_data, bars, intraday_bars

    def get_premarket_snapshots(self, universe: list[str], **kwargs) -> list[SymbolSnapshot]:
        """Fetch premarket data for candidate symbols, batched in groups of 50."""
        snapshots: list[SymbolSnapshot] = []
        BATCH_SIZE = 50

        batches = [universe[i:i + BATCH_SIZE] for i in range(0, len(universe), BATCH_SIZE)]
        for batch_idx, batch in enumerate(batches):
            try:
                quotes, snapshot_data, bars, intraday_bars = self._fetch_batch(batch)
            except Exception as e:
                logger.exception(f"Batch {batch_idx + 1} failed: {type(e).__name__}: {e}")
                continue  # skip this batch, try next

            # Tally drop reasons for summary
            drop_counts: dict[str, int] = {}

            for symbol in batch:
                try:
                    if symbol not in snapshot_data or symbol not in quotes:
                        drop_counts["missing_snapshot_or_quote"] = drop_counts.get("missing_snapshot_or_quote", 0) + 1
                        continue
                    
                    snap = snapshot_data[symbol]
                    quote = quotes[symbol]
                    
                    # Calculate gap from previous close
                    prev_close = self._get_previous_close(bars, symbol)
                    if prev_close is None or prev_close == 0:
                        drop_counts["no_prev_close"] = drop_counts.get("no_prev_close", 0) + 1
                        continue
                    
                    # Get current price safely
                    if snap.latest_trade and hasattr(snap.latest_trade, 'price') and snap.latest_trade.price:
                        current_price = float(snap.latest_trade.price)
                    else:
                        current_price = float(quote.ask_price) if quote.ask_price else 0.0
                    
                    if not current_price:
                        drop_counts["no_price"] = drop_counts.get("no_price", 0) + 1
                        continue
                    
                    gap_pct = ((current_price - prev_close) / prev_close) * 100
                    
                    # Calculate spread
                    bid = float(quote.bid_price) if quote.bid_price else current_price
                    ask = float(quote.ask_price) if quote.ask_price else current_price
                    spread_pct = ((ask - bid) / current_price) * 100 if current_price > 0 else 0
                    
                    # Data quality validation (paper trading data can be unreliable)
                    data_warning = self._validate_price_data(symbol, current_price, prev_close, gap_pct, spread_pct)
                    
                    if DEBUG:
                        warn_str = f" ⚠️ {data_warning}" if data_warning else ""
                        logger.debug(f"{symbol}: price=${current_price:.2f}, prev=${prev_close:.2f}, gap={gap_pct:.2f}%{warn_str}")
                    
                    # Skip symbols with critical data quality issues
                    if data_warning and any(
                        flag in data_warning for flag in ["extreme_gap", "wide_spread"]
                    ):
                        drop_counts["data_quality"] = drop_counts.get("data_quality", 0) + 1
                        continue
                    
                    # Get volume metrics
                    # snap.daily_bar.volume at pre-market time = pre-market accumulated
                    # volume only (regular session hasn't opened yet). Naming it
                    # premarket_vol makes the distinction clear.
                    premarket_vol = snap.daily_bar.volume if snap.daily_bar else 0

                    # Compute relative volume (pre-market shares vs previous full session)
                    prev_volume = self._get_previous_volume(bars, symbol)
                    relative_volume = premarket_vol / prev_volume if prev_volume and prev_volume > 0 else 1.0

                    # Dollar volume must reflect REAL liquidity — use yesterday's full-session
                    # notional value (prev_volume × prev_close). Pre-market volume is tiny
                    # (often 50-200k shares) and would falsely fail the $1M filter for
                    # liquid names. Fall back to 5× pre-market estimate if prev data missing.
                    dollar_volume = (prev_volume * prev_close) if (prev_volume and prev_close) else (premarket_vol * current_price * 5)
                    
                    # Compute enhanced technical indicators from 15-min intraday bars
                    # Pass daily bars too so compute_indicators can derive
                    # daily ATR, daily S/R, and prev-day high/low.
                    symbol_bars = self._get_bars_list(intraday_bars, symbol)
                    if not symbol_bars:  # fall back to daily if intraday unavailable
                        symbol_bars = self._get_bars_list(bars, symbol)
                    daily_symbol_bars = self._get_bars_list(bars, symbol)
                    tech = compute_indicators(symbol_bars, daily_bars=daily_symbol_bars)
                    ema9  = tech.get("ema9",  current_price)
                    ema20 = tech.get("ema20", current_price * 0.99)
                    vwap  = tech.get("vwap",  current_price)
                    patterns = detect_patterns(symbol_bars, tech)

                    # Interpret technical signals for debug/logging
                    if DEBUG and tech:
                        tech_signals = interpret_signals(tech, current_price)
                        rsi  = tech.get("rsi",  0.0)
                        macd = tech.get("macd", 0.0)
                        atr  = tech.get("atr",  0.0)
                        logger.debug(f"{symbol} indicators: RSI={rsi:.1f}, MACD={macd:.3f}, ATR={atr:.2f}, signals={tech_signals}")

                    # Get recent volume for volume spike detection.
                    # Pre-market (before open): snap.minute_bar is None — Alpaca only
                    # returns a minute bar during regular market hours. Fall back to
                    # the accumulated pre-market volume so the volume-spike check works.
                    #
                    # IMPORTANT: After-hours or stale minute bars can return tiny
                    # volumes (e.g. 100 shares) that are lower than the per-minute
                    # average — this creates a false negative on volume_spike.
                    # Only trust the minute bar if its volume is at least 10% of
                    # the per-minute baseline; otherwise use premarket_vol.
                    recent_minute_vol = snap.minute_bar.volume if (snap.minute_bar and snap.minute_bar.volume) else 0
                    avg_vol_base = prev_volume if prev_volume and prev_volume > 0 else premarket_vol * 5
                    avg_volume_20 = avg_vol_base // 390 if avg_vol_base > 0 else 1
                    # Sanity gate: minute bar vol must exceed 10% of per-minute avg
                    # to be considered real intraday data (not a stale after-hours print)
                    if recent_minute_vol > 0 and recent_minute_vol >= avg_volume_20 * 0.1:
                        recent_volume = recent_minute_vol
                    else:
                        recent_volume = premarket_vol
                    if DEBUG:
                        mode = "minute-bar" if (recent_minute_vol > 0 and recent_minute_vol >= avg_volume_20 * 0.1) else "premarket-fallback"
                        logger.debug(f"{symbol} volume: recent={recent_volume:,} ({mode}), avg_per_min={avg_volume_20:,}, spike_ratio={recent_volume/max(1,avg_volume_20):.1f}x")

                    # ATR for volatility sizing
                    atr_val = tech.get("atr", current_price * 0.02)

                    # ── Today's open price & intraday change ──────────────
                    # daily_bar.open at pre-market = today's first print.
                    # During regular hours this is the official open price.
                    open_price = float(snap.daily_bar.open) if snap.daily_bar else prev_close
                    if not open_price or open_price <= 0:
                        open_price = prev_close
                    intraday_change_pct = (
                        ((current_price - open_price) / open_price) * 100
                        if open_price > 0 else 0.0
                    )

                    # ── reclaim_level: pre-market session high ─────────────────
                    # This is the key structural level momentum traders watch.
                    # Long setups: entry = just above PM high (breakout of PM range)
                    # Short setups: entry = just below PM high (failed breakout)
                    # Fallback chain: PM high → VWAP → current_price
                    pm_high = float(snap.daily_bar.high) if snap.daily_bar else 0.0
                    pm_low  = float(snap.daily_bar.low)  if snap.daily_bar else 0.0
                    if pm_high and pm_high > current_price * 0.9:
                        reclaim_level = pm_high
                    else:
                        reclaim_level = vwap if vwap > 0 else current_price

                    # ── pullback_low: bull invalidation level ──────────────────
                    # If the stock falls back to prev_close the gap-up thesis fails.
                    # Use the highest of (prev_close, ema20) so we don't set stop
                    # below both meaningful supports, but cap at current_price - ATR.
                    bull_anchor = max(
                        prev_close,
                        ema20 if ema20 > 0 else 0.0,
                        pm_low if pm_low > 0 else 0.0,
                    )
                    pullback_low = min(bull_anchor, current_price - atr_val * 0.5)

                    # ── pullback_high: bear invalidation level ─────────────────
                    # Above PM high + ATR buffer = too much upside, short fails.
                    pullback_high = reclaim_level + atr_val

                    # ── Detect breakout vs pullback mode ───────────────────
                    # When price is already at or above the PM high, this is a
                    # breakout — the PM high flips from resistance to support.
                    # Pullback mode: price is below PM high, targeting it.
                    is_breakout = (
                        current_price >= reclaim_level * 0.995  # within 0.5%
                        and reclaim_level > 0
                    )

                    bar_support = tech.get("support", 0.0)
                    bar_resistance = tech.get("resistance", 0.0)
                    prev_day_high = tech.get("prev_day_high", 0.0)
                    prev_day_low  = tech.get("prev_day_low", 0.0)

                    # Max distance for a resistance level to be relevant
                    max_res_dist = atr_val * 2 if atr_val > 0 else current_price * 0.06

                    if is_breakout:
                        # ── BREAKOUT: price at/above PM high ──────────────────
                        # PM high is now support; target is an extension above.
                        # Stop just below the breakout level (PM high).
                        key_support = reclaim_level - atr_val * 0.25
                        # Use daily resistance if available, above price,
                        # and within 2× ATR (ignore stale far-away levels).
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
                        # ── PULLBACK: price below PM high ─────────────────────
                        # key_support = meaningful floor for stop placement.
                        # Collect ALL candidate support levels — use daily
                        # levels (prev_day_low, bar_support from 5-day low)
                        # alongside intraday anchors for more robust S/R.
                        support_candidates = [
                            v for v in [
                                vwap, ema20, pm_low, prev_close,
                                bar_support, prev_day_low,
                            ]
                            if v and 0 < v < current_price
                        ]
                        # Discard candidates more than 2× ATR from price
                        # (too far away to be useful as a nearby support)
                        if atr_val > 0:
                            support_candidates = [
                                v for v in support_candidates
                                if (current_price - v) <= atr_val * 2
                            ]
                        if len(support_candidates) >= 3:
                            support_candidates.sort()
                            key_support = support_candidates[1]  # 2nd-lowest
                        elif support_candidates:
                            key_support = min(support_candidates)
                        else:
                            key_support = current_price - atr_val

                        # key_resistance = nearest ceiling / profit target
                        # Prefer structural daily levels, but ONLY if within
                        # 2× ATR (discard stale levels from prior big moves).
                        resistance_candidates = [reclaim_level]
                        if (prev_day_high > 0
                                and prev_day_high > current_price
                                and (prev_day_high - current_price) <= max_res_dist):
                            resistance_candidates.append(prev_day_high)
                        if (bar_resistance > 0
                                and bar_resistance > current_price
                                and (bar_resistance - current_price) <= max_res_dist):
                            resistance_candidates.append(bar_resistance)
                        # Pick the NEAREST valid candidate above price.
                        # Intraday trades stall at the first ceiling — using
                        # the farthest level creates unreachable TP1s that
                        # inflate R:R on paper but never hit.
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
                            catalyst_score=0.0,  # Will be set by news scoring
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

        logger.info(f"{len(snapshots)} snapshots ready")
        return snapshots
    
    def _get_symbol_bars(self, bars: Any, symbol: str) -> list[Any] | None:
        """Extract the list of bar objects for a symbol from an Alpaca BarSet."""
        try:
            if hasattr(bars, 'data') and symbol in bars.data:
                symbol_bars = bars.data[symbol]
            elif hasattr(bars, '__getitem__'):
                try:
                    symbol_bars = bars[symbol]
                except (KeyError, IndexError):
                    return None
            else:
                return None
            if not symbol_bars or len(symbol_bars) == 0:
                return None
            return list(symbol_bars)
        except Exception:
            return None

    def _get_previous_close(self, bars: Any, symbol: str) -> float | None:
        """Get the most recent daily close price (yesterday, not today)."""
        symbol_bars = self._get_symbol_bars(bars, symbol)
        if not symbol_bars:
            return None
        # Sort by timestamp to guarantee ordering
        symbol_bars = sorted(symbol_bars, key=lambda b: b.timestamp)
        bar = symbol_bars[-2] if len(symbol_bars) >= 2 else symbol_bars[-1]
        return float(bar.close)
    
    def _get_previous_volume(self, bars: Any, symbol: str) -> int | None:
        """Get the previous trading day's volume."""
        symbol_bars = self._get_symbol_bars(bars, symbol)
        if not symbol_bars:
            return None
        symbol_bars = sorted(symbol_bars, key=lambda b: b.timestamp)
        bar = symbol_bars[-2] if len(symbol_bars) >= 2 else symbol_bars[-1]
        return int(bar.volume)
    
    def _get_bars_list(self, bars: Any, symbol: str) -> list[Any]:
        """Extract a list of bar objects for the given symbol."""
        return self._get_symbol_bars(bars, symbol) or []

    def _validate_price_data(
        self, symbol: str, current_price: float, prev_close: float, gap_pct: float, spread_pct: float
    ) -> str | None:
        """
        Validate price data quality from Alpaca's paper trading API.
        Returns warning message if data looks suspicious, None if OK.
        """
        warnings = []
        
        # 1. Check for extreme gaps (>50% for any stock is suspicious without news)
        if abs(gap_pct) > 50:
            warnings.append(f"extreme_gap_{gap_pct:.1f}%")
        
        # 2. Check for unreasonably wide spreads (>5% suggests bad/stale data)
        if spread_pct > 5:
            warnings.append(f"wide_spread_{spread_pct:.1f}%")
        
        # 3. Check for price/gap mismatch (if gap is huge but price seems wrong)
        if abs(gap_pct) > 30 and current_price < 5:
            warnings.append("low_price_high_gap")
        
        # 4. Check for suspiciously round prices (e.g., exactly $10.00 might be placeholder)
        if current_price >= 10 and current_price % 1 == 0:
            warnings.append("round_price")
        
        return ", ".join(warnings) if warnings else None

    # ── Core watchlist (always scanned for catalyst context) ─────────────
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

    def _get_screener_symbols(self) -> list[str]:
        """Fetch today's most-active and biggest-mover symbols from Alpaca.

        Combines three screener feeds:
          - Most active by volume  (top 50)
          - Most active by trades  (top 50)
          - Market movers – gainers + losers (top 50 each)

        Filters out warrants, rights, and units so only common shares remain.
        Returns a de-duplicated list (typically 80-150 symbols).
        """
        symbols: set[str] = set()
        try:
            actives_vol = self.screener.get_most_actives(
                MostActivesRequest(top=50, by="volume")
            )
            for a in actives_vol.most_actives:
                symbols.add(a.symbol)
        except Exception as exc:
            logger.warning(f"screener most-actives (vol) failed: {exc}")

        try:
            actives_trades = self.screener.get_most_actives(
                MostActivesRequest(top=50, by="trades")
            )
            for a in actives_trades.most_actives:
                symbols.add(a.symbol)
        except Exception as exc:
            logger.warning(f"screener most-actives (trades) failed: {exc}")

        try:
            movers = self.screener.get_market_movers(
                MarketMoversRequest(top=50)
            )
            for m in movers.gainers:
                symbols.add(m.symbol)
            for m in movers.losers:
                symbols.add(m.symbol)
        except Exception as exc:
            logger.warning(f"screener market-movers failed: {exc}")

        # Strip warrants / rights / units (e.g. RMSGW, GAB.RT, VLN.WS)
        cleaned = {s for s in symbols if not _JUNK_SUFFIX.search(s) and "." not in s}
        logger.debug(f"screener returned {len(cleaned)} unique symbols")
        return list(cleaned)

    def get_screener_symbols(self) -> list[str]:
        """Public alias for _get_screener_symbols (used by session_runner)."""
        return self._get_screener_symbols()

    def get_tradable_universe(self) -> list[str]:
        """Build today's scan universe by merging dynamic screener data with a
        small core watchlist.  This replaces the old hardcoded 170-symbol list
        so the bot can discover *any* mover in the market."""
        dynamic = self._get_screener_symbols()
        merged = set(dynamic) | set(self._CORE_WATCHLIST)
        universe = sorted(merged)
        logger.info(f"universe: {len(self._CORE_WATCHLIST)} core + "
                    f"{len(dynamic)} screener = {len(universe)} total")
        return universe
