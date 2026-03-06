# Cloud Deployment (Phase 5)

This project can now run in cloud cron jobs using environment variables only (no committed secrets).

## What was added

- Environment-variable overrides in config loading
- Render blueprint file: `render.yaml`
- Heroku-compatible process file: `Procfile`
- Example env file: `.env.example`

## Required environment variables

Set these in your cloud provider:

- `ALPACA_API_KEY`
- `ALPACA_API_SECRET`
- `ALPACA_PAPER` (usually `true`)
- `SEC_USER_AGENT` (required for SEC EDGAR requests)

## Local preflight check

Run before cloud deployment:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/cloud_preflight.ps1
```

Optional smoke test:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/cloud_preflight.ps1 -SmokeRun
```

Optional toggles:

- `NEWS_SOCIAL_PROXY_ENABLED=true`
- `NEWS_SEC_FILINGS=true`
- `NEWS_RSS_FEEDS=true`
- `NEWS_MAX_AGE_HOURS=24`

## Render deployment

### 1) Create service from blueprint

- Push repository to GitHub (already done)
- In Render, choose **New +** → **Blueprint**
- Select repository root containing `render.yaml`
- Confirm cron services

### 2) Configure secrets

For each cron service, set:

- `ALPACA_API_KEY`
- `ALPACA_API_SECRET`
- `SEC_USER_AGENT`

### 3) Validate schedules

`render.yaml` schedules use UTC:

- `0 5 * * *` → news (midnight ET approx; adjust for DST as needed)
- `45 13 * * *` → morning (8:45 ET)
- `0 17 * * *` → midday (12:00 ET)
- `50 20 * * *` → close (3:50 ET)

### 4) Verify first successful runs

After deployment, each cron job should produce:

- `outputs/catalyst_scores.json` from `run-news`
- session watchlists and playbooks from morning/midday/close runs

## Data quality validation

**Note:** Alpaca's free paper trading API occasionally returns stale or incorrect prices. The bot now includes automatic data quality validation:

**What it checks:**
- Extreme gaps (>50%) without obvious news
- Wide bid-ask spreads (>5%) indicating stale quotes
- Suspiciously low prices with high gaps
- Round/placeholder prices

**How it works:**
- Suspicious stocks are automatically filtered out
- You won't see bad data in your watchlists
- Enable `DEBUG=1` environment variable to see validation warnings in logs

**Examples of filtered data:**
```
[DEBUG] SOUN: price=$6.86, prev=$8.27, gap=-17.05% ⚠️ extreme_gap
[DEBUG] SOUN: Skipping due to suspicious data quality
```

**If you see frequent filtering:** Consider upgrading to Alpaca's paid market data tier ($9/mo) for real-time accurate quotes.

## Heroku-style option

Use `Procfile` with Scheduler add-on commands:

- `PYTHONPATH=src python -m tradingbot.cli --real-data run-news`
- `PYTHONPATH=src python -m tradingbot.cli --real-data run-morning`
- `PYTHONPATH=src python -m tradingbot.cli --real-data run-midday`
- `PYTHONPATH=src python -m tradingbot.cli --real-data run-close`

## Security checklist

- Rotate Alpaca keys before production
- Keep `config/broker.yaml` local only
- Use environment variables for all secrets
- Do not commit `.env`
