# Operations & Deployment Guide

> **Last updated:** April 19, 2026
> Consolidated from: CLOUD_DEPLOYMENT.md, TASK_SCHEDULER.md, FILE_PERSISTENCE_GUIDE.md

## Deployment Architecture

```
VPS (178.156.202.27)                    Supabase (remote DB)
────────────────────────                    ────────────────────
news-night    (01:00 ET)                alerts, trade_outcomes,
news-premarket(08:00 ET)                sessions, close_picks
morning-scout (09:15 ET, alerts only)
morning-exec  (09:45 ET, orders)         Telegram (primary alerts)
intraday-scan (every 15m, 10:00–14:45)   ────────────────────
tracker       (every 2m)                Trade cards, summaries,
close-scan    (15:30 ET)                circuit breaker alerts
cleanup       (15:45 ET)
Flask dashboard (nginx+gunicorn)
IB Gateway (paper/live)
IBKR Execution Engine
```

**Current setup**: VPS handles ALL scheduled jobs + web dashboard + IBKR execution. Render decommissioned as of Apr 15. Heroku dashboard still active but secondary.

---

## Heroku Configuration (Secondary)

Heroku dashboard is still active but secondary. All crons and primary dashboard run on VPS.

### Procfile

```
web:    PYTHONPATH=src gunicorn --workers 2 --bind 0.0.0.0:$PORT tradingbot.web.app:app
worker: PYTHONPATH=src python -m tradingbot.app.worker
```

> **Note**: Worker dyno is OFF on Heroku. VPS handles scheduling. The `WORKER_ENABLED` env var gates the worker loop — set to `false` on Heroku to prevent duplicate scans.

### Required Environment Variables

| Variable | Description |
|----------|-------------|
| `ALPACA_API_KEY` | Alpaca paper/live API key |
| `ALPACA_API_SECRET` | Alpaca API secret |
| `ALPACA_PAPER` | `true` for paper trading |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase anon/service key |
| `SEC_USER_AGENT` | e.g. `TradingBot/1.0 (you@example.com)` |
| `WORKER_ENABLED` | `false` on Heroku (VPS handles crons) |
| `DATA_PROVIDER` | `alpaca` (default) or `ibkr` for VPS |

### Optional Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `NEWS_SOCIAL_PROXY_ENABLED` | `true` | Enable Stocktwits/Reddit signals |
| `NEWS_SEC_FILINGS` | `true` | Enable SEC EDGAR scraping |
| `NEWS_RSS_FEEDS` | `true` | Enable RSS feed scraping |
| `NEWS_MAX_AGE_HOURS` | `24` | News recency threshold |
| `DEBUG` | `false` | Show detailed validation logs |
| `DATA_PROVIDER` | `alpaca` | Set to `ibkr` on VPS for IBKR data |
| `EXECUTION_MODE` | — | `paper` or `live` (VPS only) |

### Deploy

```bash
git push heroku main
```

### Live URLs
- **Dashboard**: `https://aztradingbot-c8a5462555f3.herokuapp.com`
- **Health check**: `https://aztradingbot-c8a5462555f3.herokuapp.com/api/health`
- **Alerts API**: `https://aztradingbot-c8a5462555f3.herokuapp.com/api/alerts`

---

## Render.com Configuration (Decommissioned)

> **Note**: All cron jobs migrated to VPS as of Apr 15, 2026. Render config preserved in `render.yaml` for reference only.

### Original Cron Jobs (`render.yaml`)

Six cron services were defined in `render.yaml`, all UTC:

| Job | UTC Schedule | ET Equivalent | CLI Command |
|-----|-------------|---------------|-------------|
| news-night | `0 3 * * 1-5` | ~10 PM ET | `run-news` |
| news-premarket | `0 13 * * 1-5` | ~8 AM ET | `run-news` |
| morning-scan | `45 13 * * 1-5` | 8:45 AM ET | `run-morning` |
| intraday-scan | `*/15 13-19 * * 1-5` | 9 AM–3 PM ET (every 15 min) | `run-midday` |
| tracker | `*/2 13-20 * * 1-5` | 9 AM–4 PM ET (every 2 min) | trade tracker + circuit breaker |
| close-scan | `50 19 * * 1-5` | 3:50 PM ET | `run-close` |

### Setup (Historical)

1. Push repo to GitHub
2. In Render: **New +** → **Blueprint** → select repo with `render.yaml`
3. Set environment variables for each cron service (same as Heroku list above)
4. Verify first successful runs produce output files

---

## VPS Configuration (Primary)

### Crontab

All jobs run via `crontab -e` on VPS (`178.156.202.27`), all times UTC:

