# Trading Algorithm — Full Pipeline

> **Last updated:** April 17, 2026
> Consolidated from: ALGORITHM.md, SCORING_METHODOLOGY.md, SMART_MONEY_TRACKING.md, AI_INTEGRATION.md

## Overview

This is a **gap-and-go momentum alert system** (long-only). It scans for stocks that gapped up overnight on news/catalysts, confirms the move has real participation, then generates trade card alerts with entry/stop/targets. Alerts go to Telegram and the web dashboard. An optional IBKR execution engine (feature branch) can execute paper/live bracket orders.

**Design principles:**
- **Long-only**: No short setups. `TradeCard.side` is always `"long"`.
- **Alert-primary**: Main branch is alert-only. Feature branch adds optional IBKR execution.
- **Free indicators only**: Uses `ta` library (not torch/transformers) to stay within Heroku slug limits.
- **Telegram-primary**: Main notification channel; web dashboard is secondary.
- **Stateless workers**: Heroku/Render dynos can restart — Supabase is the persistent source of truth.

---

## Step 1: Night Research (runs ~10 PM ET)

**Purpose**: Find *why* a stock might move tomorrow.

- Pulls the full tradable universe from Alpaca (~8,000 stocks)
- Scrapes news sources and scores each stock 0–100 as a **catalyst score**
- Saves the top picks to `catalyst_scores.json` for morning use

### News Sources & Relevance Scoring

| Source | Base Score | Notes |
|--------|-----------|-------|
| SEC EDGAR 8-K filings | 70–85 | Higher if `is_significant` flag set |
| RSS feeds (Benzinga, Yahoo, MarketWatch) | 50 ± 20 | Adjusted by sentiment confidence |
| Social proxy (Stocktwits/Reddit) | Direct momentum score | Uses `social_momentum_score` field |
| Earnings calendar | 90 (today), 75 (5d), 65 (2wk) | Proximity-weighted |
| Press releases | Variable | Keyword-matched |

### Catalyst Scoring Formula (`CatalystScorerV2`)

```
final_score = max_item_score × 0.5 + mean_item_score × 0.3 + count_bonus × 0.2

count_bonus = min(100, 30 + item_count × 23.3)
```

- Rewards both quality (max + mean scores) and breadth (number of news items)
- **Keyword boosting**: high-impact words (earnings beat, acquisition, FDA approval) → 1.2× multiplier
- **Negative keywords**: (investigation, scandal, lawsuit) → 0.5× multiplier
- **Recency weighting**: `recency_weight = max(0.5, 1 - hours_old / max_age_hours)` — older articles count less
- **AI sentiment** (optional): sends headlines to OpenAI/Anthropic for sentiment analysis
- **Screener movers** (no prior catalyst data): assigned default catalyst = 30

### Smart Money Tracking (optional enrichment)

Three data sources tracked via `SmartMoneyTracker`:

| Source | Data | Signal |
|--------|------|--------|
| SEC Form 4 (corporate insiders) | CEO/CFO/Director buy/sell trades | Insider buys = bullish, multiple sells = bearish |
| SEC 13F (institutional investors) | Quarterly holdings of $100M+ AUM funds | Large position increases = institutional conviction |
| STOCK Act (congressional trades) | Senate/House member disclosures | Committee members trading related stocks = advance knowledge |

**Smart money score** (0–100): Weighted combination of insider trade signals, institutional position changes, and congressional activity. Integrated into catalyst scoring as enrichment.

**Tracked institutions**: Berkshire Hathaway, ARK Investment, Scion (Burry), Citadel (Griffin), Bridgewater (Dalio), Pershing Square (Ackman), and others.

**Key files**: `src/tradingbot/research/news_aggregator.py`, `src/tradingbot/research/catalyst_scorer.py`, `src/tradingbot/research/insider_tracking.py`

---

## Step 2: Snapshot Construction (from Alpaca API)

**Purpose**: Build a `SymbolSnapshot` for each catalyst stock with all the data needed for scoring.

### Gap Calculation

```
gap_pct = ((current_price - prev_close) / prev_close) × 100
```

