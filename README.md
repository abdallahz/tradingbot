# TradingBot

An automated day-trading alert system that scans for high-probability intraday setups, sends real-time Telegram notifications, and exposes a live web dashboard — all running on Heroku with zero manual intervention.

## What This Tool Does

**Five scheduled jobs run every trading day (all times ET):**

| Time | Job | Description |
|------|-----|-------------|
| 20:00 | Night Research | Score stocks on news catalysts; send top-10 list to Telegram |
| 08:00 | Morning News | Refresh catalyst scores; send updated list to Telegram |
| 08:45 | Pre-Market Scan | Find gappers ≥4%; send individual trade-card alerts |
| 12:00 | Midday Scan | Re-scan with stricter volume/spread filters |
| 15:30 | Close Scan | Final sweep for late-session setups |

**For every qualifying setup the bot produces a `TradeCard` containing:**
- Symbol, side (LONG/SHORT), score (0–100), risk/reward ratio
- Exact entry, stop, TP1, TP2, and invalidation price
- Detected chart patterns and signal reasons
- Optional candlestick chart image

**Outputs:**
- Real-time Telegram alerts (individual trade cards + session summaries)
- Web dashboard at `https://aztradingbot-c8a5462555f3.herokuapp.com`
- `outputs/catalyst_scores.json` — scored symbol universe
- `outputs/{session}_watchlist.csv` — machine-readable cards
- `outputs/{session}_playbook.md` — human-readable summary
- `outputs/archive/` — timestamped daily archive

## Architecture

```
Heroku (web dyno)                 Heroku (worker dyno)
─────────────────                 ────────────────────
Flask dashboard  ◄── /api/alerts  worker.py (60 s loop)
gunicorn                              ├─ 20:00 night_research
                                      ├─ 08:00 morning_news
                                      ├─ 08:45 premarket_scan
                                      ├─ 12:00 midday_scan
                                      └─ 15:30 close_scan
                                              │
                              ┌───────────────┴───────────────┐
                         Scheduler                      TelegramNotifier
                              │                               │
                         SessionRunner               send_trade_alert()
                         ├─ run_news_research()      send_news_summary()
                         ├─ run_single_session()     send_session_summary()
                         ├─ _fetch_snapshots()
                         ├─ _get_night_research_picks()
                         └─ _build_cards()
```

**Implementation phases (all complete):**
- **Phase 1**: Config-driven scanner, 3-indicator confirmation, risk management, mock data
- **Phase 2**: Alpaca API integration, multi-source news aggregation, catalyst scoring
- **Phase 3**: 3-option trading system with intelligent market-condition recommendations
- **Phase 4**: CLI split (5 commands), SEC EDGAR + RSS + social proxy news, smart money tracking
- **Phase 5**: ~~Render~~ → **Heroku** cloud deployment, persistent worker scheduler
- **Phase 6**: Free technical indicators via `ta` library (RSI, MACD, ATR, Bollinger Bands, VWAP, OBV)
- **Phase 7**: Telegram bot alerts — individual trade cards, news summaries, session summaries, error notifications
- **Phase 8**: Flask web dashboard (dark theme, alert cards, live scan trigger, auto-refresh)

## Quick Start (Local Dev)

1. Create and activate a virtual environment:

```bash
# Windows
python -m venv venv
.\venv\Scripts\activate

# Linux/Mac
python3 -m venv venv
source venv/bin/activate
```

2. Install dependencies:

```bash
pip install -e .
pip install pytest PyYAML
```

3. Configure API credentials (optional — required only for real data):

Edit `config/broker.yaml` using `config/broker.example.yaml` as a template:
```yaml
alpaca:
  api_key: "YOUR_ALPACA_API_KEY"
  api_secret: "YOUR_ALPACA_SECRET_KEY"
  paper: true
```

Get free paper trading credentials at https://alpaca.markets

4. Run the bot:

