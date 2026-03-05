from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest, StockSnapshotRequest
from alpaca.data.timeframe import TimeFrame
import pandas as pd

from tradingbot.models import SymbolSnapshot

# Enable debug logging with environment variable: DEBUG=1
DEBUG = os.environ.get("DEBUG", "").strip() == "1"


class AlpacaClient:
    def __init__(self, api_key: str, api_secret: str, paper: bool = True) -> None:
        self.client = StockHistoricalDataClient(api_key, api_secret)
        self.paper = paper

    def get_premarket_snapshots(self, universe: list[str]) -> list[SymbolSnapshot]:
        """Fetch premarket data for candidate symbols."""
        snapshots: list[SymbolSnapshot] = []
        
        if DEBUG:
            print(f"[DEBUG] Fetching data for {len(universe)} symbols: {universe[:5]}...")
        
        try:
            # Get latest quotes
            quote_request = StockLatestQuoteRequest(symbol_or_symbols=universe)
            quotes = self.client.get_stock_latest_quote(quote_request)
            if DEBUG:
                print(f"[DEBUG] Got {len(quotes) if quotes else 0} quotes")
            
            # Get snapshot data with VWAP and volume
            snapshot_request = StockSnapshotRequest(symbol_or_symbols=universe)
            snapshot_data = self.client.get_stock_snapshot(snapshot_request)
            if DEBUG:
                print(f"[DEBUG] Got {len(snapshot_data) if snapshot_data else 0} snapshots")
            
            # Get 1-day bars for gap calculation
            end = datetime.now()
            start = end - timedelta(days=5)
            bars_request = StockBarsRequest(
                symbol_or_symbols=universe,
                timeframe=TimeFrame.Day,  # type: ignore[arg-type]
                start=start,
                end=end
            )
            bars = self.client.get_stock_bars(bars_request)
            if DEBUG:
                print(f"[DEBUG] Got bars data")
            
            for symbol in universe:
                try:
                    if symbol not in snapshot_data or symbol not in quotes:
                        if DEBUG:
                            print(f"[DEBUG] {symbol}: Missing in snapshot_data or quotes")
                        continue
                    
                    snap = snapshot_data[symbol]
                    quote = quotes[symbol]
                    
                    # Calculate gap from previous close
                    prev_close = self._get_previous_close(bars, symbol)
                    if prev_close is None or prev_close == 0:
                        if DEBUG:
                            print(f"[DEBUG] {symbol}: No previous close (prev_close={prev_close})")
                        continue
                    
                    # Get current price safely
                    if snap.latest_trade and hasattr(snap.latest_trade, 'price') and snap.latest_trade.price:
                        current_price = float(snap.latest_trade.price)
                    else:
                        current_price = float(quote.ask_price) if quote.ask_price else 0.0
                    
                    if not current_price:
                        continue
                    
                    gap_pct = ((current_price - prev_close) / prev_close) * 100
                    if DEBUG:
                        print(f"[DEBUG] {symbol}: price=${current_price:.2f}, prev=${prev_close:.2f}, gap={gap_pct:.2f}%")                    
                    # Calculate spread
                    bid = float(quote.bid_price) if quote.bid_price else current_price
                    ask = float(quote.ask_price) if quote.ask_price else current_price
                    spread_pct = ((ask - bid) / current_price) * 100 if current_price > 0 else 0
                    
                    # Get volume metrics
                    daily_volume = snap.daily_bar.volume if snap.daily_bar else 0
                    dollar_volume = daily_volume * current_price
                    
                    # Compute relative volume (daily vs previous day)
                    # Try to get previous day volume from bars
                    prev_volume = self._get_previous_volume(bars, symbol)
                    relative_volume = daily_volume / prev_volume if prev_volume and prev_volume > 0 else 1.0
                    
                    # Compute technical indicators
                    ema9, ema20, vwap = self._compute_indicators(bars, symbol, current_price)
                    
                    # Get recent volume for volume spike detection
                    recent_volume = snap.minute_bar.volume if snap.minute_bar else 0
                    avg_volume_20 = daily_volume // 390 if daily_volume > 0 else 1  # Rough estimate
                    
                    # Pullback levels (will be computed from intraday bars)
                    pullback_low = current_price * 0.995
                    reclaim_level = current_price
                    pullback_high = current_price * 1.005
                    
                    snapshots.append(
                        SymbolSnapshot(
                            symbol=symbol,
                            price=current_price,
                            gap_pct=gap_pct,
                            premarket_volume=int(daily_volume),
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
                        )
                    )
                except Exception as e:
                    if DEBUG:
                        print(f"[DEBUG] Error processing {symbol}: {e}")
                    continue
                    
        except Exception as e:
            if DEBUG:
                print(f"[DEBUG] Error fetching Alpaca data: {type(e).__name__}: {e}")
                import traceback
                traceback.print_exc()
            
        if DEBUG:
            print(f"[DEBUG] Returning {len(snapshots)} snapshots")
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
    
    def _compute_indicators(self, bars: Any, symbol: str, current_price: float) -> tuple[float, float, float]:
        """Compute EMA9, EMA20, and VWAP from historical bars."""
        try:
            # Access BarSet properly
            if hasattr(bars, 'data') and symbol in bars.data:
                symbol_bars = bars.data[symbol]
            elif hasattr(bars, '__getitem__'):
                try:
                    symbol_bars = bars[symbol]
                except (KeyError, IndexError):
                    return current_price, current_price * 0.99, current_price
            else:
                return current_price, current_price * 0.99, current_price
            
            if not symbol_bars or len(symbol_bars) == 0:
                return current_price, current_price * 0.99, current_price
            
            # Convert to pandas for EMA calculation
            closes = [float(bar.close) for bar in symbol_bars]
            df = pd.DataFrame({'close': closes})
            
            ema9 = df['close'].ewm(span=9, adjust=False).mean().iloc[-1] if len(closes) >= 9 else current_price
            ema20 = df['close'].ewm(span=20, adjust=False).mean().iloc[-1] if len(closes) >= 20 else current_price * 0.99
            
            # VWAP approximation (simplified)
            vwap = current_price
            
            return float(ema9), float(ema20), float(vwap)
        except Exception:
            return current_price, current_price * 0.99, current_price

    def get_tradable_universe(self) -> list[str]:
        """Get list of tradable symbols matching basic criteria."""
        # For MVP, return a curated list of high-volume stocks
        # In production, this would query Alpaca's assets API
        return [
            "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META",
            "AMD", "NFLX", "DIS", "BABA", "PYPL", "INTC", "QCOM",
            "PLTR", "RIVN", "LCID", "SOFI", "NIO", "BBBY", "GME",
            "AMC", "SPCE", "COIN", "RBLX", "UBER", "LYFT", "SNAP"
        ]