- `prev_close` comes from the daily bar before today
- **Long-only**: Only positive gaps pass the scanner (no `abs()`)

### Volume Metrics

| Metric | Formula | Notes |
|--------|---------|-------|
| Relative volume | `premarket_vol / prev_day_volume` | Compares premarket to full prior day |
| Dollar volume | `prev_volume × prev_close` (preferred) | Fallback: `premarket_vol × price × 5` |
| Recent volume | `snap.minute_bar.volume` if trusted | Only trusted if `minute_vol >= avg_volume_20 × 0.1` |

### Support & Resistance Levels

**Breakout Mode** (price >= 99.5% of premarket high):
- `key_support = premarket_high - 0.25 × ATR`
- `key_resistance` = nearest of: daily resistance, prev day high, or `price + 2×ATR`

**Pullback Mode** (price < premarket high):
- Collects all support candidates: `[VWAP, EMA20, PM_low, prev_close, daily_support, prev_day_low]`
- Filters: only candidates between 0 and current price, within 2×ATR
- If >= 3 candidates: uses **2nd-lowest** (median defense)
- If 1–2 candidates: uses lowest
- If none: uses `current_price - ATR`
- `key_resistance` = highest of premarket high, prev day high, daily resistance (all within 2×ATR)

### Other Derived Fields

| Field | Formula | Used for |
|-------|---------|----------|
| Pullback low | `min(max(prev_close, ema20, pm_low), price - 0.5×ATR)` | EMA hold check, invalidation level |
| Reclaim level | `premarket_high` (fallback: VWAP → price) | VWAP reclaim check |

**Key file**: `src/tradingbot/data/alpaca_client.py`

---

## Step 3: Gap Scanner (filter pass)

**Purpose**: Remove stocks that don't meet minimum quality thresholds.

### Current Scanner Thresholds (`config/scanner.yaml`)

| Setting | Option 3 (Strict) | Option 2 (Relaxed) | Midday |
|---------|-------------------|---------------------|--------|
| `price_min` | **$5.00** | $5.00 | — |
| `price_max` | $2,000 | $2,000 | — |
| `min_gap_pct` | **0.5%** | 0.0% | — |
| `min_premarket_volume` | **50,000** | 0 | — |
| `min_dollar_volume` | **$500K** | $50K | $500K |
| `max_spread_pct` | **2.0%** | 5.0% | 2.0% |
| `min_score` | **50** | 50 | 50 |
| `max_candidates` | **8** | 8 | — |

- Long-only: no `abs()` — negative gaps fail naturally since they're below 0.5% threshold
- Scanner outputs a `ScanResult` with `candidates` and `dropped` lists

**Key file**: `src/tradingbot/scanner/gap_scanner.py`

---

## Step 4: Technical Indicators

**Purpose**: Compute indicators for scoring and setup confirmation.

### Indicators Computed (`config/indicators.yaml`)

| Indicator | Config | What it tells you |
|-----------|--------|-------------------|
| **EMA 9** (fast) | `ema_fast: 9` | Short-term trend |
| **EMA 20** (slow) | `ema_slow: 20` | Medium-term trend |
| **RSI(14)** | Built-in | Overbought (>70) / Oversold (<35) |
| **MACD** | Built-in | Trend direction + crossover signals |
| **ATR** | Built-in | Volatility — better stop-loss levels |
| **Bollinger Bands** | 20-period, 2 StdDev | Price extremes relative to 20-day average |
| **VWAP** | Built-in | Fair value for the day |
| **OBV** | Built-in | Volume confirms price moves |
| **Support/Resistance** | Auto-detected | Key levels from recent bars |

**Volume spike multipliers:**
- Morning: **1.5×** average volume
- Midday: **1.3×** average volume

**Key files**: `src/tradingbot/analysis/indicators.py`, `ta` library

### AI Tools (Optional)

| Tool | Cost | Status |
|------|------|--------|
| FinBERT (local sentiment) | Free | Available — downloads 500MB model |
| `ta` library | Free | **Integrated** — all indicators above |
| OpenAI GPT-4o-mini | ~$5/month | Optional — `ai_sentiment_enabled: true` |
| Anthropic Claude Haiku | ~$4/month | Optional — alternative LLM |

