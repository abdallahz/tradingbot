# Trading Algorithm — Full Pipeline

## Overview

This is a **gap-and-go momentum alert system** (long-only). It scans for stocks that gapped up overnight on news/catalysts, confirms the move has real participation, then generates trade card alerts with entry/stop/targets. No trades are executed — alerts go to Telegram and the web dashboard.

---

## Step 1: Night Research (runs ~10 PM ET)

**Purpose**: Find *why* a stock might move tomorrow.

- Pulls the full tradable universe from Alpaca (~8,000 stocks)
- Scrapes news sources and scores each stock 0-100 as a **catalyst score**
- Saves the top picks to `catalyst_scores.json` for morning use

### News Sources & Relevance Scoring

| Source | Base Score | Notes |
|--------|-----------|-------|
| SEC EDGAR 8-K filings | 70-85 | Higher if `is_significant` flag set |
| RSS feeds | 50 ± 20 | Adjusted by sentiment confidence |
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

### Insider Tracking (optional enrichment)

- Tracks SEC Form 4 filings (insider buys/sells)
- **Significant titles**: CEO, CFO, COO, CTO, President, Chairman, Director, 10% Owner
- **Significant transactions**: P (purchase), M (option exercise), A (grant)
- Min transaction value: $50,000 (configurable)
- SEC rate limit: 0.15s between requests (10 req/sec max)
- Consecutive failure limit: 5 timeouts → stops fetching

**Output**: A ranked list of ~50 stocks with the highest catalyst activity. These become tomorrow's scan universe.

**Key files**: `src/tradingbot/research/news_aggregator.py`, `src/tradingbot/research/catalyst_scorer.py`, `src/tradingbot/research/insider_tracking.py`

---

## Step 2: Snapshot Construction (from Alpaca API)

**Purpose**: Build a `SymbolSnapshot` for each catalyst stock with all the data needed for scoring.

Before any scanning or ranking, the system fetches raw market data from Alpaca and computes derived fields. This is where the core metrics originate.

### Gap Calculation

```
gap_pct = ((current_price - prev_close) / prev_close) × 100
```

- `prev_close` comes from the daily bar before today

### Volume Metrics

