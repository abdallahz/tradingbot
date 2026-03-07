from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from tradingbot.config import ConfigLoader
from tradingbot.data.alpaca_client import AlpacaClient
from tradingbot.data.mock_data import (
    get_midday_snapshots,
    get_night_universe,
    get_premarket_snapshots,
)
from tradingbot.models import (
    NightResearchResult,
    RiskState,
    Side,
    SymbolSnapshot,
    ThreeOptionWatchlist,
    TradeCard,
    WatchlistRun,
)
from tradingbot.research.news_aggregator import CatalystScorerV2, NewsAggregator
from tradingbot.ranking.ranker import Ranker
from tradingbot.reports.watchlist_report import write_csv, write_markdown, write_three_option_markdown
from tradingbot.research.catalyst_scorer import CatalystScorer
from tradingbot.risk.risk_manager import RiskManager
from tradingbot.scanner.gap_scanner import GapScanner
from tradingbot.signals.pullback_setup import has_valid_setup
from tradingbot.strategy.trade_card import build_trade_card
from tradingbot.analysis.market_conditions import MarketConditionAnalyzer


class SessionRunner:
    def __init__(self, root: Path, use_real_data: bool = False) -> None:
        self.root = root
        self.use_real_data = use_real_data
        self.config = ConfigLoader(root)
        scanner_config = self.config.scanner()
        risk_config = self.config.risk()
        indicator_config = self.config.indicators()
        broker_config = self.config.broker()

        scanner_defaults = scanner_config["scanner"]
        self.midday_config = scanner_config["midday"]
        self.volume_spike_morning = indicator_config["indicators"]["volume_spike_multiplier_morning"]
        self.volume_spike_midday = indicator_config["indicators"]["volume_spike_multiplier_midday"]

        # Initialize data sources
        if use_real_data:
            alpaca_cfg = broker_config["alpaca"]
            self.alpaca_client = AlpacaClient(
                api_key=alpaca_cfg["api_key"],
                api_secret=alpaca_cfg["api_secret"],
                paper=alpaca_cfg["paper"],
            )
            news_cfg = broker_config["news"]
            news_agg = NewsAggregator(
                sec_enabled=news_cfg["sec_filings"],
                earnings_enabled=news_cfg["earnings_calendar"],
                press_releases_enabled=news_cfg["press_releases"],
                max_age_hours=news_cfg["max_age_hours"],
                use_real_sec=news_cfg.get("use_real_sec", False),
                sec_user_agent=news_cfg.get("sec_user_agent", "TradingBot/1.0 (agent@tradingbot.local)"),
                rss_enabled=news_cfg.get("rss_feeds", True),
                social_proxy_enabled=news_cfg.get("social_proxy_enabled", True),
            )
            
            # AI sentiment analyzer (Phase 6 - optional)
            ai_analyzer = None
            if news_cfg.get("ai_sentiment_enabled", False):
                from tradingbot.research.ai_sentiment import AISentimentAnalyzer
                provider = news_cfg.get("ai_sentiment_provider", "openai")
                ai_analyzer = AISentimentAnalyzer(provider=provider)
                if not ai_analyzer.enabled:
                    ai_analyzer = None  # Fallback to keyword if API unavailable
            
            self.catalyst_scorer = CatalystScorerV2(news_agg, ai_sentiment_analyzer=ai_analyzer)
        else:
            self.alpaca_client = None
            self.catalyst_scorer = None
            
        self.fallback_catalyst = CatalystScorer(min_catalyst_score=60)
        self.scanner = GapScanner(
            price_min=scanner_defaults["price_min"],
            price_max=scanner_defaults["price_max"],
            min_gap_pct=scanner_defaults["min_gap_pct"],
            min_premarket_volume=scanner_defaults["min_premarket_volume"],
            min_dollar_volume=scanner_defaults["min_dollar_volume"],
            max_spread_pct=scanner_defaults["max_spread_pct"],
        )
        self.ranker = Ranker(
            min_score=scanner_defaults["min_score"],
            max_candidates=scanner_defaults["max_candidates"],
        )
        self.midday_ranker = Ranker(
            min_score=self.midday_config["min_score"],
            max_candidates=scanner_defaults["max_candidates"],
        )
        risk_defaults = risk_config["risk"]
        self.fixed_stop_pct = risk_defaults["fixed_stop_pct"]
        self.risk_manager = RiskManager(
            max_trades_per_day=risk_defaults["max_trades_per_day"],
            daily_loss_lockout_pct=risk_defaults["daily_loss_lockout_pct"],
            max_consecutive_losses=risk_defaults["max_consecutive_losses"],
        )
        self.market_analyzer = MarketConditionAnalyzer()
        
        # Relaxed scanner for Option 2
        self.relaxed_scanner = GapScanner(
            price_min=scanner_defaults["price_min"],
            price_max=scanner_defaults["price_max"],
            min_gap_pct=1.0,  # Relaxed from 4%
            min_premarket_volume=100_000,  # Relaxed from 500k
            min_dollar_volume=10_000_000,  # Relaxed from 20M
            max_spread_pct=0.5,  # Relaxed from 0.35%
        )

    def run_day(self) -> tuple[WatchlistRun, WatchlistRun]:
        """Legacy method - runs old 2-section output."""
        catalyst_scores: dict[str, float] = {}
        night_universe_str: list[str] = []
        
        if self.use_real_data and self.alpaca_client and self.catalyst_scorer:
            # Real data flow
            universe = self.alpaca_client.get_tradable_universe()
            catalyst_scores = self.catalyst_scorer.score_symbols(universe)
            night_universe_str = [s for s, score in catalyst_scores.items() if score >= 60]
            
            premarket_snapshots = self.alpaca_client.get_premarket_snapshots(night_universe_str)
            # Apply catalyst scores
            for snap in premarket_snapshots:
                snap.catalyst_score = catalyst_scores.get(snap.symbol, 50.0)
            
            premarket = self._run_session(
                snapshots=premarket_snapshots,
                session_tag="morning",
            )
        else:
            # Fallback to mock data
            night_universe = self.fallback_catalyst.filter(get_night_universe())
            night_universe_str = [x.symbol for x in night_universe]
            premarket_snapshots_filtered = [item for item in get_premarket_snapshots() if item.symbol in set(night_universe_str)]
            catalyst_scores = {s.symbol: s.catalyst_score for s in premarket_snapshots_filtered}
            premarket = self._run_session(
                snapshots=premarket_snapshots_filtered,
                session_tag="morning",
            )
        if self.use_real_data and self.alpaca_client:
            # For midday, re-fetch current snapshots
            midday_snapshots = self.alpaca_client.get_premarket_snapshots(night_universe_str)
            for snap in midday_snapshots:
                snap.catalyst_score = catalyst_scores.get(snap.symbol, 50.0)
            midday = self._run_session(
                snapshots=midday_snapshots,
                session_tag="midday",
                stricter=True,
            )
        else:
            midday = self._run_session(
                snapshots=get_midday_snapshots(),
                session_tag="midday",
                stricter=True,
            )
        self._write_outputs(premarket.cards, midday.cards)
        return premarket, midday
    
    def run_day_three_options(self) -> tuple[ThreeOptionWatchlist, ThreeOptionWatchlist]:
        """Run full day with 3 trading options and recommendations."""
        night_universe_str: list[str] = []
        
        if self.use_real_data and self.alpaca_client and self.catalyst_scorer:
            # Real data flow
            universe = self.alpaca_client.get_tradable_universe()
            catalyst_scores = self.catalyst_scorer.score_symbols(universe)
            night_universe_str = [s for s, score in catalyst_scores.items() if score >= 60]
            
            premarket_snapshots = self.alpaca_client.get_premarket_snapshots(night_universe_str)
            # Apply catalyst scores
            for snap in premarket_snapshots:
                snap.catalyst_score = catalyst_scores.get(snap.symbol, 50.0)
            
            morning_results = self._run_three_option_session(
                snapshots=premarket_snapshots,
                catalyst_scores=catalyst_scores,
                session_tag="morning",
            )
        else:
            # Fallback to mock data
            night_universe = self.fallback_catalyst.filter(get_night_universe())
            night_universe_str = [x.symbol for x in night_universe]
            premarket_snapshots = [item for item in get_premarket_snapshots() if item.symbol in set(night_universe_str)]
            catalyst_scores = {s.symbol: s.catalyst_score for s in premarket_snapshots}
            
            morning_results = self._run_three_option_session(
                snapshots=premarket_snapshots,
                catalyst_scores=catalyst_scores,
                session_tag="morning",
            )
        
        # Midday scan
        if self.use_real_data and self.alpaca_client:
            midday_snapshots = self.alpaca_client.get_premarket_snapshots(night_universe_str)
            for snap in midday_snapshots:
                snap.catalyst_score = catalyst_scores.get(snap.symbol, 50.0)
            midday_results = self._run_three_option_session(
                snapshots=midday_snapshots,
                catalyst_scores=catalyst_scores,
                session_tag="midday",
                stricter=True,
            )
        else:
            midday_snapshots = get_midday_snapshots()
            catalyst_scores_midday = {s.symbol: s.catalyst_score for s in midday_snapshots}
            midday_results = self._run_three_option_session(
                snapshots=midday_snapshots,
                catalyst_scores=catalyst_scores_midday,
                session_tag="midday",
                stricter=True,
            )
        
        self._write_three_option_outputs(morning_results, midday_results)
        return morning_results, midday_results

    def _run_session(
        self,
        snapshots,
        session_tag: Literal["morning", "midday"],
        stricter: bool = False,
    ) -> WatchlistRun:
        scan = self.scanner.run(snapshots)
        candidates = scan.candidates
        dropped = list(scan.dropped)

        if stricter:
            filtered = []
            for item in candidates:
                if item.relative_volume < self.midday_config["min_relative_volume"]:
                    dropped.append((item.symbol, "midday_rvol_too_low"))
                    continue
                if item.dollar_volume < self.midday_config["min_dollar_volume"]:
                    dropped.append((item.symbol, "midday_dollar_volume_too_low"))
                    continue
                if item.spread_pct > self.midday_config["max_spread_pct"]:
                    dropped.append((item.symbol, "midday_spread_too_wide"))
                    continue
                filtered.append(item)
            candidates = filtered

        ranked = self.midday_ranker.run(candidates) if stricter else self.ranker.run(candidates)

        cards: list[TradeCard] = []
        risk_state = RiskState()
        for item in ranked:
            if not self.risk_manager.allow_new_trade(risk_state):
                dropped.append((item.snapshot.symbol, "risk_lockout"))
                continue

            symbol = item.snapshot
            can_long = has_valid_setup(symbol, "long", self.volume_spike_midday if stricter else self.volume_spike_morning)
            can_short = has_valid_setup(symbol, "short", self.volume_spike_midday if stricter else self.volume_spike_morning)

            if not can_long and not can_short:
                dropped.append((symbol.symbol, "indicator_confirmation_failed"))
                continue

            side: Side = "long" if can_long else "short"
            card = build_trade_card(
                stock=symbol,
                side=side,
                score=item.score,
                fixed_stop_pct=self.fixed_stop_pct,
                session_tag=cast(Literal["morning", "midday"], session_tag),
            )
            cards.append(card)
            risk_state.trades_taken += 1

        return WatchlistRun(
            generated_at=datetime.utcnow(),
            run_type="midday" if stricter else "morning",
            cards=cards,
            dropped=dropped,
        )

    def _run_three_option_session(
        self,
        snapshots: list[SymbolSnapshot],
        catalyst_scores: dict[str, float],
        session_tag: Literal["morning", "midday"],
        stricter: bool = False,
    ) -> ThreeOptionWatchlist:
        """Run 3 different scan approaches and provide recommendation."""
        
        # Option 1: Night Research - Top catalyst picks with smart money signals
        night_picks = []
        top_catalysts = sorted(catalyst_scores.items(), key=lambda x: x[1], reverse=True)[:10]
        
        # Fetch smart money signals for top catalysts
        smart_money_signals = {}
        try:
            from tradingbot.research.insider_tracking import SmartMoneyTracker
            tracker = SmartMoneyTracker()
            top_symbols = [symbol for symbol, _ in top_catalysts if catalyst_scores.get(symbol, 0) >= 60]
            if top_symbols:
                smart_money_signals = tracker.get_smart_money_signals(top_symbols, days_lookback=7)
                
                # Save smart money signals to file for persistence
                self._save_smart_money_signals(smart_money_signals, session_tag)
        except Exception as e:
            # If smart money tracking fails, continue without it
            import logging
            logging.warning(f"Could not fetch smart money signals: {e}")
        
        for symbol, score in top_catalysts:
            if score >= 60:
                # Get reasons from snapshots if available
                matching = [s for s in snapshots if s.symbol == symbol]
                if matching:
                    snap = matching[0]
                    reasons = []
                    if snap.gap_pct and abs(snap.gap_pct) >= 2.0:
                        reasons.append(f"Gap: {snap.gap_pct:+.1f}%")
                    if snap.relative_volume >= 1.5:
                        reasons.append(f"RelVol: {snap.relative_volume:.1f}x")
                    
                    # Add smart money data if available
                    smart_money_score = 50.0
                    insider_signal = ""
                    institutional_signal = ""
                    
                    if symbol in smart_money_signals:
                        sm_data = smart_money_signals[symbol]
                        smart_money_score = sm_data.get("smart_money_score", 50.0)
                        
                        # Analyze insider trades
                        insider_trades = sm_data.get("insider_trades", [])
                        if insider_trades:
                            purchases = sum(1 for t in insider_trades if "Purchase" in t.transaction_type)
                            sales = sum(1 for t in insider_trades if "Sale" in t.transaction_type)
                            if purchases > sales:
                                insider_signal = "buying"
                            elif sales > purchases:
                                insider_signal = "selling"
                            else:
                                insider_signal = "neutral"
                        
                        # Analyze institutional positions
                        inst_positions = sm_data.get("institutional_positions", [])
                        if inst_positions:
                            increasing = sum(1 for p in inst_positions 
                                           if p.change_from_prior_quarter and p.change_from_prior_quarter > 0)
                            decreasing = sum(1 for p in inst_positions
                                           if p.change_from_prior_quarter and p.change_from_prior_quarter < 0)
                            if increasing > decreasing:
                                institutional_signal = "accumulating"
                            elif decreasing > increasing:
                                institutional_signal = "reducing"
                            else:
                                institutional_signal = "neutral"
                    
                    night_picks.append(NightResearchResult(
                        symbol=symbol,
                        catalyst_score=score,
                        reasons=reasons or ["High catalyst score"],
                        smart_money_score=smart_money_score,
                        insider_signal=insider_signal,
                        institutional_signal=institutional_signal,
                    ))
        
        # Option 2: Relaxed filters scan
        relaxed_scan = self.relaxed_scanner.run(snapshots)
        relaxed_ranked = self.ranker.run(relaxed_scan.candidates)
        relaxed_cards = self._build_cards(
            ranked=relaxed_ranked,
            session_tag=session_tag,
            volume_spike=self.volume_spike_midday if stricter else self.volume_spike_morning,
        )
        
        # Option 3: Strict filters scan
        strict_scan = self.scanner.run(snapshots)
        if stricter:
            # Apply midday filters
            filtered = []
            for item in strict_scan.candidates:
                if (item.relative_volume >= self.midday_config["min_relative_volume"]
                    and item.dollar_volume >= self.midday_config["min_dollar_volume"]
                    and item.spread_pct <= self.midday_config["max_spread_pct"]):
                    filtered.append(item)
            strict_scan.candidates[:] = filtered
        
        strict_ranked = (self.midday_ranker if stricter else self.ranker).run(strict_scan.candidates)
        strict_cards = self._build_cards(
            ranked=strict_ranked,
            session_tag=session_tag,
            volume_spike=self.volume_spike_midday if stricter else self.volume_spike_morning,
        )
        
        # Analyze market conditions and make recommendation
        market_condition = self.market_analyzer.analyze(
            morning_snapshots=snapshots,
            catalyst_scores=catalyst_scores,
        )
        
        # Map analyzer recommendation to our 3 options
        if market_condition.recommended_session == "night_research":
            recommended = "night_research"
        elif market_condition.volatility_level == "high":
            recommended = "strict_filters"
        elif market_condition.volatility_level == "low":
            recommended = "night_research" if len(night_picks) >= 3 else "relaxed_filters"
        else:
            # Medium volatility - use strict if we have signals, otherwise relaxed
            recommended = "strict_filters" if len(strict_cards) >= 2 else "relaxed_filters"
        
        return ThreeOptionWatchlist(
            generated_at=datetime.utcnow(),
            run_type=session_tag,
            night_research_picks=night_picks,
            relaxed_filter_cards=relaxed_cards,
            strict_filter_cards=strict_cards,
            recommended_option=recommended,
            recommendation_reason=market_condition.recommendation_reason,
            market_volatility=market_condition.volatility_level,
            average_gap=market_condition.average_gap,
            gappers_count=market_condition.gappers_count,
        )
    
    def _build_cards(
        self,
        ranked: list,
        session_tag: Literal["morning", "midday"],
        volume_spike: float,
    ) -> list[TradeCard]:
        """Build trade cards from ranked candidates."""
        cards: list[TradeCard] = []
        risk_state = RiskState()
        
        for item in ranked:
            if not self.risk_manager.allow_new_trade(risk_state):
                break
            
            symbol = item.snapshot
            can_long = has_valid_setup(symbol, "long", volume_spike)
            can_short = has_valid_setup(symbol, "short", volume_spike)
            
            if not can_long and not can_short:
                continue
            
            side: Side = "long" if can_long else "short"
            card = build_trade_card(
                stock=symbol,
                side=side,
                score=item.score,
                fixed_stop_pct=self.fixed_stop_pct,
                session_tag=cast(Literal["morning", "midday"], session_tag),
            )
            cards.append(card)
            risk_state.trades_taken += 1
        
        return cards

    def _write_outputs(self, morning: list[TradeCard], midday: list[TradeCard]) -> None:
        output_dir = self.root / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        write_csv(output_dir / "morning_watchlist.csv", morning)
        write_csv(output_dir / "midday_watchlist.csv", midday)
        write_markdown(output_dir / "daily_playbook.md", morning, midday)
    
    def _write_three_option_outputs(self, morning: ThreeOptionWatchlist, midday: ThreeOptionWatchlist) -> None:
        output_dir = self.root / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Write legacy CSV files for compatibility
        write_csv(output_dir / "morning_watchlist.csv", morning.strict_filter_cards)
        write_csv(output_dir / "midday_watchlist.csv", midday.strict_filter_cards)
        
        # Write new 3-option markdown
        write_three_option_markdown(output_dir / "daily_playbook.md", morning, midday)

    def run_news_research(self) -> dict[str, float]:
        """
        Run night/morning news research only.
        
        Returns:
            dict[str, float]: Catalyst scores for each symbol
        """
        if self.use_real_data and self.alpaca_client and self.catalyst_scorer:
            # Real news sources
            universe = self.alpaca_client.get_tradable_universe()
            catalyst_scores = self.catalyst_scorer.score_symbols(universe)

            # Persist latest social proxy signals (if available)
            social_signals = self.catalyst_scorer.news_aggregator.get_latest_social_signals()
            if social_signals:
                self._save_social_proxy_signals(social_signals, "news")

            return catalyst_scores
        else:
            # Fallback to mock catalyst scoring
            night_universe = self.fallback_catalyst.filter(get_night_universe())
            return {item.symbol: item.catalyst_score for item in night_universe}
    
    def run_single_session(
        self,
        session_type: Literal["morning", "midday", "close"],
        catalyst_scores: dict[str, float],
    ) -> ThreeOptionWatchlist:
        """
        Run a single scan session with pre-computed catalyst scores.
        
        Args:
            session_type: Which session to run ("morning", "midday", or "close")
            catalyst_scores: Pre-computed catalyst scores from run_news_research()
            
        Returns:
            ThreeOptionWatchlist with all 3 trading options
        """
        # Filter universe to symbols with score >= 60
        night_universe_str = [s for s, score in catalyst_scores.items() if score >= 60]
        
        # Determine if this session uses stricter filters
        stricter = session_type in ["midday", "close"]
        
        # Determine session_tag for trade cards (map "close" to "midday" for now)
        session_tag: Literal["morning", "midday"] = "midday" if stricter else "morning"
        
        # Fetch market snapshots
        if self.use_real_data and self.alpaca_client:
            snapshots = self.alpaca_client.get_premarket_snapshots(night_universe_str)
            # Apply catalyst scores
            for snap in snapshots:
                snap.catalyst_score = catalyst_scores.get(snap.symbol, 50.0)
        else:
            # Mock data
            if session_type == "morning":
                snapshots = [item for item in get_premarket_snapshots() if item.symbol in set(night_universe_str)]
            else:
                snapshots = [item for item in get_midday_snapshots() if item.symbol in set(night_universe_str)]
            # Apply catalyst scores
            for snap in snapshots:
                snap.catalyst_score = catalyst_scores.get(snap.symbol, 50.0)
        
        # Run the 3-option session
        results = self._run_three_option_session(
            snapshots=snapshots,
            catalyst_scores=catalyst_scores,
            session_tag=session_tag,
            stricter=stricter,
        )
        
        return results
    
    def _write_single_session_output(self, results: ThreeOptionWatchlist, session_name: str) -> None:
        """Write outputs for a single session."""
        output_dir = self.root / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Write CSV for strict filter cards
        write_csv(output_dir / f"{session_name}_watchlist.csv", results.strict_filter_cards)
        
        # Write markdown playbook
        from tradingbot.reports.watchlist_report import _format_three_option_section
        
        lines = [
            f"# {session_name.replace('_', ' ').title()} Trading Playbook",
            f"Generated: {datetime.utcnow().isoformat()}Z",
            "",
            "---",
            ""
        ]
        lines.extend(_format_three_option_section(session_name.replace('_', ' ').title(), results))
        
        playbook_path = output_dir / f"{session_name}_playbook.md"
        playbook_path.write_text("\n".join(lines), encoding="utf-8")
    
    def _save_smart_money_signals(self, signals: dict, session_tag: str) -> None:
        """Save smart money signals to JSON file for historical reference."""
        import json
        
        output_dir = self.root / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Convert dataclass objects to dictionaries
        serializable_signals = {}
        for symbol, data in signals.items():
            serializable_data = {
                "symbol": symbol,
                "smart_money_score": data.get("smart_money_score", 50.0),
                "insider_trades": [],
                "institutional_positions": [],
                "congressional_trades": [],
            }
            
            # Convert insider trades
            for trade in data.get("insider_trades", []):
                serializable_data["insider_trades"].append({
                    "symbol": trade.symbol,
                    "insider_name": trade.insider_name,
                    "insider_title": trade.insider_title,
                    "transaction_date": trade.transaction_date.isoformat() if trade.transaction_date else None,
                    "transaction_type": trade.transaction_type,
                    "shares": trade.shares,
                    "price_per_share": trade.price_per_share,
                    "total_value": trade.total_value,
                    "is_significant": trade.is_significant,
                })
            
            # Convert institutional positions
            for pos in data.get("institutional_positions", []):
                serializable_data["institutional_positions"].append({
                    "symbol": pos.symbol,
                    "institution_name": pos.institution_name,
                    "shares_held": pos.shares_held,
                    "market_value": pos.market_value,
                    "percent_of_portfolio": pos.percent_of_portfolio,
                    "filing_date": pos.filing_date.isoformat() if pos.filing_date else None,
                    "change_from_prior_quarter": pos.change_from_prior_quarter,
                    "percent_change": pos.percent_change,
                })
            
            # Convert congressional trades
            for trade in data.get("congressional_trades", []):
                serializable_data["congressional_trades"].append({
                    "symbol": trade.symbol,
                    "politician_name": trade.politician_name,
                    "position": trade.position,
                    "party": trade.party,
                    "transaction_date": trade.transaction_date.isoformat() if trade.transaction_date else None,
                    "transaction_type": trade.transaction_type,
                    "amount_range": trade.amount_range,
                    "estimated_value": trade.estimated_value,
                })
            
            serializable_signals[symbol] = serializable_data
        
        # Save to file with timestamp
        signals_path = output_dir / f"smart_money_signals_{session_tag}.json"
        with signals_path.open("w", encoding="utf-8") as f:
            json.dump({
                "generated_at": datetime.utcnow().isoformat() + "Z",
                "session_tag": session_tag,
                "signals": serializable_signals,
            }, f, indent=2)
        
        import logging
        logging.info(f"Saved smart money signals to {signals_path}")

    def _save_social_proxy_signals(self, signals: dict[str, dict[str, float | int | str]], session_tag: str) -> None:
        """Save social proxy signals to JSON file for historical reference."""
        import json

        output_dir = self.root / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)

        signals_path = output_dir / f"social_proxy_signals_{session_tag}.json"
        with signals_path.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "generated_at": datetime.utcnow().isoformat() + "Z",
                    "session_tag": session_tag,
                    "signals": signals,
                },
                f,
                indent=2,
            )

        import logging
        logging.info(f"Saved social proxy signals to {signals_path}")
