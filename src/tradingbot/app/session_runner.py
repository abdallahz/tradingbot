from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Literal

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
from tradingbot.ranking.ranker import CatalystWeightedRanker, Ranker
from tradingbot.reports.watchlist_report import write_csv, write_markdown, write_three_option_markdown
from tradingbot.research.catalyst_scorer import CatalystScorer
from tradingbot.risk.risk_manager import RiskManager
from tradingbot.scanner.gap_scanner import GapScanner
from tradingbot.signals.pullback_setup import has_valid_setup
from tradingbot.strategy.trade_card import build_trade_card
from tradingbot.analysis.chart_generator import generate_chart
from tradingbot.analysis.pattern_detector import score_confluence, MIN_CONFLUENCE_SCORE
from tradingbot.analysis.market_conditions import MarketConditionAnalyzer
from tradingbot.analysis.ai_trade_validator import AITradeValidator
from tradingbot.notifications.telegram_notifier import TelegramNotifier
from tradingbot.web.alert_store import card_to_dict, save_alert, get_today_alerted_symbols


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
        self.relaxed_ranker = CatalystWeightedRanker(
            min_score=25,  # Very permissive — Option 2 should always show results
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
        # AI Trade Validator (paid LLM call per card) — disabled by default
        ai_validation_enabled = False
        if use_real_data:
            ai_validation_enabled = broker_config.get("news", {}).get("ai_trade_validation_enabled", False)
        self.ai_validator = AITradeValidator() if ai_validation_enabled else None
        self.notifier = TelegramNotifier.from_env()
        self._alerts_sent_count: int = 0
        
        # Relaxed scanner for Option 2
        o2_cfg = scanner_config.get("o2_relaxed", {})
        self.relaxed_scanner = GapScanner(
            price_min=o2_cfg.get("price_min", scanner_defaults["price_min"]),
            price_max=o2_cfg.get("price_max", scanner_defaults["price_max"]),
            min_gap_pct=o2_cfg.get("min_gap_pct", 0.0),
            min_premarket_volume=o2_cfg.get("min_premarket_volume", 0),
            min_dollar_volume=o2_cfg.get("min_dollar_volume", 0),
            max_spread_pct=o2_cfg.get("max_spread_pct", 5.0),
        )

    def run_day(self) -> tuple[WatchlistRun, WatchlistRun]:
        """Legacy method - runs old 2-section output."""
        catalyst_scores: dict[str, float] = {}
        night_universe_str: list[str] = []
        
        if self.use_real_data and self.alpaca_client and self.catalyst_scorer:
            # Real data flow
            universe = self.alpaca_client.get_tradable_universe()
            catalyst_scores = self.catalyst_scorer.score_symbols(universe)
            night_universe_str = [s for s, score in catalyst_scores.items() if score >= 40]
            if not night_universe_str:
                night_universe_str = [s for s, _ in sorted(catalyst_scores.items(), key=lambda x: x[1], reverse=True)[:50]]
            
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
            night_universe_str = [s for s, score in catalyst_scores.items() if score >= 40]
            if not night_universe_str:
                night_universe_str = [s for s, _ in sorted(catalyst_scores.items(), key=lambda x: x[1], reverse=True)[:50]]
            
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
        session_tag: Literal["morning", "midday", "close"],
        stricter: bool = False,
    ) -> WatchlistRun:
        """Legacy full-day session runner. Delegates card-building to _build_cards."""
        scan = self.scanner.run(snapshots)
        candidates = scan.candidates
        dropped: list[tuple[str, str]] = list(scan.dropped)

        if stricter:
            filtered = []
            for item in candidates:
                if item.relative_volume < self.midday_config["min_relative_volume"]:
                    dropped.append((item.symbol, "midday_rvol_too_low"))
                elif item.dollar_volume < self.midday_config["min_dollar_volume"]:
                    dropped.append((item.symbol, "midday_dollar_volume_too_low"))
                elif item.spread_pct > self.midday_config["max_spread_pct"]:
                    dropped.append((item.symbol, "midday_spread_too_wide"))
                else:
                    filtered.append(item)
            candidates = filtered

        volume_spike = self.volume_spike_midday if stricter else self.volume_spike_morning
        ranked = (self.midday_ranker if stricter else self.ranker).run(candidates)
        cards = self._build_cards(ranked, session_tag, volume_spike, dropped=dropped)

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
        session_tag: Literal["morning", "midday", "close"],
        stricter: bool = False,
    ) -> ThreeOptionWatchlist:
        """Run 3 different scan approaches and provide recommendation."""

        # Option 1: Night Research — top catalyst picks with smart money overlay
        night_picks = self._get_night_research_picks(snapshots, catalyst_scores, session_tag)

        # Option 2: Relaxed filters scan — also relaxes indicator confirmation
        relaxed_scan = self.relaxed_scanner.run(snapshots)
        relaxed_ranked = self.relaxed_ranker.run(relaxed_scan.candidates)
        o2_dropped: list[tuple[str, str]] = []
        relaxed_cards = self._build_cards(
            ranked=relaxed_ranked,
            session_tag=session_tag,
            volume_spike=self.volume_spike_midday if stricter else self.volume_spike_morning,
            relaxed=True,
            dropped=o2_dropped,
        )

        # Option 3: Strict filters scan
        strict_scan = self.scanner.run(snapshots)
        if stricter:
            cfg = self.midday_config
            strict_scan.candidates = [
                c for c in strict_scan.candidates
                if c.relative_volume >= cfg["min_relative_volume"]
                and c.dollar_volume >= cfg["min_dollar_volume"]
                and c.spread_pct <= cfg["max_spread_pct"]
            ]

        strict_ranked = (self.midday_ranker if stricter else self.ranker).run(strict_scan.candidates)
        o3_dropped: list[tuple[str, str]] = []
        strict_cards = self._build_cards(
            ranked=strict_ranked,
            session_tag=session_tag,
            volume_spike=self.volume_spike_midday if stricter else self.volume_spike_morning,
            dropped=o3_dropped,
        )

        print(f"[{session_tag.upper()}] snapshots={len(snapshots)} O1={len(night_picks)} O2={len(relaxed_cards)} O3={len(strict_cards)}")
        if o2_dropped:
            drop_summary = {}
            for _, reason in o2_dropped:
                key = reason.split(":")[0]
                drop_summary[key] = drop_summary.get(key, 0) + 1
            print(f"[{session_tag.upper()}] O2 drops: {drop_summary}")
        if o3_dropped:
            drop_summary = {}
            for _, reason in o3_dropped:
                key = reason.split(":")[0]
                drop_summary[key] = drop_summary.get(key, 0) + 1
            print(f"[{session_tag.upper()}] O3 drops: {drop_summary}")
        
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
        session_tag: Literal["morning", "midday", "close"],
        volume_spike: float,
        dropped: list[tuple[str, str]] | None = None,
        relaxed: bool = False,
    ) -> list[TradeCard]:
        """Build trade cards from ranked candidates.

        Includes dedup logic: if a symbol was already alerted today,
        only re-alert if the current price has pulled back at least 50%
        closer to key support (i.e. a materially better entry).

        If *relaxed* is True (Option 2), indicator confirmation is skipped
        for stocks with catalyst_score >= 55 and the confluence floor is
        lowered to 0 (only block strong opposing signals).
        """
        cards: list[TradeCard] = []

        # Load today's already-alerted symbols for dedup
        try:
            already_alerted = get_today_alerted_symbols()
        except Exception:
            already_alerted = {}

        # Pre-seed risk state with today's alert count so the daily cap
        # (max_trades_per_day) persists across 30-minute scan cycles.
        risk_state = RiskState(trades_taken=len(already_alerted))

        for item in ranked:
            if not self.risk_manager.allow_new_trade(risk_state):
                if dropped is not None:
                    dropped.append((item.snapshot.symbol, "risk_lockout"))
                break

            symbol = item.snapshot

            # ── Dedup check: skip if already alerted unless pullback ──
            prev_entry = already_alerted.get(symbol.symbol)
            if prev_entry is not None and prev_entry > 0:
                support = symbol.key_support if symbol.key_support > 0 else symbol.price * 0.98
                distance_before = prev_entry - support
                distance_now = symbol.price - support
                if distance_before > 0 and distance_now > 0:
                    # Only re-alert if price moved ≥50% closer to support
                    pullback_pct = 1.0 - (distance_now / distance_before)
                    if pullback_pct < 0.50:
                        if dropped is not None:
                            dropped.append((symbol.symbol, f"dedup:pullback_only_{pullback_pct:.0%}"))
                        continue

            can_long = has_valid_setup(symbol, "long", volume_spike)
            can_short = has_valid_setup(symbol, "short", volume_spike)

            # In relaxed mode, allow high-catalyst stocks through even
            # without full indicator confirmation (pre-market data is sparse).
            if not can_long and not can_short:
                if relaxed and symbol.catalyst_score >= 55:
                    # Catalyst-driven bypass: default to long if gap >= 0
                    can_long = symbol.gap_pct >= 0
                    can_short = not can_long
                    logging.info(f"[RELAXED] {symbol.symbol} bypassed indicator check (catalyst={symbol.catalyst_score:.0f})")
                else:
                    if dropped is not None:
                        dropped.append((symbol.symbol, "indicator_confirmation_failed"))
                    logging.info(f"[DROP] {symbol.symbol}: indicator_confirmation_failed (vol_spike={volume_spike}, catalyst={symbol.catalyst_score:.0f})")
                    continue

            # Prefer direction that matches the gap; fall back to whichever side is valid
            if symbol.gap_pct >= 0:
                side: Side = "long" if can_long else "short"
            else:
                side = "short" if can_short else "long"
            card = build_trade_card(
                stock=symbol,
                side=side,
                score=item.score,
                fixed_stop_pct=self.fixed_stop_pct,
                session_tag=session_tag,
            )
            if card is None:
                if dropped is not None:
                    dropped.append((symbol.symbol, "rr_below_floor"))
                logging.info(f"[DROP] {symbol.symbol}: rr_below_floor (support={symbol.key_support:.2f}, resistance={symbol.key_resistance:.2f}, price={symbol.price:.2f})")
                continue
            card.patterns = list(symbol.patterns)
            confluence = score_confluence(card.patterns, side)
            # Block cards with weak or opposing signals
            # Relaxed mode: only block if strong opposing signal (< 0)
            # Strict mode: require MIN_CONFLUENCE_SCORE (10)
            confluence_floor = 0 if relaxed else MIN_CONFLUENCE_SCORE
            if confluence < confluence_floor:
                if dropped is not None:
                    dropped.append((symbol.symbol, f"low_confluence:{confluence:.0f}"))
                logging.info(f"[DROP] {symbol.symbol}: low_confluence={confluence:.0f} (floor={confluence_floor}, patterns={symbol.patterns})")
                continue
            # Blend confluence bonus into the ranker score (30% weight, cap 100)
            card.score = round(min(100.0, card.score * 0.7 + confluence * 0.3), 2)

            # ── AI Trade Validation (LLM "second opinion") ──
            # Disabled by default (paid API). Enable via broker.yaml: ai_trade_validation_enabled: true
            if self.ai_validator is not None:
                validation = self.ai_validator.validate(
                    card=card,
                    snapshot=symbol,
                    catalyst_score=symbol.catalyst_score,
                )
                card.ai_confidence = validation.confidence
                card.ai_reasoning = validation.reasoning
                card.ai_concerns = list(validation.concerns)
                if not validation.approved:
                    if dropped is not None:
                        dropped.append((symbol.symbol, f"ai_rejected:confidence={validation.confidence}"))
                    continue

            chart_path = generate_chart(
                symbol=symbol.symbol,
                bars_data=symbol.raw_bars,
                indicators=symbol.tech_indicators,
                trade_card=card,
            )
            if chart_path:
                card.chart_path = chart_path
            cards.append(card)
            risk_state.trades_taken += 1
            self.notifier.send_trade_alert(card)
            save_alert(card_to_dict(card))
            self._alerts_sent_count += 1

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
    
    def _fetch_snapshots(
        self,
        session_type: Literal["morning", "midday", "close"],
        universe_str: list[str],
        catalyst_scores: dict[str, float],
    ) -> list[SymbolSnapshot]:
        """Fetch market snapshots and annotate each with its catalyst score."""
        universe_set = set(universe_str)
        if self.use_real_data and self.alpaca_client:
            # Sort by catalyst score and cap at 50 to avoid Alpaca batch-size limits
            sorted_universe = sorted(universe_str, key=lambda s: catalyst_scores.get(s, 0), reverse=True)
            fetch_universe = sorted_universe[:50]
            snapshots = self.alpaca_client.get_premarket_snapshots(fetch_universe)
        elif session_type == "morning":
            snapshots = [s for s in get_premarket_snapshots() if s.symbol in universe_set]
        else:
            snapshots = [s for s in get_midday_snapshots() if s.symbol in universe_set]
        for snap in snapshots:
            snap.catalyst_score = catalyst_scores.get(snap.symbol, 50.0)
        return snapshots

    def _get_night_research_picks(
        self,
        snapshots: list[SymbolSnapshot],
        catalyst_scores: dict[str, float],
        session_tag: str,
    ) -> list[NightResearchResult]:
        """Build Option-1 night-research picks with optional smart money signals."""
        top_catalysts = sorted(catalyst_scores.items(), key=lambda x: x[1], reverse=True)[:10]
        snap_by_symbol = {s.symbol: s for s in snapshots}

        # Attempt to enrich with smart money data (gracefully degraded if unavailable)
        smart_money_signals: dict = {}
        try:
            from tradingbot.research.insider_tracking import SmartMoneyTracker
            tracker = SmartMoneyTracker()
            top_symbols = [sym for sym, score in top_catalysts if score >= 40]
            if top_symbols:
                smart_money_signals = tracker.get_smart_money_signals(top_symbols, days_lookback=7)
                self._save_smart_money_signals(smart_money_signals, session_tag)
        except Exception as exc:
            logging.warning(f"Could not fetch smart money signals: {exc}")

        picks: list[NightResearchResult] = []
        for symbol, score in top_catalysts:
            if score < 40:
                continue
            snap = snap_by_symbol.get(symbol)
            if snap is None:
                continue

            reasons: list[str] = []
            if snap.gap_pct and abs(snap.gap_pct) >= 2.0:
                reasons.append(f"Gap: {snap.gap_pct:+.1f}%")
            if snap.relative_volume >= 1.5:
                reasons.append(f"RelVol: {snap.relative_volume:.1f}x")

            sm_score, insider_signal, institutional_signal = 50.0, "", ""
            if symbol in smart_money_signals:
                sm = smart_money_signals[symbol]
                sm_score = sm.get("smart_money_score", 50.0)

                trades = sm.get("insider_trades", [])
                if trades:
                    buys = sum(1 for t in trades if "Purchase" in t.transaction_type)
                    sells = sum(1 for t in trades if "Sale" in t.transaction_type)
                    insider_signal = "buying" if buys > sells else "selling" if sells > buys else "neutral"

                positions = sm.get("institutional_positions", [])
                if positions:
                    inc = sum(1 for p in positions if p.change_from_prior_quarter and p.change_from_prior_quarter > 0)
                    dec = sum(1 for p in positions if p.change_from_prior_quarter and p.change_from_prior_quarter < 0)
                    institutional_signal = "accumulating" if inc > dec else "reducing" if dec > inc else "neutral"

            picks.append(NightResearchResult(
                symbol=symbol,
                catalyst_score=score,
                reasons=reasons or ["High catalyst score"],
                smart_money_score=sm_score,
                insider_signal=insider_signal,
                institutional_signal=institutional_signal,
            ))

        return picks

    def run_single_session(
        self,
        session_type: Literal["morning", "midday", "close"],
        catalyst_scores: dict[str, float],
    ) -> tuple[ThreeOptionWatchlist, int]:
        # Build universe: prefer symbols with catalyst score >= 40.
        # Fall back to top-50 by score so we always have something to scan.
        universe_str = [s for s, sc in catalyst_scores.items() if sc >= 40]
        if not universe_str:
            sorted_scores = sorted(catalyst_scores.items(), key=lambda x: x[1], reverse=True)
            universe_str = [s for s, _ in sorted_scores[:50]]
        stricter = session_type in ["midday", "close"]
        session_tag = session_type  # "morning", "midday", or "close"
        snapshots = self._fetch_snapshots(session_type, universe_str, catalyst_scores)
        self._alerts_sent_count = 0
        results = self._run_three_option_session(snapshots, catalyst_scores, session_tag, stricter)
        return results, self._alerts_sent_count
    
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
        
        logging.info(f"Saved smart money signals to {signals_path}")

    def _save_social_proxy_signals(self, signals: dict[str, dict[str, float | int | str]], session_tag: str) -> None:
        """Save social proxy signals to JSON file for historical reference."""
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

        logging.info(f"Saved social proxy signals to {signals_path}")
