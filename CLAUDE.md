# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

An automated day-trading **alert system** with optional IBKR execution. Scans stocks for intraday setups, sends Telegram notifications, and serves a Flask web dashboard. Deployed on VPS (`178.156.202.27`) — all crons, dashboard (nginx + gunicorn), and optional IBKR execution. Supabase for persistence. Branch: `feature/ibkr-execution`.

## Commands

```bash
# Install (editable mode)
pip install -r requirements.txt && pip install -e .

# Run locally with mock data (no credentials needed)
python -m tradingbot.cli run-day

# Run with real Alpaca data
python -m tradingbot.cli --real-data run-news       # Night catalyst research
python -m tradingbot.cli --real-data run-scout      # 09:15 ET morning scout (alerts only)
python -m tradingbot.cli --real-data run-execute    # 09:45 ET morning execute (orders)
python -m tradingbot.cli --real-data run-midday     # 10:00-14:45 ET midday scan
python -m tradingbot.cli --real-data run-close      # 15:30 ET close scan
python -m tradingbot.cli --real-data run-cleanup    # 15:45 ET force-expire unfilled orders

# Show schedule
python -m tradingbot.cli schedule

# Tests (268 on main, 387+ on feature/ibkr-execution)
pytest tests/ -v
pytest tests/test_trade_card.py -v   # single file
```

## Architecture

### Three-Option Watchlist System

Every scan session produces three parallel watchlists:
- **Option 1 (Night Research)**: Top 10 catalyst-driven picks from news/SEC/social signals
- **Option 2 (Relaxed)**: Catalyst-weighted ranker (30% catalyst). Separate budget (2 trades/day).
- **Option 3 (Strict)**: Gap ≥0.5%, volume ≥50K, DV ≥$500K, spread ≤2%, score ≥50, max 8 cards

`MarketConditionAnalyzer` recommends which option based on live volatility.

### Data Flow

CLI → `Scheduler` → `SessionRunner` which:
1. Loads catalyst scores (from prior `run-news` job saved to `catalyst_scores.json`)
2. Fetches snapshots via `AlpacaClient`
3. Filters through `GapScanner` (price_min=$5, long-only positive gaps)
4. Detects patterns, checks pullback setups, scores confluence
5. Ranks via `CatalystWeightedRanker` (11 scoring components)
6. `_build_cards` 21-step filter chain (market guard → price guard → inverse ETF blocker → gap fade → catalyst gate → confluence engine → etc.)
7. Builds `TradeCard` (entry/stop/TP1/TP2), blends score 60% ranker + 40% confluence
8. Sends via `TelegramNotifier` (1.5s delay, retries), persists to `AlertStore` (Supabase + JSONL fallback)

### Key Modules

- `src/tradingbot/app/` — Scheduler, session runner (~1200 lines), worker loop (WORKER_ENABLED gate)
- `src/tradingbot/analysis/` — Pattern detection, market guard (SPY/QQQ), market conditions, confluence engine, chart generation
- `src/tradingbot/research/` — News aggregation (SEC EDGAR, RSS, social proxy), catalyst scoring, insider/institutional/congressional tracking
- `src/tradingbot/scanner/` — Gap scanner filters, close/hold scanner
- `src/tradingbot/signals/` — Pullback setup detection (EMA hold, VWAP reclaim, volume confirmation)
- `src/tradingbot/strategy/trade_card.py` — Trade card construction with entry/stop/target placement (MIN_RR=1.5), session-adaptive TP caps, VWAP anchor, volume-scaled sizing
- `src/tradingbot/ranking/ranker.py` — 11-component multi-factor scoring (gap 15%, catalyst 15%, relvol 13%, etc.)
- `src/tradingbot/risk/risk_manager.py` — Max 8 trades/day, 3 consecutive loss lockout, streak scaling (75%→50%→lockout)
- `src/tradingbot/data/etf_metadata.py` — ETF family dedup, ALL ETFs blocked (not just inverse/VIX)
- `src/tradingbot/tracking/trade_tracker.py` — Trade outcome tracker (every 2 min), trailing stops (3 stages), portfolio circuit breaker
- `src/tradingbot/web/` — Flask dashboard (dark theme), Supabase alert store with trade outcomes, open positions P&L panel
- `src/tradingbot/models.py` — Core dataclasses: `SymbolSnapshot`, `TradeCard`, `ThreeOptionWatchlist`, `RiskState`