```bash
# Mock data (no credentials needed)
python -m tradingbot.cli run-day

# Real data — split commands matching the live schedule
python -m tradingbot.cli run-news       # Night research / morning news
python -m tradingbot.cli run-morning    # 08:45 AM pre-market scan
python -m tradingbot.cli run-midday     # 12:00 PM midday scan
python -m tradingbot.cli run-close      # 15:30 PM close scan

# Show the configured schedule
python -m tradingbot.cli schedule
```

5. Run the test suite:

```bash
pytest tests/ -v
# Expected: 18 passed
```

## Cloud Deployment (Heroku)

The bot runs on Heroku with **two dynos** — `web` (Flask dashboard) and
`worker` (persistent 60-second scheduler loop).

**Live dashboard:** `https://aztradingbot-c8a5462555f3.herokuapp.com`

### Required env vars (Heroku → Settings → Config Vars)

| Variable | Description |
|---|---|
| `ALPACA_API_KEY` | Alpaca paper/live key |
| `ALPACA_API_SECRET` | Alpaca secret |
| `ALPACA_PAPER` | `true` for paper trading |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Your numeric Telegram chat ID |
| `SEC_USER_AGENT` | e.g. `TradingBot/1.0 (you@example.com)` |

### Procfile

```
web:    PYTHONPATH=src gunicorn --workers 2 --bind 0.0.0.0:$PORT tradingbot.web.app:app
worker: PYTHONPATH=src python -m tradingbot.app.worker
```

### Deploy

```bash
git push heroku main   # or trigger via Heroku dashboard → Deploy tab
```

Heroku runs the **web dyno only**. Render handles all scheduled cron jobs.
The `WORKER_ENABLED` env var gates the worker loop — set to `false` on Heroku.

## 3-Option Trading System

Every scan session produces **three parallel views** with an intelligent recommendation:

### Option 1 — Night Research (Catalyst-Driven)
Focuses on the top-10 stocks with highest catalyst scores from news research.  
Best on **low-volatility days** when momentum is news-driven.  
Enriched with smart money signals (insider trades, 13F filings, congressional disclosures).

### Option 2 — Relaxed Filters (More Opportunities)
Catalyst-weighted ranker (30% catalyst weight). Bypasses indicator confirmation
for `catalyst_score >= 55` with positive gap. Separate daily budget (2 trades max).  
Best on **medium-volatility days** or when the strict scanner is empty.

### Option 3 — Strict Filters (High Probability)
Full indicator confirmation, confluence engine, fakeout guard.  
Price ≥ $5, gap ≥ 0.5%, premarket volume ≥ 50K, dollar volume ≥ $500K, spread ≤ 2%.  
Best on **high-volatility days** with strong pre-market activity.

### Intelligent Recommendation

The `MarketConditionAnalyzer` reads the live snapshot universe and picks:

| Market | Avg Gap | Recommendation |
|---|---|---|
| High volatility | ≥ 3%, 5+ gappers | Option 3 — Strict Filters |
| Low volatility | < 1.5% | Option 1 — Night Research |
| Medium volatility | 1.5–3% | Option 2 or 3 based on signal count |

## Telegram Alerts

The bot sends the following message types to `@aitradingazbot`:

| Event | Message |
|---|---|
| News research complete | Top-10 catalyst symbols with score bars |
| Trade card found | Full card: direction, levels, patterns, score |
| Session complete (trades found) | `📋 Pre-Market scan complete — 2 alerts sent above.` |
| Session complete (no trades) | `📭 Midday scan complete — no qualifying setups found.` |
| Job error | `⚠️ Close scan failed — <exception>` |

Configure in Heroku Config Vars: `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`.  
For local dev, add them to a `.env` file (loaded automatically via `python-dotenv`).

## Web Dashboard

Live at `https://aztradingbot-c8a5462555f3.herokuapp.com`