---

## Step 5: Ranking

**Purpose**: Score each candidate 0–100 and select the best setups.

### Base Ranker Weights (Option 3 — Strict)

| Component | Weight | Scoring Logic |
|-----------|--------|---------------|
| Gap magnitude | **15%** | Log curve peaks at ~6–8%, penalty above 12% |
| Catalyst score | **15%** | Raw 0–100 (NaN defaults to 30) |
| Relative volume | **13%** | 2× = 80pts, 5× = 95pts, 10+ = 100pts |
| Liquidity | **10%** | 60% spread quality + 40% dollar volume |
| RSI momentum | **9%** | Triangle: peaks at RSI=60, falls toward extremes |
| Gap quality | **8%** | Volume-confirmed gaps score higher; penalizes >10% gaps |
| Volume quality | **7%** | Classifies bars as accumulation/distribution/climax/thin |
| Signal alignment | **7%** | Confirms signals match expected trade direction |
| OBV divergence | **6%** | Volume confirms price moves (80 vs 25 binary) |
| Momentum | **5%** | Distance from VWAP |
| MACD | **5%** | Histogram strength normalized by price |
| **Total** | **100%** | |

### Catalyst-Weighted Ranker (Option 2 — Relaxed)

| Component | Weight | Notes |
|-----------|--------|-------|
| Catalyst score | **30%** | Doubled — pre-market tech data is sparse |
| Gap magnitude | 11% | |
| Relative volume | 9% | |
| Liquidity | 8% | |
| Gap quality | 8% | |
| RSI momentum | 7% | |
| Volume quality | 6% | |
| OBV divergence | 6% | |
| Signal alignment | 5% | |
| Momentum | 5% | |
| MACD | 5% | |

### NaN Protection

- `math.isfinite()` guards on RSI, MACD, and catalyst_score reads
- NaN/Inf defaults to 50.0 (neutral) for RSI/MACD, 30.0 for catalyst
- Prevents silent candidate drops from `ta` library early-bar NaN values

**Key file**: `src/tradingbot/ranking/ranker.py`

---

## Step 6: `_build_cards` Filter Chain

**Purpose**: Apply safety filters and generate trade cards from ranked candidates.

This is the core filter pipeline. Each candidate passes through **every gate in order**:

```
 1. Market Guard (red regime → block ALL entries)
 2. Yellow Regime Score Gate (+5 point floor on min_score)
 3. Risk Manager (daily trade limit, loss lockout, streak)
 4. Secondary Price Guard (hard floor at scanner.price_min = $5)
 5. Dedup Check (skip if already alerted today, unless 50% pullback)
 6. ALL ETF Blocker (all ETFs blocked — going long on ETFs has poor edge)
 7. ETF Family / Concentration Limit
 8. VWAP Distance Filter (session-adaptive: 3% morning, 5% midday/close)
 9. Gap Fade Detection (price < VWAP after positive gap → fading)
10. Daily EMA50 Trend Filter (block stocks below daily EMA50 — bear rally)
11. Pullback Setup / Indicator Confirmation
11. Catalyst Gate (min catalyst or strong volume override)
12. Relaxed Mode Bypass (catalyst >= 55 + positive gap)
13. Trade Card Construction (entry/stop/TP1/TP2)
14. R:R Floor Check (min 1.5:1)
15. Fakeout Guard (9:30–9:45 ET: confluence floor=15, stop +20%)
16. Pattern Confluence Check (MIN_CONFLUENCE_SCORE = 10)
17. AI Trade Validation (optional, paid API)
18. Confluence Engine (5-factor institutional scoring)
19. Grade-F Veto (composite < 40 blocked in strict mode)
20. Score Blending: 60% ranker + 40% confluence engine
21. Source Tagging (render-alpaca or vps-ibkr)
22. Chart Generation + Telegram Send
```

### Key Filter Details

#### Market Guard (`src/tradingbot/analysis/market_guard.py`)
Checks SPY/QQQ intraday performance:
- **GREEN**: worst > **-0.3%** → full size, normal stops
- **YELLOW**: worst -0.3% to -1.5% → 50% size, 1.5× stop buffer, +5 score penalty
- **RED**: worst < -1.5% → halt ALL new entries
- Fail-open: if data unavailable, defaults to green