| Job | UTC Schedule | ET Equivalent | Command |
|-----|-------------|---------------|----------|
| news-night | `0 5 * * 1-5` | 1:00 AM ET | `run-news "Night Research"` |
| news-premarket | `0 12 * * 1-5` | 8:00 AM ET | `run-news "Pre-Market Research"` |
| morning-scout | `15 13 * * 1-5` | 9:15 AM ET | `run-scout` (alerts only, no execution) |
| morning-execute | `45 13 * * 1-5` | 9:45 AM ET | `run-execute` (re-scan with live data, place orders) |
| intraday-scan | `*/15 14-18 * * 1-5` | 10:00 AM–2:45 PM ET | `run-midday` |
| tracker | `*/2 13-20 * * 1-5` | 9 AM–4 PM ET (every 2 min) | `run-tracker` (+ circuit breaker) |
| close-scan | `30 19 * * 1-5` | 3:30 PM ET | `run-close` |
| cleanup | `45 19 * * 1-5` | 3:45 PM ET | `run-cleanup` (force-expire unfilled orders) |
| log-cleanup | `0 6 * * *` | Daily | `find logs -mtime +7 -delete` |
| ibgw-health | `*/5 8-21 * * 1-5` | 4 AM–5:59 PM ET | IB Gateway health check |
| scan-watchdog | `*/5 8-21 * * 1-5` | 4 AM–5:59 PM ET | Kill hung processes >8 min |

### Dashboard

- **nginx** → **gunicorn** on port 5000
- URL: `http://178.156.202.27`
- Features: dark theme, trade cards, open positions P&L panel, unrealized P&L %, dollar P&L
- Restart: `kill -HUP $(pgrep -f gunicorn)`

### Deploy

```bash
ssh root@178.156.202.27
cd /opt/tradingbot
git pull origin feature/ibkr-execution
kill -HUP $(pgrep -f gunicorn)  # reload dashboard
```

---

## Windows Task Scheduler (Local Dev)

### Scheduled Tasks

| Task | Time (ET) | Script |
|------|-----------|--------|
| News Research | 12:00 AM | `scripts/run_news.ps1` |
| Morning Scout | 9:15 AM | `scripts/run_morning.ps1 scout` |
| Morning Execute | 9:45 AM | `scripts/run_morning.ps1 execute` |
| Midday Scan | 12:00 PM | `scripts/run_midday.ps1` |
| Close Scan | 3:50 PM | `scripts/run_close.ps1` |

### Quick Commands

```powershell
# View all tasks
schtasks /Query /FO TABLE | Select-String "TradingBot"

# Run manually
schtasks /Run /TN "\TradingBot\TradingBot_Morning"

# Check status
schtasks /Query /TN "\TradingBot\TradingBot_News" /V /FO LIST

# View latest log
Get-Content (Get-ChildItem C:\tradingbot\logs\ | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
```

### Setup on New Machine

```powershell
# 1. Clone and install
git clone https://github.com/abdallahz/tradingbot.git C:\tradingbot
cd C:\tradingbot
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
pip install -e .

# 2. Configure API keys
# Edit config/broker.yaml using config/broker.example.yaml as template

# 3. Create scheduled tasks
$user = $env:USERNAME
schtasks /Create /TN "\TradingBot\TradingBot_News" /TR "powershell.exe -ExecutionPolicy Bypass -File 'C:\tradingbot\scripts\run_news.ps1'" /SC DAILY /ST 00:00 /RU $user /F
schtasks /Create /TN "\TradingBot\TradingBot_Morning" /TR "powershell.exe -ExecutionPolicy Bypass -File 'C:\tradingbot\scripts\run_morning.ps1'" /SC DAILY /ST 08:45 /RU $user /F
schtasks /Create /TN "\TradingBot\TradingBot_Midday" /TR "powershell.exe -ExecutionPolicy Bypass -File 'C:\tradingbot\scripts\run_midday.ps1'" /SC DAILY /ST 12:00 /RU $user /F
schtasks /Create /TN "\TradingBot\TradingBot_Close" /TR "powershell.exe -ExecutionPolicy Bypass -File 'C:\tradingbot\scripts\run_close.ps1'" /SC DAILY /ST 15:30 /RU $user /F

# 4. Test
schtasks /Run /TN "\TradingBot\TradingBot_News"
```

### Preflight Check

```powershell
powershell -ExecutionPolicy Bypass -File scripts/cloud_preflight.ps1
# Optional smoke test:
powershell -ExecutionPolicy Bypass -File scripts/cloud_preflight.ps1 -SmokeRun
```

---

## File Persistence & Archiving

### Output Files

```
outputs/
├── catalyst_scores.json                    # Night research scores (overwritten each run)
├── smart_money_signals_morning.json        # Smart money data per session
├── smart_money_signals_midday.json
├── smart_money_signals_close.json
├── social_proxy_signals_news.json
├── morning_watchlist.csv                   # Current session outputs (overwritten)
├── morning_playbook.md
├── midday_watchlist.csv / midday_playbook.md
├── close_watchlist.csv / close_playbook.md
├── daily_playbook.md
├── alerts.jsonl                            # Append-only alert log
├── charts/                                 # Generated candlestick charts
└── archive/
    └── YYYY-MM-DD/
        ├── INDEX.md                        # Auto-generated daily index
        ├── catalyst_scores_HHMMSS.json     # Timestamped copies
        ├── morning_watchlist_HHMMSS.csv
        ├── morning_playbook_HHMMSS.md
        └── ...
```