Features:
- Market status pill (pre-market / market hours / closed)
- Stats row: total alerts, long count, short count, last scan time
- **Run Scan Now** button — triggers an on-demand scan in the background
- Alert cards grid: symbol, LONG/SHORT badge, score bar, entry/stop/TP1/TP2, patterns, timestamp
- Auto-refreshes every 30 s (every 5 s while a scan is in progress)

API endpoints:
- `GET /api/health` — `{"status":"ok"}`
- `GET /api/alerts` — last 100 alerts as JSON
- `GET /api/status` — scanner running state + last scan timestamp
- `POST /scan` — trigger on-demand scan

> **Note:** The web and worker dynos have separate ephemeral filesystems on Heroku.
> Alerts from scheduled worker jobs appear in Telegram; alerts from on-demand scans
> appear on the dashboard. A shared Postgres store is a planned future improvement.

## Configuration

All config lives in `config/` YAML files. Every value can be overridden with environment variables (see `config.py`).

### `config/scanner.yaml`

| Setting | Strict (O3) | Relaxed (O2) |
|---|---|---|
| `price_min` / `price_max` | **$5** / $2,000 | $5 / $2,000 |
| `min_gap_pct` | **0.5%** | 0.0% |
| `min_premarket_volume` | **50,000** | 0 |
| `min_dollar_volume` | **$500K** | $50K |
| `max_spread_pct` | **2.0%** | 5.0% |
| `min_score` | **50** | 50 |
| `max_candidates` | **8** | 8 |

Midday filters:
- `min_relative_volume`: 1.0×
- `min_dollar_volume`: $500K
- `max_spread_pct`: 2.0%

### `config/risk.yaml`

| Setting | Default |
|---|---|
| `max_trades_per_day` | **8** |
| `o2_max_trades_per_day` | **2** |
| `daily_loss_lockout_pct` | 1.5% |
| `max_consecutive_losses` | **3** |
| `risk_per_trade_pct` | 0.5% |
| `fixed_stop_pct` | **2.5%** |

### `config/indicators.yaml`

| Setting | Default |
|---|---|
| `ema_fast` | 9 |
| `ema_slow` | 20 |
| `volume_spike_multiplier_morning` | **1.5×** |
| `volume_spike_multiplier_midday` | **1.3×** |

### `config/schedule.yaml`

```yaml
schedule:
  timezone: "America/New_York"
  night_research:  "20:00"
  morning_news:    "08:00"
  premarket_scan:  "08:45"
  midday_scan:     "12:00"
  close_scan:      "15:30"
```

## Usage

### CLI Commands

```bash
# Full-day run (legacy, mock data)
python -m tradingbot.cli run-day

# Full-day run (real Alpaca data)
python -m tradingbot.cli --real-data run-day

# Split commands (matches the live Heroku schedule)
python -m tradingbot.cli run-news       # Night / morning news research
python -m tradingbot.cli run-morning    # Pre-market scan
python -m tradingbot.cli run-midday     # Midday scan
python -m tradingbot.cli run-close      # Close scan

# Print scheduled times
python -m tradingbot.cli schedule
```

### Understanding Results

Each session report shows all three options side-by-side.  
**Zero results in Option 3 on a quiet day is expected** — strict filters protect capital, and the recommendation will point you to Option 1 or 2 instead.

### TradeCard Fields

```csv
symbol, side, score, entry_price, stop_price, tp1_price, tp2_price,
invalidation_price, session_tag, reason, patterns, risk_reward, generated_at
```

`risk_reward` is always 2.0 (TP2 = entry ± 2 × risk), confirming a 1R:2R structure.

### Diagnostics

```bash
python diagnostic.py   # Shows raw Alpaca data and per-symbol filter decisions
```

## Project Status (April 3, 2026)