### Configuration

All thresholds live in YAML files under `config/`:
- `scanner.yaml` — price_min=$5, min_gap=0.5%, min_score=50, max_candidates=8
- `risk.yaml` — max_trades=8, o2_max=2, stop=2.5%, lockout=1.5%, consecutive=3
- `indicators.yaml` — EMA 9/20, vol spike morning=1.5×, midday=1.3×
- `schedule.yaml` — night 01:00, morning news 08:00, scout 09:15, execute 09:45, close 15:30
- `indicators.yaml` also has `vwap_distance_pct_morning: 3.0`, `vwap_distance_pct_midday: 5.0`

Environment variables override YAML for cloud deployment. See `config/broker.example.yaml` for credential template.

### Deployment

- **VPS** (`178.156.202.27`): All crons (news ×2, scout, execute, midday every 15min, tracker every 2min, close, cleanup), Flask dashboard via nginx + gunicorn, IB Gateway + IBKR execution
- **Heroku**: Decommissioned for crons. Dashboard URL still active but secondary.
- **Supabase**: Tables: alerts (with `source` column), trade_outcomes, sessions, close_picks

Required env vars: `ALPACA_API_KEY`, `ALPACA_API_SECRET`, `ALPACA_PAPER`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SUPABASE_URL`, `SUPABASE_KEY`

Optional: `DATA_PROVIDER=ibkr` (VPS only), `EXECUTION_MODE=paper|live` (VPS only)

## Design Decisions

- **Long-only**: No short setups. `TradeCard` and `CloseHoldPick` have `side: str = "long"` as a constant default.
- **Alert-primary**: Main branch is alert-only. Feature branch (`feature/ibkr-execution`) adds optional IBKR bracket order execution.
- **Free indicators only**: Uses `ta` library (not torch/transformers) to stay within Heroku slug size limits.
- **Telegram-primary**: Telegram is the main notification channel; web dashboard is a secondary alert viewer.
- **Stateless workers**: Heroku/Render dynos can restart anytime — Supabase is the persistent source of truth for alerts.
- **AI optional**: LLM-based sentiment/validation (OpenAI/Anthropic) is opt-in and not required for core functionality.
- **ALL ETFs blocked**: All ETFs blocked (not just inverse/VIX) — going long on ETFs has poor edge for gap-and-go.
- **Secondary price guard**: Hard floor at $5 in `_build_cards` regardless of scanner path.
- **Session-adaptive TP caps**: Morning `min(2.5×ATR, 4%)`, Midday `min(2.0×ATR, 4%)`, Close `min(1.5×ATR, 4%)`. Prevents unrealistic targets.
- **VWAP anchor TP**: For below-VWAP plays, anchors TP1 to VWAP instead of key_resistance.
- **Intraday ATR**: Uses last 5 bars' high-low range instead of daily ATR — more responsive to real-time volatility.
- **Volume-scaled sizing**: High relvol (≥3×) → 1.5× position, relvol ≥2× → 1.25×. Rewards high-participation setups.
- **Structural TP2**: Uses `key_resistance_2` (2nd resistance level) when available, instead of fixed 2× risk distance.
- **Gap extension fallback**: If no intraday resistance found, uses premarket high + 0.5×ATR.
- **Session-adaptive VWAP**: Morning 3% max distance, Midday/Close 5% — stocks drift from VWAP as day progresses.
- **Source tagging**: Each alert tagged `render-alpaca` or `vps-ibkr` in Telegram + Supabase.
- **Daily EMA50 trend filter**: Blocks stocks gapping up below daily EMA50 (bear rally protection).
- **Portfolio circuit breaker**: Closes all open trades if portfolio drawdown ≥ 1.5%, SPY/QQQ down ≥ 2%, or ≥ 75% of trades losing. Fires once per session, sends Telegram alert.

## Live Deployment

- **VPS dashboard**: `http://178.156.202.27` (nginx → gunicorn, dark theme, P&L tracking)
- **Heroku dashboard** (secondary): `https://aztradingbot-c8a5462555f3.herokuapp.com`
- **VPS crons**: 10 cron jobs via crontab on VPS (Render decommissioned as of Apr 15). Two-phase morning: scout (9:15, alerts only) → execute (9:45, live data + orders). Cleanup at 3:45 PM.

