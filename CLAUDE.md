# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

An automated day-trading **alert system** (no trades executed). Scans stocks for intraday setups, sends Telegram notifications, and serves a Flask web dashboard. Deployed on Heroku/Render with Supabase persistence.

## Commands

```bash
# Install (editable mode)
pip install -e .

# Run locally with mock data (no credentials needed)
python -m tradingbot.cli run-day

# Run with real Alpaca data
python -m tradingbot.cli --real-data run-news       # Night catalyst research
python -m tradingbot.cli --real-data run-morning    # 08:45 ET pre-market scan
python -m tradingbot.cli --real-data run-midday     # 12:00 ET midday scan
python -m tradingbot.cli --real-data run-close      # 15:50 ET close scan

# Show schedule
python -m tradingbot.cli schedule

# Tests
pytest tests/ -v
pytest tests/test_trade_card.py -v   # single file
```

## Architecture

### Three-Option Watchlist System

Every scan session produces three parallel watchlists:
- **Option 1 (Night Research)**: Top 10 catalyst-driven picks from news/SEC/social signals
- **Option 2 (Relaxed)**: Gap ≥1%, volume ≥100K, DV ≥$10M
- **Option 3 (Strict)**: Gap ≥4%, volume ≥500K, DV ≥$20M, spread ≤0.35%

`MarketConditionAnalyzer` recommends which option based on live volatility.

### Data Flow

CLI → `Scheduler` → `SessionRunner` which:
1. Loads catalyst scores (from prior `run-news` job saved to `catalyst_scores.json`)
2. Fetches snapshots via `AlpacaClient`
3. Filters through `GapScanner`
4. Detects patterns, checks pullback setups, scores confluence
5. Ranks via `CatalystWeightedRanker`
6. Builds `TradeCard` (entry/stop/TP1/TP2)
7. Sends via `TelegramNotifier`, persists to `AlertStore` (Supabase + JSONL fallback)

### Key Modules

- `src/tradingbot/app/` — Scheduler orchestration, session runner, Heroku worker loop
- `src/tradingbot/analysis/` — Pattern detection, market conditions, technical indicators, chart generation
- `src/tradingbot/research/` — News aggregation (SEC EDGAR, RSS, social proxy), catalyst scoring, insider tracking
- `src/tradingbot/scanner/` — Gap scanner filters, close/hold scanner
- `src/tradingbot/signals/` — Pullback setup detection (EMA hold, VWAP reclaim, volume confirmation)
- `src/tradingbot/strategy/trade_card.py` — Trade card construction with entry/stop/target placement
- `src/tradingbot/ranking/ranker.py` — Multi-factor scoring (gap, volume, spread, RSI, MACD, catalyst)
- `src/tradingbot/risk/risk_manager.py` — Max trades/day, loss lockout, streak multiplier
- `src/tradingbot/web/` — Flask dashboard (dark theme), Supabase alert store
- `src/tradingbot/models.py` — Core dataclasses: `SymbolSnapshot`, `TradeCard`, `ThreeOptionWatchlist`, `RiskState`

### Configuration

All thresholds live in YAML files under `config/` (scanner.yaml, risk.yaml, indicators.yaml, schedule.yaml). Environment variables override YAML values for cloud deployment. See `config/broker.example.yaml` for credential template.

### Deployment

Two Heroku/Render processes:
- **Web**: `gunicorn` serving Flask dashboard
- **Worker/Cron**: Scheduled scan jobs (night research, morning, midday every 30min, close)

Required env vars: `ALPACA_API_KEY`, `ALPACA_API_SECRET`, `ALPACA_PAPER`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SUPABASE_URL`, `SUPABASE_KEY`

## Design Decisions

- **Long-only**: No short setups. The `side` field was removed from models.
- **Alert-only**: No order execution — generates trade card recommendations.
- **Free indicators only**: Uses `ta` library (not torch/transformers) to stay within Heroku slug size limits.
- **Telegram-primary**: Telegram is the main notification channel; web dashboard is a secondary alert viewer.
- **Stateless workers**: Heroku dynos can restart anytime — Supabase is the persistent source of truth for alerts.
- **AI optional**: LLM-based sentiment/validation (OpenAI/Anthropic) is opt-in and not required for core functionality.

## Live Deployment

- **Heroku dashboard**: `https://aztradingbot-c8a5462555f3.herokuapp.com`
- **Health check**: `https://aztradingbot-c8a5462555f3.herokuapp.com/api/health`
- **Render config**: `render.yaml` (alternative deployment, cron jobs defined here)

## Local Development

```bash
# Install all deps (pyproject.toml only has PyYAML — use requirements.txt)
pip install -r requirements.txt && pip install -e .

# Run Flask dashboard locally
FLASK_APP=src/tradingbot/web/app.py python -m flask run --port 5000
# Then visit http://127.0.0.1:5000

# Run scanner with mock data (no credentials needed)
python -m tradingbot.cli run-day
```

## Known Issues & Next Steps

### Fixed (2026-03-31)
1. **`TradeCard.side` crash** — `side` field was removed from models but 9 references remained. Fixed: added `side: str = "long"` default to `TradeCard` and `CloseHoldPick`.
2. **Fakeout guard broken field names** — `session_runner.py` referenced `card.stop_loss`/`card.entry` which don't exist on `TradeCard`. Fixed: corrected to `card.stop_price`/`card.entry_price`. The fakeout guard (9:30-9:45 ET stop widening) had never worked.
3. **NaN kills ranker scores** — `ta` library returns NaN for early bars, propagating through the weighted sum and silently dropping candidates. Fixed: `_safe()` method in `Ranker` defaults NaN/Inf to 50.0 (neutral) and logs a warning.
4. **`abs()` in gap scanner** — Long-only system was accepting negative gaps via `abs()`. Fixed: removed `abs()`, negative gaps now fail the threshold naturally.

### Deferred
5. **EMA hold check** (`indicators.py`) — uses `pullback_low >= ema20` which may be too loose (allows EMA9 breaks), but tightening to `ema9` may be too aggressive given how `pullback_low` is computed with ATR. Needs more analysis.

### Still Open
6. **pyproject.toml missing runtime deps** — only lists PyYAML; `pip install -e .` alone won't work. Need to sync deps from requirements.txt into pyproject.toml.
7. **Render.yaml cron schedules (UTC/ET confusion)** — several schedules are off. Night research at 5:00 UTC = midnight ET (should be ~3 AM UTC for 10 PM ET). Midday stops at 18 UTC = 1 PM ET (should extend to ~20 UTC for 3 PM ET). Close scan at 19:30 UTC = 2:30 PM ET (should be ~19:50 UTC for 3:50 PM ET).

### Improvements Planned
8. **Mock data too optimistic** — all 6 mock stocks pass every filter. Add diverse test data that exercises rejection/drop paths.
9. **Dashboard filtering** — currently loads 500 alerts then filters in Python. Should push filters to Supabase query.
10. **Test coverage gaps** — no tests for `_build_cards` core logic, ranker scoring, market guard, alert dedup, ETF family dedup.
11. **session_runner.py is ~800 lines** — should extract CardBuilder and ScanStrategy classes.
12. **Redundant datetime imports in cli.py** — imported 4 times in different blocks instead of once at top.

See `docs/IMPROVEMENTS.md` for the full algorithm improvement tracker with validation verdicts.
