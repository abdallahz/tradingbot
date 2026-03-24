from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest, StockSnapshotRequest
from alpaca.data.timeframe import TimeFrame
import pandas as pd

from tradingbot.analysis.technical_indicators import compute_indicators, interpret_signals
from tradingbot.analysis.pattern_detector import detect_patterns
from tradingbot.models import SymbolSnapshot

# Enable debug logging with environment variable: DEBUG=1
DEBUG = os.environ.get("DEBUG", "").strip() == "1"


class AlpacaClient:
    def __init__(self, api_key: str, api_secret: str, paper: bool = True) -> None:
        self.client = StockHistoricalDataClient(api_key, api_secret)
        self.paper = paper

    def _fetch_batch(self, symbols: list[str]) -> tuple[dict, dict, Any, Any]:
        """
        Fetch quotes, snapshots, daily bars, and intraday bars for a batch of symbols.
        Uses IEX feed (free-tier compatible). Raises on failure so the caller can log and skip.
        """
        quote_request = StockLatestQuoteRequest(symbol_or_symbols=symbols, feed="iex")
        quotes = self.client.get_stock_latest_quote(quote_request)

        snapshot_request = StockSnapshotRequest(symbol_or_symbols=symbols, feed="iex")
        snapshot_data = self.client.get_stock_snapshot(snapshot_request)

        end = datetime.now()
        start = end - timedelta(days=5)
        bars_request = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Day,  # type: ignore[arg-type]
            start=start,
            end=end,
            feed="iex",
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
                    feed="iex",
                )
            )
        except Exception as e:
            print(f"[ALPACA] Intraday bars fetch failed: {e}, falling back to daily bars")
            intraday_bars = bars

        return quotes, snapshot_data, bars, intraday_bars

    def get_premarket_snapshots(self, universe: list[str]) -> list[SymbolSnapshot]:
        """Fetch premarket data for candidate symbols, batched in groups of 50."""
        snapshots: list[SymbolSnapshot] = []
        BATCH_SIZE = 50

        batches = [universe[i:i + BATCH_SIZE] for i in range(0, len(universe), BATCH_SIZE)]
        for batch_idx, batch in enumerate(batches):
            try:
                quotes, snapshot_data, bars, intraday_bars = self._fetch_batch(batch)
            except Exception as e:
                import traceback
                print(f"[ALPACA] ERROR batch {batch_idx + 1}: {type(e).__name__}: {e}")
                traceback.print_exc()
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
                        print(f"[DEBUG] {symbol}: price=${current_price:.2f}, prev=${prev_close:.2f}, gap={gap_pct:.2f}%{warn_str}")
                    
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
                    symbol_bars = self._get_bars_list(intraday_bars, symbol)
                    if not symbol_bars:  # fall back to daily if intraday unavailable
                        symbol_bars = self._get_bars_list(bars, symbol)
                    tech = compute_indicators(symbol_bars)
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
                        print(f"[DEBUG] {symbol} indicators: RSI={rsi:.1f}, MACD={macd:.3f}, ATR={atr:.2f}, signals={tech_signals}")

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
                        print(f"[DEBUG] {symbol} volume: recent={recent_volume:,} ({mode}), avg_per_min={avg_volume_20:,}, spike_ratio={recent_volume/max(1,avg_volume_20):.1f}x")

                    # ATR for volatility sizing
                    atr_val = tech.get("atr", current_price * 0.02)

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

                    # ── key_support: meaningful floor for stop placement ─────
                    # Collect ALL candidate support levels (including bar-data
                    # support) and keep only those below the current price.
                    # Then pick the 2nd-lowest when we have ≥ 3 candidates:
                    #   - Not the absolute floor (min) which the fixed_stop_pct
                    #     cap always overrides, making the level meaningless.
                    #   - Not the tightest (max) which creates stops that trip
                    #     on normal intraday noise.
                    bar_support = tech.get("support", 0.0)
                    support_candidates = [
                        v for v in [vwap, ema20, pm_low, prev_close, bar_support]
                        if v and 0 < v < current_price
                    ]
                    if len(support_candidates) >= 3:
                        support_candidates.sort()
                        key_support = support_candidates[1]  # 2nd-lowest
                    elif support_candidates:
                        key_support = min(support_candidates)
                    else:
                        key_support = current_price - atr_val

                    # ── key_resistance: nearest ceiling / profit target ───────
                    key_resistance = reclaim_level  # PM high is the primary resistance
                    bar_resistance = tech.get("resistance", 0.0)
                    if bar_resistance > 0 and bar_resistance > current_price:
                        key_resistance = max(key_resistance, bar_resistance)
                    # Resistance must be above current price
                    if key_resistance <= current_price:
                        key_resistance = current_price + atr_val

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
                            patterns=patterns,
                            raw_bars=symbol_bars,
                            tech_indicators=tech,
                        )
                    )
                except Exception as e:
                    print(f"[ALPACA] Error processing {symbol}: {type(e).__name__}: {e}")
                    drop_counts["exception"] = drop_counts.get("exception", 0) + 1
                    continue

        print(f"[ALPACA] {len(snapshots)} snapshots ready")
        return snapshots
    
    def _get_previous_close(self, bars: Any, symbol: str) -> float | None:
        """Get the most recent daily close price."""
        try:
            # BarSet uses dict-like access but isn't a dict
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
                
            # Get the second-to-last bar (previous trading day)
            if len(symbol_bars) >= 2:
                return float(symbol_bars[-2].close)
            return float(symbol_bars[-1].close)
        except Exception as e:
            if DEBUG:
                print(f"[DEBUG] _get_previous_close error for {symbol}: {type(e).__name__}: {e}")
            return None
    
    def _get_previous_volume(self, bars: Any, symbol: str) -> int | None:
        """Get the previous trading day's volume."""
        try:
            # BarSet uses dict-like access
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
                
            # Get the second-to-last bar (previous trading day)
            if len(symbol_bars) >= 2:
                return int(symbol_bars[-2].volume)
            return int(symbol_bars[-1].volume)
        except Exception:
            return None
    
    def _get_bars_list(self, bars: Any, symbol: str) -> list[Any]:
        """Extract a list of bar objects for the given symbol."""
        try:
            if hasattr(bars, 'data') and symbol in bars.data:
                return list(bars.data[symbol])
            elif hasattr(bars, '__getitem__'):
                return list(bars[symbol])
        except Exception:
            pass
        return []

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
        if current_price > 0 and current_price == round(current_price) and current_price >= 10:
            decimal_places = len(str(current_price).split('.')[-1]) if '.' in str(current_price) else 0
            if decimal_places == 0:
                warnings.append("round_price")
        
        return ", ".join(warnings) if warnings else None

    def get_tradable_universe(self) -> list[str]:
        """Get list of tradable symbols matching basic criteria."""
        return [
            # ── Mega-cap Tech ──────────────────────────────────────────────
            "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "TSLA",
            "AVGO", "ORCL", "CRM", "AMD", "INTC", "QCOM", "TXN", "MU",
            "AMAT", "LRCX", "KLAC", "MRVL", "SMCI", "ARM", "DELL", "HPQ",
            "IBM", "NOW", "SNOW", "PLTR", "PANW", "CRWD", "ZS", "NET",
            "DDOG", "MDB", "GTLB", "TEAM", "SHOP", "ADBE", "INTU", "ANSS",

            # ── Financials ─────────────────────────────────────────────────
            "JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW",
            "AXP", "V", "MA", "COF", "PYPL", "SQ", "HOOD", "COIN",
            "ICE", "CME", "SPGI", "MCO", "BX", "KKR", "APO",

            # ── Healthcare & Biotech ───────────────────────────────────────
            "LLY", "UNH", "JNJ", "ABBV", "MRK", "PFE", "TMO", "ABT",
            "DHR", "ISRG", "BSX", "MDT", "SYK", "EW", "REGN", "BIIB",
            "GILD", "MRNA", "BNTX", "VRTX", "IDXX", "ZBH", "HUM", "CVS",

            # ── Consumer ──────────────────────────────────────────────────
            "WMT", "COST", "TGT", "HD", "LOW", "NKE", "SBUX",
            "MCD", "YUM", "CMG", "DPZ", "ABNB", "BKNG", "EXPE", "LYFT",
            "UBER", "DASH", "RBLX", "SNAP", "PINS", "MTCH",

            # ── Energy ────────────────────────────────────────────────────
            "XOM", "CVX", "COP", "EOG", "SLB", "MPC", "VLO", "PSX",
            "OXY", "DVN", "HAL", "BKR",

            # ── Industrials & EV ──────────────────────────────────────────
            "GE", "HON", "MMM", "CAT", "DE", "BA", "LMT", "RTX",
            "NOC", "GD", "RIVN", "LCID", "NIO", "LI", "XPEV", "F", "GM",

            # ── Communications & Media ────────────────────────────────────
            "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS", "PARA", "WBD",
            "SPOT", "TTWO", "EA", "ATVI",

            # ── High-momentum / Retail favorites ──────────────────────────
            "GME", "AMC", "BBBY", "SPCE", "SOFI", "IONQ", "QUBT", "RGTI",
            "SOUN", "BBAI", "LUNR", "RKT", "OPEN", "CLOV", "WISH",

            # ── ETFs (for market context) ─────────────────────────────────
            "SPY", "QQQ", "IWM", "DIA", "ARKK", "SOXS", "SOXL", "TQQQ",
        ]