#### Inverse/VIX ETF Blocker
Blocks in long-only mode (going long on inverse = short bet):
- **Inverse ETFs**: TZA, SQQQ, SPXS, SDOW, SPDN, SH, DOG, RWM, PSQ, SRTY, HDGE
- **VIX ETFs**: UVIX, UVXY
- Detection: `get_leverage_factor()` returns negative for inverse ETFs

> **Note (Apr 6+)**: ALL ETFs are now blocked, not just inverse/VIX. Going long on ETFs has poor edge for gap-and-go. The inverse/VIX blocker remains as a secondary safety net.

#### Daily EMA50 Trend Filter (added Apr 8)
- Fetches daily bars and computes 50-period EMA
- **Blocks** stocks gapping up below their daily EMA50 — these are bear rallies, not continuation
- Partially addresses the "higher-timeframe trend filter" backlog item (#9)

#### Gap Fade Detection
- If stock gapped up but current price < VWAP → gap is fading → blocked
- Skipped in relaxed mode (catalyst-driven entries tolerate drift)

#### Catalyst Gate
- Default min catalyst: 40 (adaptive via market conditions)
- **Volume override**: If catalyst < min BUT `relative_volume >= 3.0` AND `premarket_volume >= 100K` → pass
- Combines conviction from news + volume for final gate

#### Fakeout Guard (9:30–9:45 ET)
- Raises confluence floor to 15 (vs normal 10)
- Widens stop by 20% to survive opening wicks
- Only applies in strict mode

#### Confluence Engine (5-factor institutional scoring)
- **Volume profile**: Accumulation/distribution/climax/thin
- **Market trend**: SPY/QQQ direction
- **ATR exhaustion**: Price extension beyond normal range
- **Technical stack**: EMA/VWAP/RSI/MACD alignment
- **Catalyst backing**: News-driven moves have higher continuation

Grades: A (≥80), B (≥65), C (≥50), D (≥40), F (<40)
- Strict mode: Grade F is vetoed
- Score blending: `final_score = ranker × 0.60 + confluence × 0.40`

**Key file**: `src/tradingbot/app/session_runner.py`

---

## Step 7: Trade Card Construction

**Purpose**: Calculate exact entry, stop, and target prices.

### Trade Card Fields

| Field | Description |
|-------|-------------|
| `symbol` | Stock ticker |
| `side` | Always `"long"` |
| `score` | 0–100 (blended ranker + confluence) |
| `entry_price` | Current market price |
| `stop_price` | Below entry by ATR × buffer |
| `tp1_price` | Entry + 1× risk distance |
| `tp2_price` | Entry + 2× risk distance |
| `invalidation_price` | Key support (full thesis broken) |
| `risk_reward` | Calculated R:R ratio |
| `session_tag` | morning / midday / close |
| `patterns` | Detected chart patterns |
| `catalyst_score` | News catalyst score (0–100) |
| `confluence_grade` | A/B/C/D/F from confluence engine |
| `confluence_score` | 0–100 composite |
| `volume_classification` | accumulation / distribution / climax / thin |
| `chart_path` | Path to generated candlestick chart |
| `generated_at` | Timestamp |

### Stop Placement
- `stop = entry - (ATR × buffer × stop_buffer_multiplier)`
- Default buffer: 0.5× ATR
- Yellow regime: 1.5× buffer multiplier
- Fakeout window (9:30–9:45): additional 20% widening
- Fixed stop fallback: 2.5% from entry (`config/risk.yaml`)

### Target Placement
- Session-adaptive TP caps:
  - Morning: `max_tp_dist = min(2.5 × ATR, 4% of entry)`
  - Midday: `max_tp_dist = min(2.0 × ATR, 4% of entry)`
  - Close: `max_tp_dist = min(1.5 × ATR, 4% of entry)`
- **VWAP anchor**: If price is below VWAP, TP1 = VWAP (natural magnet) instead of key_resistance
- `TP1 = min(key_resistance, entry + max_tp_dist)`
- **Structural TP2**: Uses `key_resistance_2` (2nd resistance level) when available, else `TP1 + 1 × risk_distance`
- **Gap extension fallback**: If no intraday resistance found, uses `premarket_high + 0.5 × ATR`
- `invalidation = key_support` (below stop — full thesis broken)

**ATR calculation**: Uses **intraday ATR** (average high-low range of last 5 bars) instead of daily ATR for more responsive volatility measurement.

**TP1 cap history:**
- v1 (pre-Apr 6): `min(3×ATR, 6%)` — too wide, ETF losses
- v2 (Apr 6–9): `min(2×ATR, 3%)` — **broken**: max R:R = 3%/2.5% = 1.2 < MIN_RR 1.5, zero cards
- v3 (Apr 10–16): `min(2.5×ATR, 5%)` — max R:R = 2.0, sweet spot
- v4 (Apr 17+): Session-adaptive caps (see above) — tighter caps for later sessions

### R:R Floor
- Minimum R:R: **1.5:1** — cards below this are rejected (`build_trade_card()` returns `None`)
- Maximum R:R cap: **3.0:1** — unusually high R:R often means thin resistance

### Position Sizing
- Base risk per trade: 0.5% of account
- Yellow regime: 50% of base (0.25%)
- Streak scaling: 1 loss → 75%, 2 → 50%, 3 → 35%
- **Volume-scaled sizing**: relvol ≥3× → 1.5× position, relvol ≥2× → 1.25×
- Combined: `effective_risk = base × regime_multiplier × streak_multiplier × volume_multiplier`
- Account value configurable via `risk.yaml` `account_value` key or `ACCOUNT_VALUE` env var

**Key file**: `src/tradingbot/strategy/trade_card.py`

---

## Step 8: Risk Management

### Current Risk Config (`config/risk.yaml`)

| Setting | Value |
|---------|-------|
| `max_trades_per_day` | **8** |
| `o2_max_trades_per_day` | **2** (Option 2 separate cap) |
| `daily_loss_lockout_pct` | **1.5%** |
| `max_consecutive_losses` | **3** → locked out for day |
| `risk_per_trade_pct` | **0.5%** |
| `fixed_stop_pct` | **2.5%** |

### Streak Scaling

| Consecutive Losses | Size Multiplier |
|-------------------|-----------------|
| 0 | 100% |
| 1 | 75% |
| 2 | 50% |
| 3+ | Locked out |

### O2 Independent Budget
- Option 2 (relaxed) has `independent_cap=True`
- Trade count starts at 0 (doesn't consume O3's slots)
- Separate `RiskManager` with `o2_max_trades_per_day` cap

**Key file**: `src/tradingbot/risk/risk_manager.py`

---

## Step 9: Three-Option Watchlist System

Every scan session produces three parallel watchlists:

### Option 1 — Night Research (Catalyst-Driven)
- Top 10 stocks with highest catalyst scores from news research
- Enriched with smart money signals (insider trades, 13F, congressional)
- Best on **low-volatility days** when momentum is news-driven

### Option 2 — Relaxed Filters
- Uses `CatalystWeightedRanker` (catalyst weight = 30%)
- Indicator confirmation bypassed if `catalyst_score >= 55` and gap positive
- Separate daily budget (2 trades max via `o2_max_trades_per_day`)
- Best on **medium-volatility days** or when strict scanner is empty

### Option 3 — Strict Filters (High Probability)
- Full indicator confirmation required
- All filter gates enforced (confluence, fakeout guard, etc.)
- Best on **high-volatility days** with strong pre-market activity

### Market Condition Recommendation

`MarketConditionAnalyzer` reads the live snapshot universe:

| Market | Avg Gap | Recommendation |
|--------|---------|----------------|
| High volatility | ≥ 3%, 5+ gappers | Option 3 — Strict |
| Low volatility | < 1.5% | Option 1 — Night Research |
| Medium volatility | 1.5–3% | Option 2 or 3 based on signal count |

**Key files**: `src/tradingbot/analysis/market_conditions.py`, `src/tradingbot/models.py`

---

## Step 10: Trade Tracking & Outcomes

### Trailing Stop System

| Trigger | Action |
|---------|--------|
| 1R gain | Move stop to breakeven (entry price) |
| 2R gain | Move stop to entry + 1R |
| TP1 hit | Move stop to TP1 price |
| Price drops below trail | Stop triggered |
| 3:30 PM ET expire | Market sell at current price |

### Portfolio Circuit Breaker

The tracker runs a portfolio-level risk check **before** evaluating individual trades. Three independent triggers (any one fires):

| Trigger | Threshold | Action |
|---------|-----------|--------|
| Portfolio drawdown | Combined unrealised loss ≥ 1.5% of account | Close all → `emergency_closed` |
| Market crash | SPY or QQQ down ≥ 2% intraday | Close all → `emergency_closed` |
| Correlated red | ≥ 75% of open trades losing (min 3 trades) | Close all → `emergency_closed` |

- Fires once per session (no re-triggering after initial close)
- Sends Telegram alert with per-trade P&L breakdown
- Thresholds configurable via `CB_PORTFOLIO_DRAWDOWN_PCT`, `CB_MARKET_CRASH_PCT`, `CB_CORRELATED_RED_RATIO` env vars

### Outcome Recording

All trades tracked in Supabase `trade_outcomes` table with:
- Entry price, exit price, PnL percentage
- Exit reason (stop, TP1, TP2, trail, expire, emergency_closed)
- Duration, session tag, patterns
- Polling interval: every **2 minutes** during market hours (9 AM–4 PM ET)

**Key files**: `src/tradingbot/tracking/trade_tracker.py`, `src/tradingbot/web/alert_store.py`

---

## Step 11: Chart Generation & Persistence

### Charts
- Candlestick charts generated per trade card alert
- EMA9/EMA20 overlays, entry/stop/TP levels marked
- VWAP and support/resistance annotated
- Sent as Telegram image attachments
- Stored in `outputs/charts/`

### Output Files

| File | Session | Archived? |
|------|---------|-----------|
| `catalyst_scores.json` | Night research | Yes |
| `smart_money_signals_{session}.json` | Each session | Yes |
| `{session}_watchlist.csv` | Morning/Midday/Close | Yes |
| `{session}_playbook.md` | Morning/Midday/Close | Yes |
| `alerts.jsonl` | All sessions | Append-only |

### Archive Structure
```
outputs/archive/YYYY-MM-DD/
├── INDEX.md                          # Auto-generated index
├── catalyst_scores_HHMMSS.json
├── morning_watchlist_HHMMSS.csv
├── morning_playbook_HHMMSS.md
└── ...
```

### Supabase Tables
- `alerts` — all trade card alerts
- `trade_outcomes` — trade tracking results with PnL
- `sessions` — scan session metadata
- `close_picks` — close/hold scanner picks
- JSONL fallback if Supabase is unavailable

**Key files**: `src/tradingbot/analysis/chart_generator.py`, `src/tradingbot/web/alert_store.py`

---

## Data Quality Validation

Alpaca's free IEX tier occasionally returns stale or incorrect prices. Built-in checks:

- Extreme gaps (>50%) without obvious news → filtered
- Wide bid-ask spreads (>5%) indicating stale quotes → filtered
- Suspiciously low prices with high gaps → filtered
- Round/placeholder prices → filtered
- Enable `DEBUG=1` to see validation warnings in logs

---

## Known Limitations

1. **EMA hold check** uses `pullback_low >= ema20` which may be too loose (allows EMA9 breaks). Tightening to EMA9 may be too aggressive given ATR-based pullback_low computation. Deferred.
2. **Midday volume multiplier** (1.3×) is low but has a fallback path via relvol + premarket check.
3. ~~**No higher-timeframe trend filter**~~ — **Partially addressed** (Apr 8): Daily EMA50 trend filter blocks stocks below daily EMA50. Weekly trend check still in backlog.
4. **No volume decay detection** — snapshots are point-in-time, no tracking of fading participation.
5. **Midday scans underperform** — Apr 10 backtest showed 0% WR on midday vs 100% on morning. Gap momentum fades by midday.

See `docs/IMPROVEMENTS.md` for the full improvement tracker with validation verdicts and priorities.
