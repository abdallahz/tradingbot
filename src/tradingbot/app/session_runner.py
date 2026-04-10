from __future__ import annotations

import json
import logging
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from tradingbot.config import ConfigLoader
from tradingbot.data import create_data_client, DataClient
from tradingbot.data.alpaca_client import AlpacaClient
from tradingbot.data.mock_data import (
    get_midday_snapshots,
    get_night_universe,
    get_premarket_snapshots,
)
from tradingbot.models import (
    NightResearchResult,
    RiskState,
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
from tradingbot.scanner.momentum_scanner import MomentumScanner
from tradingbot.signals.pullback_setup import has_valid_setup
from tradingbot.signals.pullback_reentry import evaluate_pullback_reentry
from tradingbot.strategy.trade_card import build_trade_card
from tradingbot.analysis.chart_generator import generate_chart
from tradingbot.analysis.pattern_detector import score_confluence, MIN_CONFLUENCE_SCORE
from tradingbot.analysis.market_conditions import MarketConditionAnalyzer, MarketCondition
from tradingbot.analysis.market_guard import MarketGuard, MarketHealth
from tradingbot.analysis.ai_trade_validator import AITradeValidator
from tradingbot.analysis.confluence_engine import evaluate_confluence, should_fire_alert
from tradingbot.analysis.volume_quality import classify_volume_profile, is_move_exhausted
from tradingbot.analysis.institutional_alert import (
    build_institutional_context,
    format_institutional_alert,
)
from tradingbot.execution.execution_manager import create_execution_manager
from tradingbot.notifications.telegram_notifier import TelegramNotifier
from tradingbot.web.alert_store import card_to_dict, save_alert, get_today_alerted_symbols
from tradingbot.data.etf_metadata import is_etf, get_etf_family, get_leverage_factor

# Maximum number of ETF alerts per scan pass (individual stocks get priority)
MAX_ETF_ALERTS = 3
# Default maximum VWAP distance (%) — overridden by market regime
MAX_VWAP_DISTANCE_PCT_DEFAULT = 3.0
# Maximum intraday move (%) from today’s open allowed for new entries.
# Stocks already up > this % have already made their move — chasing them
# means buying late with slim upside and wide risk.
MAX_INTRADAY_CHANGE_PCT_DEFAULT = 6.0


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
            self.data_client: DataClient | None = create_data_client(broker_config)
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
            self.data_client = None
            self.catalyst_scorer = None
            
        self.fallback_catalyst = CatalystScorer(min_catalyst_score=60)
        # Quality symbols get a lower gap threshold (mega-cap names with
        # 0.2-0.4% gaps are meaningful; micro-caps need bigger gaps).
        # Both AlpacaClient and IBKRClient share the same _CORE_WATCHLIST.
        quality_symbols = set(AlpacaClient._CORE_WATCHLIST) if hasattr(AlpacaClient, '_CORE_WATCHLIST') else set()
        self.scanner = GapScanner(
            price_min=scanner_defaults["price_min"],
            price_max=scanner_defaults["price_max"],
            min_gap_pct=scanner_defaults["min_gap_pct"],
            min_premarket_volume=scanner_defaults["min_premarket_volume"],
            min_dollar_volume=scanner_defaults["min_dollar_volume"],
            max_spread_pct=scanner_defaults["max_spread_pct"],
            min_gap_pct_quality=scanner_defaults.get("min_gap_pct_quality"),
            quality_symbols=quality_symbols,
            max_gap_pct=scanner_defaults.get("max_gap_pct", 0.0),
        )
        # Intraday momentum scanner — finds stocks rallying from today's open
        # (runs alongside GapScanner during midday/close sessions).
        momentum_cfg = scanner_config.get("momentum", {})
        self.momentum_scanner = MomentumScanner(
            price_min=momentum_cfg.get("price_min", scanner_defaults["price_min"]),
            price_max=momentum_cfg.get("price_max", scanner_defaults["price_max"]),
            min_intraday_change_pct=momentum_cfg.get("min_intraday_change_pct", 1.5),
            min_relative_volume=momentum_cfg.get("min_relative_volume", 1.3),
            min_dollar_volume=momentum_cfg.get("min_dollar_volume", 500_000.0),
            max_spread_pct=momentum_cfg.get("max_spread_pct", 2.0),
            require_above_vwap=momentum_cfg.get("require_above_vwap", True),
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
        self.risk_per_trade_pct = risk_defaults.get("risk_per_trade_pct", 0.5)
        # Risk-tiered stop caps: low-risk trades get tighter stops
        self.stop_pct_by_risk = {
            "low": risk_defaults.get("stop_pct_low_risk", self.fixed_stop_pct),
            "medium": risk_defaults.get("stop_pct_medium_risk", self.fixed_stop_pct),
            "high": risk_defaults.get("stop_pct_high_risk", self.fixed_stop_pct),
        }
        self.risk_manager = RiskManager(
            max_trades_per_day=risk_defaults["max_trades_per_day"],
            daily_loss_lockout_pct=risk_defaults["daily_loss_lockout_pct"],
            max_consecutive_losses=risk_defaults["max_consecutive_losses"],
        )
        # Separate (tighter) risk manager for O2 relaxed trades
        self.o2_risk_manager = RiskManager(
            max_trades_per_day=risk_defaults.get("o2_max_trades_per_day", 3),
            daily_loss_lockout_pct=risk_defaults["daily_loss_lockout_pct"],
            max_consecutive_losses=risk_defaults["max_consecutive_losses"],
        )
        self.market_analyzer = MarketConditionAnalyzer()
        self.market_guard = MarketGuard()
        # Current market condition — populated by _run_three_option_session,
        # consumed by _build_cards for dynamic filter thresholds.
        self._market_condition: MarketCondition | None = None
        # Current broad-market health — populated before _build_cards.
        self._market_health: MarketHealth | None = None
        # AI Trade Validator (paid LLM call per card) — disabled by default
        ai_validation_enabled = False
        if use_real_data:
            ai_validation_enabled = broker_config.get("news", {}).get("ai_trade_validation_enabled", False)
        self.ai_validator = AITradeValidator() if ai_validation_enabled else None
        self.notifier = TelegramNotifier.from_env()
        self._alerts_sent_count: int = 0

        # ── Execution engine (optional — requires IBKR + mode != alert_only)
        self.execution_mgr = create_execution_manager(
            data_client=self.data_client,
            risk_config=risk_config,
        ) if use_real_data else None

        # Auto-tune overrides (populated by apply_tuning before first scan)
        self._tuning_overrides: dict[str, float] = {}
        
        # Relaxed scanner for Option 2
        o2_cfg = scanner_config.get("o2_relaxed", {})
        self.relaxed_scanner = GapScanner(
            price_min=o2_cfg.get("price_min", scanner_defaults["price_min"]),
            price_max=o2_cfg.get("price_max", scanner_defaults["price_max"]),
            min_gap_pct=o2_cfg.get("min_gap_pct", 0.0),
            min_premarket_volume=o2_cfg.get("min_premarket_volume", 0),
            min_dollar_volume=o2_cfg.get("min_dollar_volume", 0),
            max_spread_pct=o2_cfg.get("max_spread_pct", 5.0),
            max_gap_pct=o2_cfg.get("max_gap_pct", 12.0),
        )

    def apply_tuning(self) -> None:
        """Run auto-tuner and apply safe recommendations to this session.

        Called once at session boot (before the first scan cycle).
        Only recommendations with confidence >= 0.5 are applied.
        """
        from tradingbot.analysis.auto_tuner import AutoTuner, persist_tuning

        tuner = AutoTuner(
            min_trades=20,
            current_thresholds={
                "min_catalyst_score": 40,
                "min_relative_volume": 3.0,
                "min_ranker_score": self.ranker.min_score,
                "min_confluence_score": MIN_CONFLUENCE_SCORE,
                "max_vwap_distance_pct": MAX_VWAP_DISTANCE_PCT_DEFAULT,
                "max_trades_per_day": self.risk_manager.max_trades_per_day,
            },
        )
        result = tuner.tune()
        logging.info(f"[AUTO_TUNE] {result.summary()}")

        applied: list[str] = []
        for rec in result.recommendations:
            if rec.confidence < 0.5:
                logging.info(f"[AUTO_TUNE] Skipping {rec.parameter}: confidence {rec.confidence:.0%} < 50%")
                continue
            self._tuning_overrides[rec.parameter] = rec.recommended
            applied.append(f"{rec.parameter}={rec.recommended}")

        if applied:
            logging.info(f"[AUTO_TUNE] Applied overrides: {', '.join(applied)}")
            result.applied = True
        else:
            logging.info("[AUTO_TUNE] No high-confidence recommendations — using defaults")

        persist_tuning(result)

    def run_day(self) -> tuple[WatchlistRun, WatchlistRun]:
        """Legacy method - runs old 2-section output."""
        catalyst_scores: dict[str, float] = {}
        night_universe_str: list[str] = []
        
        if self.use_real_data and self.data_client and self.catalyst_scorer:
            # Real data flow
            universe = self.data_client.get_tradable_universe()
            catalyst_scores = self.catalyst_scorer.score_symbols(universe)
            night_universe_str = [s for s, score in catalyst_scores.items() if score >= 40]
            if not night_universe_str:
                night_universe_str = [s for s, _ in sorted(catalyst_scores.items(), key=lambda x: x[1], reverse=True)[:50]]
            
            premarket_snapshots = self.data_client.get_premarket_snapshots(night_universe_str)
            # Apply catalyst scores
            for snap in premarket_snapshots:
                snap.catalyst_score = catalyst_scores.get(snap.symbol, 30.0)
            
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
        if self.use_real_data and self.data_client:
            # For midday, re-fetch current snapshots
            midday_snapshots = self.data_client.get_premarket_snapshots(night_universe_str)
            for snap in midday_snapshots:
                snap.catalyst_score = catalyst_scores.get(snap.symbol, 30.0)
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
        
        if self.use_real_data and self.data_client and self.catalyst_scorer:
            # Real data flow
            universe = self.data_client.get_tradable_universe()
            catalyst_scores = self.catalyst_scorer.score_symbols(universe)
            night_universe_str = [s for s, score in catalyst_scores.items() if score >= 40]
            if not night_universe_str:
                night_universe_str = [s for s, _ in sorted(catalyst_scores.items(), key=lambda x: x[1], reverse=True)[:50]]
            
            premarket_snapshots = self.data_client.get_premarket_snapshots(night_universe_str)
            # Apply catalyst scores
            for snap in premarket_snapshots:
                snap.catalyst_score = catalyst_scores.get(snap.symbol, 30.0)
            
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
        if self.use_real_data and self.data_client:
            midday_snapshots = self.data_client.get_premarket_snapshots(night_universe_str)
            for snap in midday_snapshots:
                snap.catalyst_score = catalyst_scores.get(snap.symbol, 30.0)
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

        # ── SPY/QQQ broad market guard ──
        if self.use_real_data:
            self._market_health = self.market_guard.check()
            logging.info(f"[MARKET_GUARD] {self._market_health.regime}: {self._market_health.reason}")
            if self._market_health.regime == "red":
                logging.warning(f"[MARKET_GUARD] RED — halting new entries")
        else:
            self._market_health = None

        # Option 1: Night Research — top catalyst picks with smart money overlay
        night_picks = self._get_night_research_picks(snapshots, catalyst_scores, session_tag)

        # ── Run O3 FIRST so high-quality strict picks claim the daily cap ──
        # Option 3: Strict filters scan
        strict_scan = self.scanner.run(snapshots)

        # For midday/close sessions, also run the momentum scanner to catch
        # intraday runners (stocks that opened flat but rallied).  Merge
        # momentum candidates into the strict scan, deduplicating by symbol.
        if stricter:
            momentum_scan = self.momentum_scanner.run(snapshots)
            if momentum_scan.candidates:
                gap_symbols = {c.symbol for c in strict_scan.candidates}
                new_momentum = [c for c in momentum_scan.candidates if c.symbol not in gap_symbols]
                strict_scan.candidates.extend(new_momentum)
                logging.info(
                    f"[MOMENTUM] +{len(new_momentum)} intraday runners added "
                    f"({len(momentum_scan.candidates)} total momentum, "
                    f"{len(gap_symbols)} already from gap scan)"
                )

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

        # Option 2: Relaxed filters scan — uses its OWN trade cap so it
        # never steals slots from O3.  Alerts are saved & sent but counted
        # against a separate budget.
        relaxed_scan = self.relaxed_scanner.run(snapshots)
        # Merge momentum candidates into O2 as well for midday/close
        if stricter:
            relaxed_symbols = {c.symbol for c in relaxed_scan.candidates}
            for mc in momentum_scan.candidates:
                if mc.symbol not in relaxed_symbols:
                    relaxed_scan.candidates.append(mc)
        relaxed_ranked = self.relaxed_ranker.run(relaxed_scan.candidates)
        o2_dropped: list[tuple[str, str]] = []
        relaxed_cards = self._build_cards(
            ranked=relaxed_ranked,
            session_tag=session_tag,
            volume_spike=self.volume_spike_midday if stricter else self.volume_spike_morning,
            relaxed=True,
            dropped=o2_dropped,
            independent_cap=True,
            risk_manager_override=self.o2_risk_manager,
        )

        logging.info(f"[{session_tag.upper()}] snapshots={len(snapshots)} O1={len(night_picks)} O2={len(relaxed_cards)} O3={len(strict_cards)}")
        if o2_dropped:
            drop_summary = {}
            for _, reason in o2_dropped:
                key = reason.split(":")[0]
                drop_summary[key] = drop_summary.get(key, 0) + 1
            logging.info(f"[{session_tag.upper()}] O2 drops: {drop_summary}")
        if o3_dropped:
            drop_summary = {}
            for _, reason in o3_dropped:
                key = reason.split(":")[0]
                drop_summary[key] = drop_summary.get(key, 0) + 1
            logging.info(f"[{session_tag.upper()}] O3 drops: {drop_summary}")
        
        # Analyze market conditions and make recommendation
        market_condition = self.market_analyzer.analyze(
            morning_snapshots=snapshots,
            catalyst_scores=catalyst_scores,
        )
        # Store for _build_cards to read dynamic thresholds
        self._market_condition = market_condition
        
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
    
    # ── Pre-card filter helpers (shared by _build_cards loop) ──────────

    def _passes_dedup(
        self,
        symbol: SymbolSnapshot,
        already_alerted: dict[str, float],
        dropped: list[tuple[str, str]] | None,
    ) -> bool:
        """Return True if the symbol should (re-)alert; False to skip.

        First-time symbols always pass.  Previously-alerted symbols must
        demonstrate a qualifying pullback re-entry:
          - Pulled back 30-70% of its move (constructive dip)
          - Holding VWAP or EMA20 (support intact)
          - Recovering above EMA9 (bounce signal)
          - Entry price at least 2% better than the first alert

        This replaces the old crude 50% distance-to-support check with
        the full pullback re-entry detector.
        """
        prev_entry = already_alerted.get(symbol.symbol)
        if prev_entry is None or prev_entry <= 0:
            return True

        # Run the pullback re-entry evaluator
        signal = evaluate_pullback_reentry(symbol, prev_entry_price=prev_entry)
        if signal.qualifies:
            logging.info(
                f"[RE-ENTRY] {symbol.symbol}: pullback re-entry qualified "
                f"(score={signal.reentry_score:.0f}, depth={signal.pullback_depth_pct:.0f}%, "
                f"prev_entry=${prev_entry:.2f}, new=${symbol.price:.2f})"
            )
            return True

        if dropped is not None:
            dropped.append((symbol.symbol, f"dedup:{signal.reason}"))
        return False

    def _passes_etf_limits(
        self,
        symbol: SymbolSnapshot,
        etf_count: int,
        selected_families: set[str],
        dropped: list[tuple[str, str]] | None,
    ) -> bool:
        """Return True if ETF concentration / family limits allow this symbol."""
        if not is_etf(symbol.symbol):
            return True
        if etf_count >= MAX_ETF_ALERTS:
            if dropped is not None:
                dropped.append((symbol.symbol, "etf_concentration_cap"))
            logging.info(f"[DROP] {symbol.symbol}: ETF concentration cap reached ({etf_count}/{MAX_ETF_ALERTS})")
            return False
        family = get_etf_family(symbol.symbol)
        if family and family in selected_families:
            if dropped is not None:
                dropped.append((symbol.symbol, f"etf_family_dup:{family}"))
            logging.info(f"[DROP] {symbol.symbol}: ETF family '{family}' already selected")
            return False
        return True

    def _passes_intraday_extension(
        self,
        symbol: SymbolSnapshot,
        dropped: list[tuple[str, str]] | None,
    ) -> bool:
        """Return True if price has NOT moved too far from today's open.

        Stocks already up >6% from the open have made their primary move.
        Entering late means buying the top with slim remaining upside.
        Morning pre-market scans skip this check (no open yet).

        Exception: if the stock has pulled back constructively (30-70% of
        its move, holding VWAP/EMA), we allow the re-entry — this is the
        classic gap-up → pullback → bounce pattern.
        """
        if symbol.intraday_change_pct <= 0:
            return True  # flat or down — not extended
        max_move = self._tuning_overrides.get(
            "max_intraday_change_pct", MAX_INTRADAY_CHANGE_PCT_DEFAULT
        )
        if symbol.intraday_change_pct > max_move:
            # Before dropping, check if this is a pullback re-entry
            signal = evaluate_pullback_reentry(symbol)
            if signal.qualifies:
                logging.info(
                    f"[PULLBACK_PASS] {symbol.symbol}: extended +{symbol.intraday_change_pct:.1f}% "
                    f"but qualifies as pullback re-entry (depth={signal.pullback_depth_pct:.0f}%, "
                    f"score={signal.reentry_score:.0f})"
                )
                return True  # allow the pullback re-entry
            if dropped is not None:
                dropped.append((
                    symbol.symbol,
                    f"intraday_extended:{symbol.intraday_change_pct:.1f}%>{max_move:.0f}%",
                ))
            logging.info(
                f"[DROP] {symbol.symbol}: already up {symbol.intraday_change_pct:.1f}% "
                f"from open (max {max_move:.0f}%) — too extended"
            )
            return False
        return True

    def _passes_vwap_distance(
        self,
        symbol: SymbolSnapshot,
        dropped: list[tuple[str, str]] | None,
    ) -> bool:
        """Return True if price is not extended too far from VWAP."""
        mc = self._market_condition
        max_vwap = mc.max_vwap_distance_pct if mc else MAX_VWAP_DISTANCE_PCT_DEFAULT
        max_vwap = self._tuning_overrides.get("max_vwap_distance_pct", max_vwap)
        if symbol.vwap > 0 and symbol.price > 0:
            vwap_dist_pct = abs(symbol.price - symbol.vwap) / symbol.vwap * 100
            if vwap_dist_pct > max_vwap:
                if dropped is not None:
                    dropped.append((symbol.symbol, f"vwap_extended:{vwap_dist_pct:.1f}%"))
                logging.info(
                    f"[DROP] {symbol.symbol}: price too far from VWAP "
                    f"({vwap_dist_pct:.1f}% > {max_vwap}%)")
                return False
        return True

    def _passes_catalyst_gate(
        self,
        symbol: SymbolSnapshot,
        can_long: bool,
        dropped: list[tuple[str, str]] | None,
    ) -> bool:
        """Return True if catalyst or volume conviction is sufficient."""
        mc = self._market_condition
        min_catalyst = mc.min_catalyst_score if mc else 40
        min_catalyst = self._tuning_overrides.get("min_catalyst_score", min_catalyst)
        min_rvol = mc.min_relative_volume if mc else 3.0
        min_rvol = self._tuning_overrides.get("min_relative_volume", min_rvol)
        if symbol.catalyst_score < min_catalyst:
            has_strong_volume = (
                symbol.relative_volume >= min_rvol
                and symbol.premarket_volume >= 100_000
            )
            if not (has_strong_volume and can_long):
                if dropped is not None:
                    dropped.append((symbol.symbol, f"low_catalyst_weak_vol:{symbol.catalyst_score:.0f}/rv={symbol.relative_volume:.1f}"))
                logging.info(
                    f"[DROP] {symbol.symbol}: catalyst={symbol.catalyst_score:.0f} < {min_catalyst} "
                    f"and volume not convincing (relvol={symbol.relative_volume:.1f}, pm_vol={symbol.premarket_volume})"
                )
                return False
        return True

    def _passes_gap_fade_check(
        self,
        symbol: SymbolSnapshot,
        dropped: list[tuple[str, str]] | None,
        relaxed: bool = False,
    ) -> bool:
        """Return False if the gap is fading (price below VWAP after a positive gap).

        A stock that gapped up but is now trading below VWAP indicates sellers
        are absorbing the gap — high probability of continued fade. In relaxed
        mode this check is skipped (catalyst-driven entries tolerate more drift).
        """
        if relaxed:
            return True
        # Only apply to stocks that gapped up
        if symbol.gap_pct <= 0:
            return True
        # If price is below VWAP, the gap is fading
        if symbol.vwap > 0 and symbol.price < symbol.vwap:
            if dropped is not None:
                dropped.append((symbol.symbol,
                    f"gap_fade:price={symbol.price:.2f}<vwap={symbol.vwap:.2f}"))
            logging.info(
                f"[DROP] {symbol.symbol}: gap fading — price ${symbol.price:.2f} "
                f"below VWAP ${symbol.vwap:.2f} (gap was +{symbol.gap_pct:.1f}%)"
            )
            return False
        return True

    def _passes_trend_filter(
        self,
        symbol: SymbolSnapshot,
        dropped: list[tuple[str, str]] | None,
        relaxed: bool = False,
    ) -> bool:
        """Return False if the stock is gapping up inside a daily downtrend.

        Stocks trading below their daily EMA50 are in a higher-timeframe
        downtrend.  A gap-up against the trend is a bear rally that fades
        more often than it continues.  This is the single biggest edge
        improvement for the scanner.

        Bypass: relaxed mode (catalyst-driven) and stocks with strong
        catalysts (score >= 70) — a significant news event can override
        a weak daily trend.
        """
        if relaxed:
            return True
        # Skip if daily EMA50 not available (insufficient daily bars)
        if symbol.daily_ema50 <= 0:
            return True
        # Only penalise gap-ups in downtrend (price below daily EMA50)
        if symbol.price >= symbol.daily_ema50:
            return True
        # Strong catalyst can override a weak trend
        if symbol.catalyst_score >= 70:
            logging.info(
                f"[TREND_BYPASS] {symbol.symbol}: below daily EMA50 "
                f"(${symbol.price:.2f} < ${symbol.daily_ema50:.2f}) but "
                f"catalyst={symbol.catalyst_score:.0f} overrides")
            return True
        if dropped is not None:
            dropped.append((
                symbol.symbol,
                f"daily_downtrend:price={symbol.price:.2f}<ema50={symbol.daily_ema50:.2f}",
            ))
        logging.info(
            f"[DROP] {symbol.symbol}: daily downtrend — price ${symbol.price:.2f} "
            f"below daily EMA50 ${symbol.daily_ema50:.2f} (bear rally risk)")
        return False

    def _build_cards(
        self,
        ranked: list,
        session_tag: Literal["morning", "midday", "close"],
        volume_spike: float,
        dropped: list[tuple[str, str]] | None = None,
        relaxed: bool = False,
        independent_cap: bool = False,
        risk_manager_override: RiskManager | None = None,
    ) -> list[TradeCard]:
        """Build trade cards from ranked candidates.

        Includes dedup logic: if a symbol was already alerted today,
        only re-alert if the current price has pulled back at least 50%
        closer to key support (i.e. a materially better entry).

        If *relaxed* is True (Option 2), indicator confirmation is skipped
        for stocks with catalyst_score >= 55 and the confluence floor is
        lowered to 0 (only block strong opposing signals).

        If *independent_cap* is True, the risk-manager trade count starts
        at zero instead of counting today's existing alerts.  This gives
        the caller its own separate daily budget (used by O2 so it never
        steals O3's slots).

        If *risk_manager_override* is provided, it replaces self.risk_manager
        for this call (used to give O2 a tighter per-day cap).
        """
        cards: list[TradeCard] = []

        # Load today's already-alerted symbols for dedup
        try:
            already_alerted = get_today_alerted_symbols()
        except Exception as exc:
            logging.warning(f"Dedup check failed ({exc}); proceeding without dedup")
            already_alerted = {}

        # Track ETF families already selected (for conflict + family dedup)
        selected_etf_families: set[str] = set()
        # Count ETFs selected this pass (for concentration cap)
        etf_count = 0

        # Pre-seed risk state with today's alert count so the daily cap
        # (max_trades_per_day) persists across 30-minute scan cycles.
        # When independent_cap is True the counter starts at 0 so this
        # option gets its own full budget.
        risk_state = RiskState(
            trades_taken=0 if independent_cap else len(already_alerted)
        )

        rm = risk_manager_override or self.risk_manager

        # Market guard: halt entries if SPY/QQQ in red regime
        mh = self._market_health
        if mh and mh.regime == "red":
            logging.warning(f"[MARKET_GUARD] Skipping all card generation: {mh.reason}")
            return cards

        # Yellow regime: raise effective score floor by 5 points to filter
        # marginal setups in weak-tape environments.
        yellow_score_penalty = 5.0 if (mh and mh.regime == "yellow") else 0.0

        for item in ranked:
            # ── Yellow regime score gate ──────────────────────────
            if yellow_score_penalty > 0 and item.score < (self.ranker.min_score + yellow_score_penalty):
                if dropped is not None:
                    dropped.append((item.snapshot.symbol, f"yellow_regime_low_score:{item.score:.1f}"))
                logging.info(
                    f"[MARKET_GUARD] {item.snapshot.symbol}: score {item.score:.1f} "
                    f"< {self.ranker.min_score + yellow_score_penalty:.0f} (yellow regime penalty)"
                )
                continue

            if not rm.allow_new_trade(risk_state):
                if dropped is not None:
                    dropped.append((item.snapshot.symbol, "risk_lockout"))
                break

            symbol = item.snapshot

            # ── Secondary price guard (safety net) ──────────────────
            # The GapScanner already enforces price_min, but this catch-all
            # ensures no sub-$5 stock ever makes it to an alert regardless
            # of which scanner path or configuration was used.
            hard_price_min = self.scanner.price_min
            if symbol.price < hard_price_min:
                if dropped is not None:
                    dropped.append((symbol.symbol, f"price_guard:{symbol.price:.2f}<{hard_price_min}"))
                logging.info(f"[DROP] {symbol.symbol}: price ${symbol.price:.2f} below ${hard_price_min} minimum")
                continue

            # ── Dedup check: skip if already alerted unless pullback ──
            if not self._passes_dedup(symbol, already_alerted, dropped):
                continue

            # ── ETF conflict / family dedup / concentration cap ─────
            sym_is_etf = is_etf(symbol.symbol)
            sym_family = get_etf_family(symbol.symbol)

            # Block ALL ETFs — analysis of Apr 6-8 showed 9/23 picks were
            # ETFs with 0% win rate.  ETFs don't gap-and-go like individual
            # stocks — they mean-revert intraday.  This drops all known ETFs
            # including non-leveraged ones (BITO, HYG, SLV, IWM, etc.).
            if sym_is_etf:
                if dropped is not None:
                    dropped.append((symbol.symbol, "etf_blocked"))
                logging.info(f"[DROP] {symbol.symbol}: ETF blocked — ETFs have 0% intraday WR")
                continue

            # ── VWAP distance filter (regime-adaptive + auto-tune) ──────
            if not self._passes_vwap_distance(symbol, dropped):
                continue

            # ── Intraday extension filter ───────────────────────────
            # Block stocks that have already moved too far from today's
            # open — we don't want to chase extended runners.
            if not self._passes_intraday_extension(symbol, dropped):
                continue

            # ── Daily trend filter (higher-timeframe EMA50) ─────────
            # Block gap-ups inside a daily downtrend — bear rallies
            # that fade more often than they continue.
            if not self._passes_trend_filter(symbol, dropped, relaxed):
                continue

            # ── Gap fade detection ──────────────────────────────────
            # If the stock gapped up but current price is below VWAP,
            # the gap is fading — skip to avoid catching falling knives.
            if not self._passes_gap_fade_check(symbol, dropped, relaxed):
                continue

            can_long = has_valid_setup(symbol, volume_spike)

            # ── Catalyst / conviction gate (regime-adaptive + auto-tune) ──
            if not self._passes_catalyst_gate(symbol, can_long, dropped):
                continue

            # In relaxed mode, allow high-catalyst stocks through even
            # without full indicator confirmation (pre-market data is sparse).
            if not can_long:
                if relaxed and symbol.catalyst_score >= 55:
                    # Catalyst-driven bypass: require positive gap
                    if symbol.gap_pct >= 0:
                        can_long = True
                        logging.info(f"[RELAXED] {symbol.symbol} bypassed indicator check (catalyst={symbol.catalyst_score:.0f})")
                    else:
                        if dropped is not None:
                            dropped.append((symbol.symbol, "relaxed_negative_gap"))
                        logging.info(f"[DROP] {symbol.symbol}: negative gap in long-only mode (gap={symbol.gap_pct:.1f}%)")
                        continue
                else:
                    if dropped is not None:
                        dropped.append((symbol.symbol, "indicator_confirmation_failed"))
                    logging.info(f"[DROP] {symbol.symbol}: indicator_confirmation_failed (vol_spike={volume_spike}, catalyst={symbol.catalyst_score:.0f})")
                    continue

            # ── Long setup required ────────────────────────────
            if not can_long:
                if dropped is not None:
                    dropped.append((symbol.symbol, "no_long_setup"))
                logging.info(
                    f"[DROP] {symbol.symbol}: no valid long setup")
                continue
            # Apply market guard size multiplier (yellow = 50%, green = 100%)
            effective_risk_pct = self.risk_per_trade_pct
            if mh and mh.size_multiplier < 1.0:
                effective_risk_pct *= mh.size_multiplier
            # Apply streak-based scaling after consecutive losses
            streak_mult = rm.streak_size_multiplier(risk_state)
            if streak_mult < 1.0:
                effective_risk_pct *= streak_mult
                logging.info(f"[STREAK] {symbol.symbol}: sizing at {streak_mult:.0%} after {risk_state.consecutive_losses} consecutive losses")
            card = build_trade_card(
                stock=symbol,
                score=item.score,
                fixed_stop_pct=self.fixed_stop_pct,
                session_tag=session_tag,
                risk_per_trade_pct=effective_risk_pct,
                stop_buffer_multiplier=mh.stop_buffer_multiplier if mh else 1.0,
                stop_pct_by_risk=self.stop_pct_by_risk,
            )
            if card is None:
                if dropped is not None:
                    dropped.append((symbol.symbol, "rr_below_floor"))
                logging.info(f"[DROP] {symbol.symbol}: rr_below_floor (support={symbol.key_support:.2f}, resistance={symbol.key_resistance:.2f}, price={symbol.price:.2f})")
                continue
            card.patterns = list(symbol.patterns)
            card.catalyst_score = symbol.catalyst_score
            confluence = score_confluence(card.patterns)

            # ── First-15-min fakeout guard ────────────────────────
            # Between 9:30-9:45 ET the opening cross creates wild wicks
            # that trigger false breakouts.  Raise the confluence floor
            # to 15 and widen the stop by 20% so only the strongest
            # setups fire, and stops aren't clipped by opening noise.
            _ET = ZoneInfo("America/New_York")
            now_et = datetime.now(_ET).time()
            in_opening_window = dt_time(9, 30) <= now_et <= dt_time(9, 45)

            if in_opening_window and not relaxed:
                confluence_floor = 15  # tighter filter during fakeout window
                if card.stop_price and card.entry_price:
                    # widen stop by 20% to survive opening wicks
                    stop_dist = abs(card.entry_price - card.stop_price)
                    widened = stop_dist * 1.20
                    card.stop_price = round(card.entry_price - widened, 2)
                    logging.info(f"[FAKEOUT_GUARD] {symbol.symbol}: widened stop to {card.stop_price:.2f} (opening window)")
            else:
                # Normal confluence floor
                # Relaxed mode: only block if strong opposing signal (< 0)
                # Strict mode: require MIN_CONFLUENCE_SCORE (10, or auto-tuned)
                _tuned_min = self._tuning_overrides.get("min_confluence_score")
                confluence_floor = 0 if relaxed else (_tuned_min if _tuned_min is not None else MIN_CONFLUENCE_SCORE)

            # Block cards with weak or opposing signals
            if confluence < confluence_floor:
                if dropped is not None:
                    dropped.append((symbol.symbol, f"low_confluence:{confluence:.0f}"))
                logging.info(f"[DROP] {symbol.symbol}: low_confluence={confluence:.0f} (floor={confluence_floor}, patterns={symbol.patterns})")
                continue

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

            # ── Confluence engine (multi-factor scoring) ──
            # Replaces simple pattern-only blending with 5-factor institutional check:
            # volume profile, market trend, ATR exhaustion, tech stack, catalyst.
            spy_pct = mh.spy_change_pct if mh else 0.0
            qqq_pct = mh.qqq_change_pct if mh else 0.0
            # Use real open_price for ATR exhaustion accuracy;
            # fall back to VWAP only when open isn't available.
            open_price = symbol.open_price if symbol.open_price > 0 else symbol.tech_indicators.get("vwap", symbol.price)
            confluence_result = evaluate_confluence(
                current_price=symbol.price,
                open_price=open_price,
                ema9=symbol.ema9,
                ema20=symbol.ema20,
                vwap=symbol.vwap,
                atr=symbol.atr,
                spread_pct=symbol.spread_pct,
                bars_data=getattr(symbol, "raw_bars", []),
                relative_volume=symbol.relative_volume,
                spy_change_pct=spy_pct,
                qqq_change_pct=qqq_pct,
                rsi=symbol.tech_indicators.get("rsi", 50.0),
                macd_hist=symbol.tech_indicators.get("macd_hist", 0.0),
                catalyst_score=symbol.catalyst_score,
                patterns=list(symbol.patterns),
                gap_pct=symbol.gap_pct,
            )

            # Attach confluence grade to card for alert formatting
            card.confluence_grade = confluence_result.grade
            card.confluence_score = confluence_result.composite_score
            card.false_positive_flags = list(confluence_result.false_positive_flags)

            # In strict mode, veto low-grade setups for high win-rate
            if not relaxed and confluence_result.vetoed:
                if dropped is not None:
                    dropped.append((symbol.symbol, f"confluence_veto:{confluence_result.veto_reason}"))
                logging.info(f"[DROP] {symbol.symbol}: {confluence_result.veto_reason}")
                continue

            # In strict mode, block Grade-F setups (composite < 40)
            if not relaxed and confluence_result.grade == "F":
                if dropped is not None:
                    dropped.append((symbol.symbol, f"grade_F:{confluence_result.composite_score:.0f}"))
                logging.info(
                    f"[DROP] {symbol.symbol}: Grade F confluence "
                    f"(score={confluence_result.composite_score:.0f})")
                continue

            # Blend confluence engine composite into the ranker score.
            # Use the multi-factor composite (0-100) instead of the simple
            # pattern-only score for a more accurate quality signal.
            # 60% ranker (gap/vol/tech) + 40% confluence engine (multi-factor)
            card.score = round(min(100.0,
                card.score * 0.60 + confluence_result.composite_score * 0.40
            ), 2)

            # Build volume profile for alert context
            vol_profile = classify_volume_profile(
                getattr(symbol, "raw_bars", []),
                symbol.relative_volume,
                symbol.price,
                symbol.vwap,
            )
            card.volume_classification = vol_profile.classification

            # Build institutional context for enriched alerts
            inst_ctx = build_institutional_context(
                card=card,
                snapshot=symbol,
                confluence_result=confluence_result,
                volume_profile=vol_profile,
                spy_change=spy_pct,
                qqq_change=qqq_pct,
            )

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

            # Update ETF tracking after successful selection
            if sym_is_etf:
                etf_count += 1
                if sym_family:
                    selected_etf_families.add(sym_family)

            # Send institutional-grade Telegram notification
            tg_ok = self.notifier.send_institutional_alert(card, inst_ctx)
            if not tg_ok:
                logging.warning(f"[TELEGRAM] Failed to send alert for {card.symbol} — alert saved to dashboard only")
            save_alert(card_to_dict(card))
            self._alerts_sent_count += 1

            # ── Optional execution (paper/live via IBKR) ──────────
            self._maybe_execute(card)

        return cards

    def _maybe_execute(self, card: TradeCard) -> None:
        """Submit a bracket order for *card* if execution is enabled.

        Errors are caught and logged — a failed order never blocks the
        scan loop.  The result is logged and (in a future iteration)
        sent as a Telegram confirmation.
        """
        if not self.execution_mgr:
            return

        try:
            result = self.execution_mgr.execute_card(card)
            if result["executed"]:
                logging.info(
                    f"[EXEC] {card.symbol}: {result['shares']} shares submitted"
                )
            else:
                logging.info(
                    f"[EXEC] {card.symbol}: not executed — {result['reason']}"
                )
        except Exception as exc:
            logging.error(f"[EXEC] {card.symbol}: unexpected error — {exc}")

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
        if self.use_real_data and self.data_client and self.catalyst_scorer:
            # Real news sources
            universe = self.data_client.get_tradable_universe()
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
        if self.use_real_data and self.data_client:
            # Sort by catalyst score (highest first) so the most
            # promising symbols are fetched in the earliest batches.
            sorted_universe = sorted(universe_str, key=lambda s: catalyst_scores.get(s, 0), reverse=True)
            snapshots = self.data_client.get_premarket_snapshots(sorted_universe)
        elif session_type == "morning":
            snapshots = [s for s in get_premarket_snapshots() if s.symbol in universe_set]
        else:
            snapshots = [s for s in get_midday_snapshots() if s.symbol in universe_set]
        for snap in snapshots:
            snap.catalyst_score = catalyst_scores.get(snap.symbol, 30.0)
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

        # Merge live screener movers so we catch intraday runners
        # that weren't in last night's research universe.
        if self.use_real_data and self.data_client:
            screener_syms = self.data_client.get_screener_symbols()
            new_syms = [s for s in screener_syms if s not in catalyst_scores]
            if new_syms:
                # Give new movers a penalised baseline catalyst score of 30
                # (same as no-news default) so they don't get a free pass.
                # They still enter the universe but must earn their score
                # through strong technicals.
                for s in new_syms:
                    catalyst_scores[s] = 30.0
                universe_str = list(set(universe_str) | set(new_syms))
                logging.info(f"[session] +{len(new_syms)} new screener movers added to universe")

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