### What Gets Archived

Every run automatically creates a timestamped copy in `outputs/archive/YYYY-MM-DD/`:
- Catalyst scores, smart money signals
- Watchlists (CSV) and playbooks (MD)
- Auto-generated `INDEX.md` listing all files for that day

### Supabase Tables

| Table | Purpose |
|-------|---------|
| `alerts` | All trade card alerts (primary store) |
| `trade_outcomes` | Trade tracking results with entry/exit/PnL |
| `sessions` | Scan session metadata |
| `close_picks` | Close/hold scanner picks |

JSONL fallback (`outputs/alerts.jsonl`) if Supabase is unavailable.

### Source Tagging

Each alert is tagged with its source infrastructure:
- `render-alpaca` — from Render/VPS cron jobs using Alpaca data (default)
- `vps-ibkr` — from VPS using IBKR data (set `DATA_PROVIDER=ibkr`)

The source tag appears in Telegram messages as `[☁️ Render/Alpaca]` or `[🖥 VPS/IBKR]` and is stored in the Supabase `source` column.

---

## VPS / IBKR Architecture (feature branch)

```
VPS (178.156.202.27)                     Render (cron jobs)
────────────────────                     ──────────────────
IB Gateway (paper: DUP749086)            Same as above
IBKR Execution Engine                    Alerts only (no execution)
├── IBKRClient (656 lines)               DATA_PROVIDER=alpaca
├── CapitalAllocator (355 lines)
├── OrderExecutor (571 lines)
├── PositionMonitor (170 lines)
├── ExecutionManager (336 lines)
└── ExecutionTracker (182 lines)
       │
       └──→ Supabase (same tables + execution fields)
       └──→ Telegram (same channel, [🖥 VPS/IBKR] badge)
```

**Status**: All 13 modules implemented, 119 tests passing. Non-Professional market data APPROVED.

---

## Portfolio Circuit Breaker

The trade tracker includes a portfolio-level circuit breaker that fires before per-trade evaluation:

| Trigger | Threshold | Action |
|---------|-----------|--------|
| Portfolio drawdown | Combined unrealised loss ≥ 1.5% of account | Close all → `emergency_closed` |
| Market crash | SPY or QQQ down ≥ 2% intraday | Close all → `emergency_closed` |
| Correlated red | ≥ 75% of open trades losing (min 3) | Close all → `emergency_closed` |

- Fires once per session, sends Telegram alert with per-trade P&L breakdown
- Thresholds configurable via env vars: `CB_PORTFOLIO_DRAWDOWN_PCT`, `CB_MARKET_CRASH_PCT`, `CB_CORRELATED_RED_RATIO`
- Reuses `MarketGuard` for SPY/QQQ data

---

## Logging

Each task creates a timestamped log in `logs/`:
- Format: `{session}_{YYYYMMDD}_{HHMMSS}.log`
- Example: `news_20260306_112557.log`

### Maintenance

```powershell
# Review errors in recent logs
Get-ChildItem C:\tradingbot\logs\ -Filter "*.log" |
    ForEach-Object {
        $content = Get-Content $_.FullName -Raw
        if ($content -match "Error|Exception|Failed") {
            Write-Host $_.Name -ForegroundColor Red
        }
    }

# Clean old logs (>30 days)
Get-ChildItem C:\tradingbot\logs\ -Filter "*.log" |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
    Remove-Item

# Clean old archives (>90 days)
Get-ChildItem C:\tradingbot\outputs\archive\ |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-90) } |
    Remove-Item -Recurse
```

---

## Data Quality

Alpaca's free IEX tier occasionally returns stale/incorrect prices. Built-in validation:

- Extreme gaps (>50%) → filtered
- Wide spreads (>5%) → filtered
- Suspiciously low prices with high gaps → filtered
- Round/placeholder prices → filtered
- Enable `DEBUG=1` for validation warnings

> Consider upgrading to Alpaca's paid data tier ($9/mo) for real-time quotes if stale data is frequent.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "No setups found" all options | Normal on quiet days. Run `python diagnostic.py` to inspect raw data |
| Telegram alerts not arriving | Verify `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` env vars |
| Duplicate alerts | Ensure only ONE scheduler is active (VPS crontab only) |
| API errors | Check Alpaca keys, ensure `ALPACA_PAPER=true` |
| SEC 503 errors | Normal during high traffic; bot continues with other sources |
| Missing `catalyst_scores.json` | Run `run-news` first before scan commands |
| Dashboard empty | Check Supabase connection; restart gunicorn: `kill -HUP $(pgrep -f gunicorn)` |
| Circuit breaker false trigger | Adjust thresholds via `CB_*` env vars |
| VPS dashboard not updating | `ssh root@178.156.202.27 "kill -HUP $(pgrep -f gunicorn)"` |

### Security

- `config/broker.yaml` is in `.gitignore` — never commit credentials
- Use `config/broker.example.yaml` as a template
- All production secrets in VPS env vars (sourced from `.env`)
- VPS access via SSH key only