## Local Development

```bash
pip install -r requirements.txt && pip install -e .

# Run Flask dashboard locally
FLASK_APP=src/tradingbot/web/app.py python -m flask run --port 5000

# Run scanner with mock data (no credentials needed)
python -m tradingbot.cli run-day
```

## Documentation

- `docs/ALGORITHM.md` — Full trading pipeline, scoring weights, filter chain, business rules
- `docs/OPERATIONS.md` — Deployment, scheduling, file persistence, troubleshooting
- `docs/IMPROVEMENTS.md` — Algorithm improvement tracker with validation verdicts
- `docs/EXECUTION_ENGINE_PLAN.md` — Automated order execution plan (next MVP)

## Known Issues & Next Steps

### Deferred
- **EMA hold check** — uses `pullback_low >= ema20` which may be too loose. Needs analysis.

### Still Open
- **pyproject.toml missing runtime deps** — only lists PyYAML; need to sync from requirements.txt.
- **session_runner.py is ~1200 lines** — should extract CardBuilder and ScanStrategy classes.
- **test_catalyst_scorer_with_mocked_news** — hangs indefinitely (network call issue). Pre-existing.

### IBKR Execution (feature branch)
- All 13 modules DONE (IBKRClient, CapitalAllocator, OrderExecutor, PositionMonitor, ExecutionManager, ExecutionTracker, TelegramCommands, 119+ tests)
- **Non-Professional approval**: APPROVED — subscribe to US Securities Snapshot & Futures Value Bundle ($10/mo) or US Equity and Options Add-On Streaming Bundle ($4.50/mo)
- **Market data type**: Switched to live (type 1) — auto-falls back to delayed if subscription missing for an exchange
- **Scanner enhanced**: 5 TWS scanners including gap-specific codes (`TOP_OPEN_PERC_GAIN`, `HIGH_OPEN_GAP`) with server-side `priceAbove` and `volumeAbove` filters
- **Next**: Subscribe to market data → Paper test on VPS → Dashboard execution badges → Validate → Go live

### Recent Improvements (Apr 19)
- **Two-phase morning scan** — 9:15 AM scout (alerts only, no execution) → 9:45 AM execute (re-scan with live data, bypass dedup, place orders). Eliminates fakeout entries by waiting for 15 min of confirmed volume/VWAP.
- **End-of-day cleanup** — `run-cleanup` at 3:45 PM force-expires unfilled IBKR orders and open Supabase trades. Time-guarded (3:40–4:15 PM ET).
- **Cron schedule realigned** — midday shifted to 10:00–14:45 ET, IB health/watchdog narrowed to 04:00 AM–5:59 PM ET, old 8:45 morning removed.

### Previous Improvements (Apr 17)
- **Session-adaptive TP caps** — morning 4%, midday 4%, close 4% with ATR multipliers per session
- **Portfolio circuit breaker** — 3 triggers: portfolio drawdown, market crash (SPY/QQQ), correlated red
- **Tracker interval** — 5min → 2min for faster TP/stop detection
- **Dashboard P&L** — unrealized P&L %, dollar P&L, open positions summary panel
- **Volume-scaled sizing** — high relvol gets bigger positions
- **Intraday ATR** — uses last 5 bars instead of daily ATR
- **Structural TP2** — uses key_resistance_2 when available
- **VWAP anchor** — below-VWAP plays anchor TP1 to VWAP

### Backlog (biggest potential improvements)
- **Volume decay detection** — track fading participation across bars.
- **Dynamic R:R by score** — high-conviction setups should accept lower R:R.
- **Midday entry improvement** — 0% WR in backtest vs 100% morning. Consider tighter midday filters.
- **TP1/TP2 partial sell** — sell 50% at TP1, trail remainder to TP2.

See `docs/IMPROVEMENTS.md` for the full tracker.
