# Phase 2: Real Data Integration Guide

## Overview

Phase 2 adds Alpaca API integration and multi-source news aggregation to replace mock data while preserving the entire strategy/risk pipeline from Phase 1.

## New Features

### 1. Alpaca Market Data
- Real-time and historical stock prices
- Pre-market volume and gap calculations
- VWAP, EMA9, EMA20 indicators computed from bars
- Spread and liquidity metrics
- Tradable universe filtering

### 2. News Aggregation & Catalyst Scoring
- **SEC Filings**: 8-K, 10-Q, 10-K material events
- **Earnings Calendar**: Earnings beats, guidance updates
- **Press Releases**: Company announcements, wire news
- **Twitter/X**: (Optional) Sentiment and trending tickers
- **Reddit/Stocktwits**: (Optional) Community sentiment

### 3. Dual-Mode Operation
- **Mock Mode** (default): Uses sample data, no API keys needed
- **Real Data Mode**: Fetches live market data and news via `--real-data` flag

## Setup

### Step 1: Get Alpaca Credentials

1. Sign up for free paper trading at https://alpaca.markets
2. Generate API keys from the dashboard
3. Copy your API Key and Secret Key

### Step 2: Configure Credentials

Edit `config/broker.yaml`:

```yaml
alpaca:
  api_key: "YOUR_ALPACA_API_KEY"
  api_secret: "YOUR_ALPACA_SECRET_KEY"
  paper: true  # Keep as true for paper trading
```

### Step 3: Enable News Sources

In `config/broker.yaml`, enable desired news sources:

```yaml
news:
  sec_filings: true
  earnings_calendar: true
  press_releases: true
  twitter_enabled: false  # Requires Twitter API
  reddit_enabled: false
  max_age_hours: 24
```

## Usage

### Run with Mock Data (No API Keys Required)

```bash
python -m tradingbot.cli run-day
```

Output: `[MOCK DATA] Morning cards: 1 | Midday cards: 0 | ...`

### Run with Real Alpaca Data

```bash
python -m tradingbot.cli run-day --real-data
```

Output: `[REAL DATA] Morning cards: X | Midday cards: Y | ...`

### Check Schedule

```bash
python -m tradingbot.cli schedule
```

Output: `TZ=America/New_York | night=20:00 | premarket=08:45 | midday=11:00 | eod=15:50`

## Real Data Flow

### Morning (08:45 ET)
1. **Night Research** (20:00 previous day): News aggregator scores all symbols in tradable universe
2. **Universe Filter**: Keep only symbols with catalyst score >= 60
3. **Alpaca Fetch**: Get pre-market snapshots for filtered universe
4. **Apply Scores**: Attach catalyst scores to each snapshot
5. **Scanner**: Apply gap filters (gap >= 4%, volume >= 500k, etc.)
6. **Ranker**: Composite scoring with news weight
7. **Signals**: 3-indicator confirmation (Volume + EMA + VWAP)
8. **Risk Gate**: Enforce trade count and loss limits
9. **Output**: Morning watchlist with entry/TP/SL prices

### Midday (11:00 ET)
1. **Re-fetch**: Get updated snapshots for same universe
2. **Stricter Filters**: Higher RVOL, tighter spreads, higher score threshold
3. **Fill Remaining Slots**: Only emit cards if daily trade budget allows
4. **Output**: Midday watchlist

## News Scoring Logic

Each symbol receives a catalyst score (0-100):

- **Base Score**: Average relevance from all news items
- **Keyword Boost**: +20% for high-impact keywords ("earnings beat", "FDA approval", etc.)
- **Recency Weight**: Decays linearly over 24 hours
- **No News**: Defaults to 50 (neutral baseline)

Example:
- Symbol with "Earnings Beat Estimates" from 8 hours ago: ~85-95 score
- Symbol with routine PR from 20 hours ago: ~60-70 score
- Symbol with no news: 50 score

## Current Limitations (MVP)

1. **News Sources**: Currently use mocked data; production would integrate:
   - SEC EDGAR API for filings
   - Financial Modeling Prep or Alpha Vantage for earnings
   - Alpaca News API or similar for press releases
   - Twitter API v2 for social sentiment

2. **Indicator Computation**: VWAP and pullback levels are simplified; production needs:
   - Intraday 1-minute bars for precise VWAP
   - Real-time opening range tracking
   - Dynamic pullback detection from live tape

3. **Universe**: Hardcoded to ~30 liquid stocks; production would:
   - Query Alpaca assets API for all tradable symbols
   - Apply minimum liquidity filters dynamically
   - Monitor halts and restricts in real-time

## Testing

Run full test suite:

```bash
pytest tests/ -v
```

Tests verify:
- Mock mode backward compatibility
- Real data mode initialization
- News aggregator and catalyst scoring
- Risk controls and trade card generation

## Next Steps

To fully activate real data mode:

1. Add real news API integrations in `src/tradingbot/research/news_aggregator.py`
2. Enhance indicator computation with intraday bars in `src/tradingbot/data/alpaca_client.py`
3. Add dynamic universe fetching from Alpaca assets
4. Implement watchlist refresh logic for market hours
5. Add real-time alert delivery (email, Telegram, Slack)

## Security Note

**Never commit `config/broker.yaml` with real credentials to version control.**

Add to `.gitignore`:
```
config/broker.yaml
```

Keep a separate `config/broker.example.yaml` with placeholder values for documentation.