| Metric | Formula | Notes |
|--------|---------|-------|
| Relative volume | `premarket_vol / prev_day_volume` | Compares premarket to full prior day. Can overflow on thin stocks (see IMPROVEMENTS.md #3) |
| Dollar volume | `prev_volume × prev_close` (preferred) | Fallback: `premarket_vol × price × 5` (assumes premarket ≈ 20% of day) |
| Recent volume | `snap.minute_bar.volume` if trusted | Sanity gate: only trusted if `minute_vol >= avg_volume_20 × 0.1` — prevents stale after-hours prints from inflating volume spike scores. Falls back to `premarket_vol` if untrusted |

### Support & Resistance Levels

This is the most complex part of snapshot construction. Two modes:

**Breakout Mode** (price >= 99.5% of premarket high):
- `key_support = premarket_high - 0.25 × ATR`
- `key_resistance = daily_resistance` (if within 2×ATR above price), else `prev_day_high` (if within 2×ATR), else `price + 2×ATR`

**Pullback Mode** (price < premarket high):
- Collects ALL support candidates: `[VWAP, EMA20, PM_low, prev_close, daily_support, prev_day_low]`
- Filters: only candidates between 0 and current price
- Discards candidates > 2×ATR below price (too far to be relevant)
- If >= 3 candidates: uses **2nd-lowest** (median defense — avoids the absolute bottom)
- If 1-2 candidates: uses lowest
- If none: uses `current_price - ATR`
- `key_resistance = highest of [premarket_high, prev_day_high (within 2×ATR), daily_resistance (within 2×ATR)]`

### Other Derived Fields

| Field | Formula | Used for |
|-------|---------|----------|
| Pullback low | `min(max(prev_close, ema20, pm_low), price - 0.5×ATR)` | EMA hold check, invalidation level |
| Reclaim level | `premarket_high` (fallback: VWAP → price) | VWAP reclaim check |

### Data Quality Filters

- Gap > 50%: flagged as probable data error, dropped silently with reason `"data_quality"`
- Spread > 10%: flagged as data quality issue, dropped

**Key file**: `src/tradingbot/data/alpaca_client.py`

---

## Step 3: Gap Scanner (runs at market open ~8:45 AM ET)

**Purpose**: Filter overnight universe down to stocks that actually moved.

Takes the ~50 catalyst stocks and applies 5 hard filters:

| Filter | Threshold | Why |
|--------|-----------|-----|
| Price range | $1 - $2,000 | Avoid penny stocks and illiquid mega-caps |
| Gap % | >= 0.5% (uses `abs()`) | Must have actually gapped — ⚠️ `abs()` lets negative gaps through in a long-only system (see IMPROVEMENTS.md #2) |
| Premarket volume | >= 50,000 shares | Confirms real participation |
| Dollar volume | >= $500K | Ensures enough liquidity to enter/exit |
| Spread | <= 2.0% | Execution cost must be manageable |

Any stock that fails is dropped with a reason tag (`price_out_of_range`, `gap_too_small`, `premarket_volume_too_low`, `dollar_volume_too_low`, `spread_too_wide`). Survivors move to ranking.

**Key file**: `src/tradingbot/scanner/gap_scanner.py`

---

## Step 4: Technical Indicator Computation

**Purpose**: Compute all technical indicators needed for ranking and pattern detection.

### Indicator Calculations

| Indicator | Method | Notes |
|-----------|--------|-------|
| EMA 9/20/50 | `ta` library (or pandas `ewm(adjust=False)` fallback) | EMA50 window capped at available bars |
| RSI (14) | `ta.momentum.RSIIndicator` | 14-period default |
| MACD | `ta.trend.MACD` (12, 26, 9) | Histogram = MACD - signal |
| Bollinger Bands | `ta.volatility.BollingerBands` (20, 2) | Used for BB overbought/oversold |
| OBV | `ta.volume.OnBalanceVolumeIndicator` | Latest value compared to prior |

### VWAP (critical — resets daily)

```
typical_price = (High + Low + Close) / 3
VWAP = sum(typical_price × volume) / sum(volume)
```

- **Resets daily**: filters bars by `timestamp.date()` to isolate today's session only
- If today has no bars: falls back to full-range VWAP
- This daily reset is key — VWAP from yesterday is meaningless for intraday

### ATR (Average True Range)

Two computation paths:
- **Daily ATR** (preferred): from 5-day bars, window = `min(14, available_bars)`
- **Intraday fallback**: 14-bar ATR on 15-min bars, then scaled by `√26` to approximate daily
  - Why √26? There are ~26 fifteen-minute bars per trading day (6.5 hours × 4 bars/hour)

### Support & Resistance from Bars

| Level | With daily bars | Without (intraday fallback) |
|-------|----------------|---------------------------|
| Support | 5-day low | Lowest low over last 52 bars (~2 trading days of 15-min data) |
| Resistance | 5-day high | Highest high over last 52 bars |
| Prior day high/low | From second-to-last daily bar | N/A |

### Signal Interpretation

The `interpret_signals()` function converts raw indicators into named signals:

| Signal | Condition |
|--------|-----------|
| `ema_bullish_alignment` | `price > ema9 > ema20` |
| `ema_bearish_alignment` | `price < ema9 < ema20` |
| `macd_bullish_cross` | `macd_hist > 0` |
| `macd_bearish_cross` | `macd_hist < 0` |
| `above_vwap` / `below_vwap` | Price vs VWAP |
| `rsi_oversold` | RSI < 35 |
| `rsi_overbought` | RSI > 70 |
| `bb_oversold` / `bb_overbought` | Price at/below lower band or at/above upper band |

**Key file**: `src/tradingbot/analysis/technical_indicators.py`

---

## Step 5: Ranking (multi-factor scoring, 0-100)

**Purpose**: Score each surviving stock to find the best setups.

### Scoring Functions (each returns 0-100)

**Gap Magnitude (17%)**:
```
base = min(1.0, log(1 + gap/3) / log(4)) × 100
if gap > 12%: base -= (gap - 12) × 5    # exhaustion penalty
```
- Sweet spot: 6-8%. Penalizes >12% (mean-reversion risk).

**Relative Volume (16%)**:
```
base = min(rv/2, 1) × 80              # 0-2x → 0-80 points
if rv > 2: base += min((rv-2)/8, 1) × 20  # 2-10x → bonus 0-20
```
- 2x = 80pts, 5x ≈ 95pts, 10x = 100pts. Diminishing returns above 2x.

**Catalyst Score (15%)**: Raw 0-100 from night research, passed through directly.

**Liquidity (11%)**:
```
spread_component = max(0, 1 - spread_pct/2) × 60%
dv_component = min(dollar_volume / $20M, 1) × 40%
score = (spread_component + dv_component) × 100
```
- $20M threshold is retail-scale, not institutional.

**RSI (10%)** — triangular curve peaking at RSI 60:
```
RSI 0-30:   score = rsi/30 × 50           (0 → 50)
RSI 30-60:  score = 50 + (rsi-30)/30 × 50 (50 → 100, PEAK)
RSI 60-80:  score = 100 - (rsi-60)/20 × 40 (100 → 60)
RSI 80-100: score = 60 - (rsi-80)/20 × 60  (60 → 0)
```
- ⚠️ Peaks at 60, but gap-and-go momentum stocks often have RSI 65-75 (see IMPROVEMENTS.md #5)

**Gap Quality (8%)**:
```
vol_factor: 3x+ relvol = 1.0, 2x = 0.85, 1.5x = 0.65, <1.5x = 0.35
gap_factor: ≤6% = 0.9-1.0, 6-10% = 0.8, >10% = max(0.3, 0.8 - (gap-10)×0.05)
base = vol_factor × gap_factor × 100
catalyst bonus: score ≥ 60 → +15, ≥ 40 → +5
```
- ⚠️ Step function at 6% boundary (see IMPROVEMENTS.md #6)

**Signal Alignment (7%)** — direction-aware:
- Determines expected direction from `gap_pct` (≥ 0 = expects long)
- Counts confirming vs opposing signals (from `interpret_signals()`)
- `alignment = confirming / total`, `score = alignment × 100`
- Count bonus: `min(signal_count / 4, 1)` — more signals = more confidence
- Prevents bearish-signal stocks from ranking high in a long-only system

**OBV Divergence (6%)**:
- With 10+ raw bars: computes OBV series from closes/volumes, checks slope direction
  - Price slope agrees with OBV slope → **80 pts** (confirmation)
  - Disagrees → **25 pts** (divergence)
- Fallback (no bars): gap direction vs OBV sign → 75 or 30
- No data → 50 (neutral)
- ⚠️ Binary scoring with no significance threshold (see IMPROVEMENTS.md #7)

**Momentum / VWAP Distance (5%)**:
```
distance = abs(price - vwap) / price
score = max(0, 1 - distance × 20) × 100
```
- Price at VWAP = 100, 5% away = 0.

**MACD (5%)**:
```
strength = abs(macd_hist) / price
score = min(strength / 0.01, 1) × 100
```
- Normalized relative to price so $5 and $500 stocks compare fairly.

### Final Score

```
score = 0.17×gap + 0.16×relvol + 0.15×catalyst + 0.11×liquidity + 0.10×rsi
      + 0.08×gap_quality + 0.07×signal_align + 0.06×obv + 0.05×momentum + 0.05×macd
```

Stocks below `min_score` (40) are dropped. Top 10 by score survive.

### CatalystWeightedRanker (Option 2)

```
score = 0.33×catalyst + 0.12×gap + 0.10×relvol + 0.09×liquidity + 0.08×gap_quality
      + 0.07×rsi + 0.06×obv + 0.05×momentum + 0.05×macd + 0.05×signal_align
```

Catalyst weight 33% (vs 15% in base). Used when pre-market technical data is thin.

**Key file**: `src/tradingbot/ranking/ranker.py`

---

## Step 6: Setup Confirmation (pullback signals)

**Purpose**: Verify each ranked stock has actual technical structure, not just a gap.

Two requirements must BOTH pass:

### Volume confirmation (at least one):
- **Volume spike**: `recent_volume >= max(1, avg_volume_20) × multiplier`
  - Morning multiplier: 1.5x (from `config/indicators.yaml`)
  - Midday multiplier: 1.3x — ⚠️ too low, noise at midday (see IMPROVEMENTS.md #8)
  - Multiplier = 0.0 disables the volume gate
- **Relative volume fallback**: `relative_volume >= multiplier AND premarket_volume >= 50,000`
  - More reliable when minute-bar data is stale/missing

### Directional signal (at least one):
- **EMA hold**: `pullback_low >= ema20 AND price >= ema9`
  - ⚠️ Too loose — allows stocks that broke EMA9 but held EMA20 to pass as "holds" (see IMPROVEMENTS.md #1)
- **VWAP reclaim**: `price >= vwap AND reclaim_level >= vwap`
  - `reclaim_level` = premarket high (structural level traders watch)
  - Both price AND reclaim_level must be above VWAP — ensures institutional demand at both levels

If a stock has volume but no directional signal, it's dropped.

**Exception**: In relaxed mode (Option 2), stocks with `catalyst >= 55` skip this check entirely (but still require positive gap).

**Key files**: `src/tradingbot/signals/pullback_setup.py`, `src/tradingbot/signals/indicators.py`

---

## Step 7: Pattern Detection & Confluence Scoring

**Purpose**: Detect chart patterns and score how many bullish signals are present.

### Pattern Detection Rules

| Pattern | Detection Logic |
|---------|----------------|
| **Bull flag** | 15-bar lookback. First half (pole): >= 3% upward move. Last 5 bars (flag): -3% to 0% pullback with strictly lower highs (monotonic decline) |
| **Breakout** | Price crosses resistance by > 0.2% (`price > resistance × 1.002`) AND `recent_volume >= 1.5× avg_volume_20` |
| **Support bounce** | Price within 1.5% of support (`abs(price - support) / support < 0.015`) AND upward momentum (`close[-1] > close[-2]`) AND mild volume uptick (`>= 1.2× average`) |
| **Hammer** | Small body (close > open), lower wick >= 2× body size, upper wick <= 0.3× body size |
| **Bullish engulfing** | Current candle fully engulfs prior candle's body, close > open |
| **Bearish engulfing** | Current candle fully engulfs prior candle's body, close < open |
| **Doji** | Body < 10% of total candle range (high - low) |
| **Above VWAP** | `price > vwap` (simple check) |

### Confluence Scoring

Each detected pattern gets a weight:

| Pattern | Points |
|---------|--------|
| Bull flag | +25 |
| Breakout | +20 |
| Bullish engulfing | +20 |
| Support bounce | +15 |
| Hammer | +15 |
| Above VWAP | +10 |
| Doji | -5 |
| Bearish engulfing | -30 |

**Confluence score** = sum of all detected pattern weights.

- If no patterns detected: score defaults to **15.0** (neutral — not penalized)
- Minimum confluence floor:
  - **Strict mode**: 10 (must have at least one meaningful bullish pattern)
  - **Relaxed mode**: 0 (only strong opposing signals like bearish engulfing kill it)
  - **Fakeout window (9:30-9:45 ET)**: raised to 15

**Key file**: `src/tradingbot/analysis/pattern_detector.py`

---

## Step 8: Card-Building Filters (`_build_cards`)

**Purpose**: Apply final gates before building the trade card. This is the most complex filter stage.

The filters run in this order inside `_build_cards`:

### 8a. Market Guard (first check — can halt everything)

Fetches live SPY & QQQ intraday performance. Uses the **worse** of the two:

| Regime | Threshold | Position Size | Stop Buffer | Effect |
|--------|-----------|---------------|-------------|--------|
| GREEN | SPY/QQQ > -0.5% | 1.0× (full) | 1.0× (normal) | Normal trading |
| YELLOW | -0.5% to -1.5% | 0.5× (half) | 1.5× (wider) | Reduced exposure |
| RED | < -1.5% | 0.0× (none) | 2.0× | **Halt ALL new entries** |

- If red: all cards dropped immediately, no further processing
- Data source: Alpaca SPY/QQQ snapshots (daily bar open vs latest trade)
- Fail-open: if data unavailable, assumes green

### 8b. Dedup Check

- Queries `get_today_alerted_symbols()` for all symbols alerted today
- If stock was already alerted, only re-alert if price pulled back significantly:
  ```
  distance_before = prev_entry - key_support
  distance_now = current_price - key_support
  pullback_pct = 1.0 - (distance_now / distance_before)
  ```
  - Must be >= 50% pullback toward support (materially better entry)

### 8c. ETF Limits

- Max **3 ETFs** per scan (`MAX_ETF_ALERTS = 3`, hardcoded)
- Max **1 per family**: e.g., only SPXL or UPRO, not both
- Family mapping via `get_etf_family(symbol)` from `etf_metadata.py`

### 8d. VWAP Distance Filter

```
vwap_dist_pct = abs(price - vwap) / vwap × 100
```

- Regime-adaptive threshold:
  - High vol: max 2.0%
  - Medium: max 3.0%
  - Low vol: max 4.0%
- Can be overridden by auto-tuner

### 8e. Catalyst Gate

If `catalyst_score < min_catalyst` (40, regime-adaptive):
- Requires **strong volume conviction** to pass:
  - `relative_volume >= 3×` AND `premarket_volume >= 100,000 shares`
- If both conditions met AND `has_valid_setup()` passes: allowed through
- Otherwise: dropped

### 8f. Pullback Setup Confirmation

- Calls `has_valid_setup(stock, volume_multiplier)` (see Step 6)
- **Relaxed mode bypass**: if `catalyst >= 55`, skips this check (but still requires positive gap)

### 8g. Confluence Check

- Runs pattern detection and confluence scoring (see Step 7)
- Must meet minimum floor (10 strict, 0 relaxed, 15 during fakeout window)
- **Score blending**: after passing, confluence is blended into the ranker score:
  ```
  card.score = min(100, card.score × 0.7 + confluence × 0.3)
  ```
  This 70/30 weighting rewards cards that have both strong ranking AND strong chart patterns.

### 8h. Fakeout Guard (9:30-9:45 ET, strict mode only)

During the first 15 minutes of market open:
- Confluence floor raised to **15** (vs normal 10)
- Stop distance widened by **20%**: `stop = entry - (stop_dist × 1.20)`
- This overwrites the level-based stop calculation
- Reason: opening cross creates wicks that routinely span 1-2× ATR

### 8i. AI Validation (optional)

- If enabled: sends card to OpenAI/Anthropic for a second opinion
- AI rates confidence 1-10; if below threshold: card rejected
- Cost ~$0.001 per card
- Not required for core functionality

**Key file**: `src/tradingbot/app/session_runner.py` (`_build_cards` method)

---

## Step 9: Trade Card Construction

**Purpose**: Build the actual alert with entry, stop, targets, and position size.

### Level Calculations

| Field | Formula | Notes |
|-------|---------|-------|
| Entry | `round(price, 2)` | Current market price |
| ATR buffer | `atr × 0.5` (fallback: `entry × 0.005`) | Cushion below support |
| Stop | `max(key_support - atr_buffer, entry × (1 - fixed_stop_pct/100))` | Level-based, but capped so risk never exceeds `fixed_stop_pct` (2.5%). The `max()` means the tighter cap always wins |
| TP1 | `min(key_resistance, entry + min(3×ATR, entry×0.06))` | Key resistance capped at tighter of 3×ATR or 6% of entry — prevents using daily levels that are unreachable intraday |
| TP2 | `tp1 + (entry - stop)` | 1R extension above TP1 |
| R:R | `(tp1 - entry) / (entry - stop)` | Based on TP1, not TP2. **Must be >= 1.5 or card is rejected** |

### Rejection Conditions

A card returns `None` (rejected) if:
- `key_resistance <= 0` (no resistance level set)
- `key_resistance <= entry` (resistance below current price — no room for long)
- `risk <= 0` (stop at or above entry)
- `R:R < 1.5` (reward too small relative to risk)

### Position Sizing

```
leverage = get_leverage_factor(symbol)    # 3x ETF = 3, normal stock = 1
adjusted_risk_pct = risk_per_trade_pct / leverage  # e.g., 0.5% / 3 = 0.167%
risk_dollars = account_value × (adjusted_risk_pct / 100)
position_size = risk_dollars / (entry - stop)
```

Safety caps:
- Max notional: `min($10,000, account × 50%)`
- If `position_size × entry > max_notional`: scale down

### Risk Assessment (`_assess_risk()`)

Penalty-based scoring (0-10 scale):

| Condition | Penalty |
|-----------|---------|
| Price < $3 (penny stock) | +2 |
| Price < $5 (small cap) | +1 |
| Spread > 1.5% (execution risk) | +2 |
| Spread > 0.8% (wider than ideal) | +1 |
| Dollar volume < $500K (thin) | +2 |
| Dollar volume < $2M (below average) | +1 |
| R:R < 2.0 (marginal reward) | +1 |
| ATR/price > 5% (highly volatile) | +1 |

Mapping: 0-2 → `"low"`, 3-4 → `"medium"`, 5+ → `"high"`

**Key file**: `src/tradingbot/strategy/trade_card.py`

---

## Step 10: Risk Management

Runs across the full session, not per-stock:

| Rule | Value | Effect |
|------|-------|--------|
| Max trades/day | 5-8 (regime-adaptive) | Hard stop after N alerts |
| Daily loss lockout | -1.5% | No more trades if down 1.5% for the day |
| Max consecutive losses | 3 | Lockout after 3 losses in a row |
| Streak sizing | See table below | Position size shrinks after each loss |
| Market guard | SPY/QQQ check | Yellow = 50% size, Red = no trades |

### Streak Sizing Multipliers

| Consecutive Losses | Size Multiplier |
|-------------------|-----------------|
| 0 | 1.00 (full) |
| 1 | 0.75 |
| 2 | 0.50 |
| 3+ | 0.35 (minimum before hard lockout) |

After a win, consecutive losses reset to 0 → back to full size.

**Key file**: `src/tradingbot/risk/risk_manager.py`

---

## Step 11: Three Options Output

Every scan produces 3 parallel watchlists:

| Option | Scanner | Ranker | Risk Budget | Who it's for |
|--------|---------|--------|-------------|--------------|
| O1 Night Research | None (top catalysts) | By catalyst score | N/A (info only) | "What to watch" |
| O2 Relaxed | Gap >= 0%, no vol/spread floor | CatalystWeightedRanker (33%) | Separate cap (3/day) | Catalyst believers |
| O3 Strict | Full filters | Standard Ranker | Main cap (5-8/day) | Technical setups |

### Market Condition Recommendation

The `MarketConditionAnalyzer` recommends which option based on live conditions:

| Condition | Recommendation |
|-----------|---------------|
| High vol (avg gap >= 3%, 5+ gappers) | "Use Strict" — `morning_premarket` |
| Low vol + catalyst data | "Use Night Research" |
| Low vol, no catalyst | "Wait for midday" — `midday_rescan` |
| Medium vol (3+ high-vol stocks) | `midday_rescan` |
| Medium vol (few stocks) | `morning_premarket` |

### Regime-Adaptive Thresholds

| Parameter | Low Vol | Medium | High Vol |
|-----------|---------|--------|----------|
| Max VWAP distance | 4.0% | 3.0% | 2.0% |
| Min catalyst score | 35 | 40 | 45 |
| Min relative volume | 2.5× | 3.0× | 3.5× |
| Max trades/day | 10 | 8 | 6 |

### Volatility Classification

| Regime | Conditions |
|--------|-----------|
| High | `avg_gap >= 3.0%` AND `gappers_count >= 5` |
| Medium | `avg_gap >= 1.5%` AND `gappers_count >= 3` |
| Low | Everything else |

**Key file**: `src/tradingbot/analysis/market_conditions.py`

---

## Step 12: Auto-Tuner (runs at session boot)

Looks at the last 20+ trades' outcomes in Supabase, calculates which thresholds are too tight or too loose, and adjusts.

### Tunable Parameters & Bounds

| Parameter | Min | Max |
|-----------|-----|-----|
| `min_catalyst_score` | 25 | 70 |
| `min_relative_volume` | 1.5 | 5.0 |
| `min_ranker_score` | 30 | 80 |
| `min_confluence_score` | 5 | 20 |
| `max_vwap_distance_pct` | 1.5 | 6.0 |
| `max_trades_per_day` | 4 | 15 |

### Logic

1. Runs `Backtester.run()` to get historical performance
2. Analyzes per-filter and per-bucket performance
3. Recommends tightening if too many false signals
4. Recommends loosening if too many missed opportunities
5. Only applies recommendations with **confidence >= 50%**
6. Requires minimum 20 trades for any recommendations

**Key file**: `src/tradingbot/analysis/auto_tuner.py`

---

## Step 13: Trade Outcome Tracking

**Purpose**: After alerts are sent, track whether trades hit TP1, TP2, or stopped out.

### Outcome Lifecycle

```
open → tp1_hit → tp2_hit   (upgrade path — wins)
open → stopped              (loss)
open → expired              (at 16:00 ET market close — neither hit)
```

### Tracking Logic

- **Price fetch fallback chain**:
  1. Latest trade (most reliable for IEX feed)
  2. Snapshot latest_trade + daily bar (catches gaps in feed)
  3. Latest quote bid/ask (last resort)
- **Seeding**: daily — creates new outcome records for today's alerted symbols
- **Tick frequency**: every 5 minutes during market hours
- Only checks symbols with open outcomes (API-efficient)

**Key file**: `src/tradingbot/tracking/trade_tracker.py`

---

## Step 14: Close/Hold Scanner (runs ~3:50 PM ET)

**Purpose**: Find stocks worth holding overnight based on end-of-day strength.

### Close Hold Scoring (weights to 100%)

| Factor | Weight | How it's scored |
|--------|--------|----------------|
| Momentum | 25% | Intraday % change, capped at 15% |
| Relative Volume | 20% | Capped at 5× |
| Catalyst | 20% | Raw catalyst score (0-100) |
| Technical | 15% | RSI + MACD + S/R proximity |
| Closing Strength | 10% | Position within session range (high=strong) |
| Liquidity | 10% | Spread + dollar volume |

### RSI Sub-scoring (within Technical)

| RSI Range | Points | Interpretation |
|-----------|--------|---------------|
| ≤ 30 | 90 | Oversold bounce opportunity |
| 50-70 | 80 | Healthy momentum |
| > 80 | 30 | Overbought, risky overnight |
| Other | 50 | Neutral |

- Min score: 40.0
- Max picks: 5 per close scan

**Key file**: `src/tradingbot/scanner/close_hold_scanner.py`

---

## Step 15: Chart Generation

**Purpose**: Generate candlestick charts attached to trade card alerts.

- Library: `mplfinance` with "nightclouds" dark theme
- Dimensions: 14" wide × 8" tall
- **Overlays**:
  - EMA9 (blue line)
  - EMA20 (orange line)
  - VWAP (purple dashed line)
  - Entry / Stop / TP1 / TP2 horizontal lines (from TradeCard)
  - Volume subplot
- Output: `outputs/charts/{symbol}_{YYYYMMDD_HHMM}.png`

**Key file**: `src/tradingbot/analysis/chart_generator.py`

---

## Persistence Layer

### Alert Store (Supabase + JSONL hybrid)

- **Primary**: Supabase insert
- **Fallback**: JSONL file at `ALERT_STORE_PATH` env var (or `outputs/alerts.jsonl`)
- Max JSONL records: 200 (auto-pruned)
- **Weekend skip**: checks if `trade_date` is Saturday/Sunday, skips saving
- **Schema compatibility**: tries insert with all columns first; if schema too old, retries without optional columns (e.g., `risk_level`)

### Sessions Table

- Written by the worker for every scan, even zero-card scans
- Used by the dashboard to show scan times and counts
- Enables scan-time filtering even when no alerts were generated

**Key file**: `src/tradingbot/web/alert_store.py`

---

## Scan Schedule

| Job | Time (ET) | What runs |
|-----|-----------|-----------|
| Night research | ~10 PM | News scraping, catalyst scoring |
| Pre-market scan | 8:45 AM | Morning 3-option scan |
| Midday scans | Every 30 min, 9:30 AM - 3 PM | Midday re-scan with stricter filters |
| Close scan | 3:50 PM | End-of-day recap + close/hold scanner |
| Tracker | Every 5 min during market hours | Tracks open positions for outcome recording |

---

## Configuration Files

| File | What it controls |
|------|-----------------|
| `config/scanner.yaml` | Gap scanner thresholds, ranker min_score, max_candidates, O2 relaxed thresholds, midday overrides |
| `config/risk.yaml` | Max trades, loss lockout, consecutive loss limit, stop %, risk % |
| `config/indicators.yaml` | EMA periods (9/20), volume spike multipliers (morning 1.5×, midday 1.3×), VWAP hold bars |
| `config/schedule.yaml` | Scan times |
| `config/broker.yaml` | Alpaca credentials, news source toggles, AI settings |

---

## Known Bugs & Issues

These are actively tracked in [IMPROVEMENTS.md](IMPROVEMENTS.md). Key ones affecting live results:

1. **`TradeCard.side` removed but still referenced** — `side` field was deleted from models in commit `e2babcb` but `telegram_notifier.py`, `cli.py`, `watchlist_report.py`, and `ai_trade_validator.py` still call `card.side`. These paths will crash at runtime.
2. **EMA hold too loose** — checks `pullback_low >= ema20` instead of `ema9`, allowing broken momentum structures to pass as "holds".
3. **Gap scanner uses `abs()`** — long-only system accepts negative gaps, wasting ranking slots.
4. **No NaN guard in ranker** — a single NaN indicator silently kills the entire score, dropping valid candidates.
5. **Relative volume overflow** — illiquid stocks with tiny prior-day volume can score 100x relvol, appearing as high conviction.
6. **Midday volume multiplier too low** — 1.3x at midday is noise, not a real volume confirmation signal.
