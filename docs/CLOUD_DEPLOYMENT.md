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