| Phase | Status | Description |
|---|---|---|
| Phase 1 | ✅ Complete | Scanner, indicators, risk manager, mock data |
| Phase 2 | ✅ Complete | Alpaca API, news aggregation, catalyst scoring |
| Phase 3 | ✅ Complete | 3-option system, market-condition recommendations |
| Phase 4 | ✅ Complete | CLI split, SEC/RSS/social news, smart money tracking |
| Phase 5 | ✅ Complete | Heroku + Render deployment |
| Phase 6 | ✅ Complete | Technical indicators: RSI, MACD, ATR, Bollinger, OBV |
| Phase 7 | ✅ Complete | Telegram bot — trade cards, news summaries, error alerts |
| Phase 8 | ✅ Complete | Flask web dashboard, on-demand scan, alert card UI |
| Phase 9 | ✅ Complete | Supabase persistence, trade tracking with PnL outcomes |
| Phase 10 | ✅ Complete | Performance analysis, filter hardening, inverse ETF blocker |

**Current state:**
- 131 tests passing
- Heroku live (web only): `aztradingbot-c8a5462555f3.herokuapp.com`
- Render handles all scheduled cron jobs (Heroku worker OFF)
- Supabase stores alerts + trade outcomes with exit price and PnL
- Market guard (SPY/QQQ), inverse/VIX ETF blocker, gap fade detection, fakeout guard
- Confluence engine (5-factor institutional scoring) with A–F grading

## Next: Execution Engine (MVP)

Full automated paper trading via Alpaca's Trading API. See `docs/EXECUTION_ENGINE_PLAN.md`.

## Planned Improvements

| Feature | Priority |
|---|---|
| Higher-timeframe trend filter | High — biggest single edge improvement |
| Volume decay detection | Medium |
| Dynamic R:R by score | Medium |
| Sector correlation filter | Low |
| Entry timing signal (pullback zones) | Low |

See `docs/IMPROVEMENTS.md` for the full tracker with validation verdicts.

⚠️ **Never use for live trading without extensive paper-trading validation first.**

## Troubleshooting

**"No setups found" in all options:**
- Normal on extremely quiet days
- Run `python diagnostic.py` to inspect raw Alpaca data
- Check that `catalyst_scores.json` exists (run `run-news` first before scan commands)

**Telegram alerts not arriving:**
- Verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set in Heroku/Render Config Vars
- Check Render cron job logs for errors
- Telegram has rate limits — bot uses 1.5s delay + retry logic

**Duplicate alerts:**
- Ensure only ONE scheduler is active (set `WORKER_ENABLED=false` on Heroku if Render handles crons)

**API errors:**
- Verify credentials in `config/broker.yaml` (local) or Heroku/Render Config Vars (cloud)
- Ensure `ALPACA_PAPER=true` is set
- Check Alpaca account status at https://alpaca.markets

## Security

- `config/broker.yaml` is in `.gitignore` — never commit live credentials
- Use `config/broker.example.yaml` as a template
- All secrets live in Heroku Config Vars in production

## Documentation

| File | Contents |
|---|---|
| [README.md](README.md) | This file |
| [CLAUDE.md](CLAUDE.md) | AI assistant context for this repository |
| [docs/ALGORITHM.md](docs/ALGORITHM.md) | Full trading pipeline, scoring, filters, business rules |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Deployment, scheduling, file persistence, troubleshooting |
| [docs/IMPROVEMENTS.md](docs/IMPROVEMENTS.md) | Algorithm improvement tracker with validation verdicts |
| [docs/EXECUTION_ENGINE_PLAN.md](docs/EXECUTION_ENGINE_PLAN.md) | Automated order execution plan (next MVP) |
| [config/broker.example.yaml](config/broker.example.yaml) | API credential template |

## Notes

- Mock mode works without any API credentials
- All tests pass (`pytest tests/ -v` → 131 passed)
- Heroku slug is under 1 GB (torch/transformers excluded; FinBERT degrades gracefully)
- Python version pinned to 3.10 via `.python-version`
