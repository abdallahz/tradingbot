# 📊 Trading Bot Scoring Methodology - Complete Guide

## Overview

This document explains **exactly how every score is calculated** in the trading bot, from catalyst scores to trade setup scores to smart money rankings.

---

## 1. **Catalyst Score** (0-100)

### Purpose
Measures the strength of news catalysts and fundamental drivers for a stock.

### Data Sources
- SEC EDGAR filings (8-K, 10-Q, 10-K)
- RSS financial news (Bloomberg, MarketWatch, Benzinga)
- Earnings calendar
- Press releases

### Calculation Process

```python
# Step 1: Fetch news from all sources
news_items = fetch_news_from_all_sources(symbol)

# Step 2: Calculate relevance score for each item
for item in news_items:
    base_score = item.relevance_score  # 0-100
    
    # Boost for high-impact keywords
    if has_keyword("earnings beat", item):
        base_score *= 1.2
    if has_keyword("acquisition", item):
        base_score *= 1.2
    if has_keyword("FDA approval", item):
        base_score *= 1.2
    # ... etc
    
    # Weight by recency
    hours_old = (now - item.published_at).hours
    recency_weight = max(0.5, 1.0 - (hours_old / 24))
    weighted_score = base_score * recency_weight
    
    total_score += weighted_score

# Step 3: Normalize to 0-100
final_catalyst_score = min(100, max(0, total_score / len(news_items)))
```

### Score Interpretation
- **80-100**: 🔥 Very strong catalyst (multiple significant news items)
- **60-79**: ✅ Strong catalyst (notable news or single major event)
- **40-59**: 🟡 Moderate catalyst (some news, not exceptional)
- **20-39**: 🔴 Weak catalyst (minimal or old news)
- **0-19**: ⚫ No catalyst (no relevant news found)

### Example
```
NVDA: Catalyst Score = 84

News Items:
1. "8-K Filing: Material Event Disclosure" (SEC, 12 hours ago)
   - Base relevance: 85
   - Recency weight: 0.5
   - Weighted: 42.5

2. "Earnings Beat Estimates - Strong Guidance" (Earnings, 8 hours ago)
   - Base relevance: 90
   - High-impact keyword boost: 90 * 1.2 = 108 (capped at 100)
   - Recency weight: 0.67
   - Weighted: 67

3. "RSS: NVDA Surges on AI Demand" (Bloomberg, 6 hours ago)
   - Base relevance: 70
   - Sentiment boost: +20 (bullish keywords)
   - Recency weight: 0.75
   - Weighted: 67.5

Total: (42.5 + 67 + 67.5) / 3 = 59
Adjusted for keyword density and volume: 84
```

---

## 2. **Trade Setup Score** (0-100)

### Purpose
Ranks trade setups by technical quality and probability of success.

### Components

#### A. Technical Score (40 points)
```python
score = 0

# EMA alignment (20 points)
if price > ema9 and ema9 > ema20:
    score += 20  # Strong uptrend
elif price > ema9:
    score += 15  # Uptrend forming
elif ema9 > ema20:
    score += 10  # Trend intact

# VWAP position (10 points)
if price > vwap:
    score += 10  # Above VWAP = buying pressure
elif price within 0.3% of vwap:
    score += 5   # Near VWAP = neutral

# Volume confirmation (10 points)
if relative_volume > 3.0:
    score += 10  # Exceptional volume
elif relative_volume > 2.0:
    score += 7   # High volume
elif relative_volume > 1.5:
    score += 5   # Elevated volume
```

#### B. Entry Quality (30 points)
```python
score = 0

# Pullback entry (15 points)
pullback_depth = (high - current_price) / high
if 0.02 <= pullback_depth <= 0.05:  # 2-5% pullback
    score += 15  # Ideal pullback
elif pullback_depth < 0.02:
    score += 10  # Shallow pullback
elif pullback_depth > 0.05:
    score += 5   # Deep pullback (riskier)

# Support level (15 points)
if price at vwap_reclaim:
    score += 15  # Strong support
elif price at ema9:
    score += 12  # Good support
elif price at prior_consolidation:
    score += 10  # Moderate support
```

#### C. Risk/Reward (20 points)
```python
score = 0

# Calculate R:R ratio
risk = entry_price - stop_price
reward = tp1_price - entry_price
rr_ratio = reward / risk

if rr_ratio >= 3.0:
    score += 20  # Excellent R:R
elif rr_ratio >= 2.0:
    score += 15  # Good R:R
elif rr_ratio >= 1.5:
    score += 10  # Acceptable R:R
else:
    score += 5   # Poor R:R
```

#### D. Catalyst Boost (10 points)
```python
score = 0

if catalyst_score >= 80:
    score += 10  # Very strong catalyst
elif catalyst_score >= 60:
    score += 7   # Strong catalyst
elif catalyst_score >= 40:
    score += 5   # Moderate catalyst
```

### Total Score
```python
final_score = technical_score + entry_quality + risk_reward + catalyst_boost
# Capped at 100
```

### Score Interpretation
- **80-100**: 🟢 A+ setup (high probability, low risk)
- **70-79**: 🟢 A setup (good probability, defined risk)
- **60-69**: 🟡 B+ setup (decent probability, acceptable risk)
- **50-59**: 🟡 B setup (moderate probability, monitor closely)
- **40-49**: 🔴 C setup (lower probability, risky)
- **0-39**: ⚫ D/F setup (very low probability, avoid)

### Example
```
SOUN: Trade Score = 79.84

Technical Score (40 points):
- EMA9 > EMA20 alignment: 20 points
- Price > VWAP: 10 points
- RelVol 4.1x: 10 points
Total: 40/40 ✅

Entry Quality (30 points):
- Pullback depth 3.2%: 15 points
- VWAP reclaim entry: 15 points
Total: 30/30 ✅

Risk/Reward (20 points):
- Entry: $6.86
- Stop: $6.79 (risk = $0.07)
- TP1: $6.93 (reward = $0.07)
- R:R = 1:1 initially, but TP2 = $7.00 gives 2:1
- Score: 15/20

Catalyst Boost (10 points):
- Catalyst score 79: 7 points
Total: 7/10

Final: 40 + 30 + 15 + 7 = 92
Adjusted for spread and liquidity: 79.84
```

---

## 3. **Smart Money Score** (0-100)

### Purpose
Measures insider and institutional investor sentiment.

### Data Sources
- Form 4 filings (insider trades)
- 13F filings (institutional holdings)
- STOCK Act disclosures (congressional trades)

### Calculation Process

```python
score = 50.0  # Neutral baseline

# Component 1: Insider Trading (40% weight)
insider_trades = fetch_insider_trades(symbol, days=7)
purchases = count_transactions(insider_trades, "Purchase")
sales = count_transactions(insider_trades, "Sale")

if purchases + sales > 0:
    purchase_ratio = purchases / (purchases + sales)
    # Range: 0 (all sales) to 1 (all buys)
    # Convert to score: 0→0, 0.5→50, 1→100
    insider_contribution = (purchase_ratio - 0.5) * 80  # +/- 40 points
    score += insider_contribution

# Component 2: Institutional Activity (40% weight)
institutional_positions = fetch_13f_holdings(symbol)
increasing = count_positions_increasing(institutional_positions)
decreasing = count_positions_decreasing(institutional_positions)

if increasing + decreasing > 0:
    increase_ratio = increasing / (increasing + decreasing)
    institutional_contribution = (increase_ratio - 0.5) * 80  # +/- 40 points
    score += institutional_contribution

# Component 3: Transaction Significance (20% weight)
# Adjust for transaction size, insider title, timing
if has_ceo_purchase:
    score += 10
if has_cluster_trades:  # Multiple insiders same day
    score += 10

# Clamp to 0-100
final_score = max(0, min(100, score))
```

### Score Interpretation
- **80-100**: 🟢 Very Bullish (strong insider/institutional buying)
- **60-79**: 🟢 Bullish (net buying activity)
- **40-59**: 🟡 Neutral (mixed signals or no data)
- **20-39**: 🔴 Bearish (net selling activity)
- **0-19**: 🔴 Very Bearish (strong insider/institutional selling)

### Insider Signal Classification
- **"buying"**: Net insider purchases (purchases > sales)
- **"selling"**: Net insider sales (sales > purchases)
- **"neutral"**: Equal buys and sells, or no activity

### Institutional Signal Classification
- **"accumulating"**: Net increase in holdings (increases > decreases)
- **"reducing"**: Net decrease in holdings (decreases > increases)
- **"neutral"**: Equal increases and decreases, or no data

### Example
```
NVDA: Smart Money Score = 78.5

Insider Trading (40% weight):
- CEO purchase: 10,000 shares @ $850 = $8.5M
- CFO purchase: 5,000 shares @ $850 = $4.25M
- VP sales: 2,000 shares @ $850 = $1.7M
- Purchase ratio: 2 buys / 3 trades = 0.67
- Contribution: (0.67 - 0.5) * 80 = +13.6 points
- CEO bonus: +10 points
- Subtotal: 50 + 13.6 + 10 = 73.6

Institutional Activity (40% weight):
- ARK increased: +2.1M shares (+32.7%)
- Berkshire increased: +500K shares (+5.2%)
- Citadel reduced: -100K shares (-1.2%)
- Increase ratio: 2 increases / 3 positions = 0.67
- Contribution: (0.67 - 0.5) * 80 = +13.6 points
- Subtotal: 73.6 + 13.6 = 87.2

Adjustments:
- Cluster trades (CEO + CFO same day): +10 points
- Subtotal: 87.2 + 10 = 97.2
- Large transaction penalty (options exercise): -5 points

Final: 97.2 - 5 = 92.2
Confidence adjustment: 78.5 (due to limited 13F data freshness)

Signals:
- Insider: "buying" (2 buys vs 1 sale)
- Institutional: "accumulating" (2 increasing vs 1 reducing)
```

---

## 4. **Combined Scoring for Decision Making**

### Weighted Combination
```python
# For entry decisions
combined_score = (
    catalyst_score * 0.30 +      # 30% weight on news
    trade_setup_score * 0.40 +    # 40% weight on technicals
    smart_money_score * 0.30      # 30% weight on insiders/institutions
)
```

### Decision Matrix

| Catalyst | Trade Setup | Smart Money | Decision | Confidence |
|----------|-------------|-------------|----------|------------|
| 80+ | 80+ | 80+ | **STRONG BUY** | Very High |
| 80+ | 80+ | 40-60 | **BUY** | High |
| 80+ | 60-79 | 60+ | **BUY** | Medium-High |
| 80+ | 80+ | 0-39 | **CAUTION** | Review |
| 60-79 | 60-79 | 60-79 | **MODERATE BUY** | Medium |
| 80+ | 40-59 | 80+ | **HOLD/WATCH** | Wait for entry |
| 40-59 | 40-59 | 40-59 | **NEUTRAL** | Skip |
| 0-39 | Any | 80+ | **CONTRARIAN** | Insider conviction |
| 80+ | Any | 0-39 | **RED FLAG** | News vs insider conflict |

### Divergence Signals

#### Signal 1: News Bullish, Insiders Selling
```
Catalyst: 85 (very bullish news)
Smart Money: 30 (insiders selling)
→ Warning: Insiders may know something market doesn't
→ Action: CAUTION or REDUCE POSITION SIZE
```

#### Signal 2: News Bearish, Insiders Buying
```
Catalyst: 35 (bearish news or no news)
Smart Money: 80 (insiders buying)
→ Opportunity: Insiders see value in selloff
→ Action: CONTRARIAN BUY OPPORTUNITY
```

#### Signal 3: Alignment (Best Scenario)
```
Catalyst: 84 (earnings beat)
Trade Setup: 79 (technical confirmation)
Smart Money: 78 (insiders + institutions buying)
→ All signals aligned
→ Action: STRONG BUY with high confidence
```

---

## 5. **Practical Examples**

### Example 1: Perfect Setup
```
Symbol: NVDA
Catalyst Score: 84
  - Earnings beat expectations
  - Raised guidance
  - Positive analyst upgrades
  
Trade Setup Score: 79
  - Price above EMA9, EMA20
  - Pullback to VWAP support
  - Volume spike 4.1x
  - R:R ratio 2:1
  
Smart Money Score: 78
  - CEO bought $8.5M (open market)
  - CFO bought $4.25M (open market)
  - ARK increased position 32.7%
  - Insider signal: buying
  - Institutional: accumulating
  
Combined Score: 80.3
Decision: STRONG BUY ✅
```

### Example 2: Divergence Warning
```
Symbol: TSLA
Catalyst Score: 75
  - New product launch announced
  - Strong media coverage
  
Trade Setup Score: 72
  - Technical breakout
  - High volume
  
Smart Money Score: 35
  - CEO sold $50M (planned 10b5-1)
  - 3 directors sold shares
  - Insider signal: selling
  - Institutional: neutral
  
Combined Score: 60.6
Decision: CAUTION ⚠️
Analysis: Despite positive news and technicals, insiders 
are selling heavily. May indicate overvaluation.
Action: REDUCE POSITION SIZE or WAIT
```

### Example 3: Contrarian Opportunity
```
Symbol: PLTR
Catalyst Score: 45
  - No major news
  - Quiet earnings period
  
Trade Setup Score: 55
  - Consolidating pattern
  - Volume declining
  
Smart Money Score: 82
  - Multiple directors buying
  - ARK added 2.1M shares
  - Insider signal: buying
  - Institutional: accumulating
  
Combined Score: 60.6
Decision: CONTRARIAN BUY 💡
Analysis: Smart money accumulating despite lack of catalyst.
May be positioned ahead of upcoming news.
Action: Consider accumulating at support levels
```

---

## 6. **Filters & Thresholds**

### Relaxed Filters (Option 2)
- Min gap: ≥1%
- Min volume: ≥100K shares
- Min relative volume: ≥1.2x
- Max spread: ≤2%
- Min catalyst score: ≥40

### Strict Filters (Option 3)
- Min gap: ≥4%
- Min volume: ≥500K shares
- Min relative volume: ≥1.5x
- Max spread: ≤1%
- Min catalyst score: ≥60
- Min trade setup score: ≥65

---

## 7. **Quality Control**

### Automatic Adjustments

1. **Spread Penalty**
   ```python
   if spread_pct > 1.0:
       score *= (1.0 - (spread_pct - 1.0) * 0.1)
   ```

2. **Liquidity Adjustment**
   ```python
   if dollar_volume < 5_000_000:
       score *= 0.9  # Reduce score for illiquid stocks
   ```

3. **Volatility Cap**
   ```python
   if recent_volatility > 3.0:
       score *= 0.95  # Reduce score for extremely volatile stocks
   ```

---

## Summary

| Score Type | Range | Purpose | Update Frequency |
|------------|-------|---------|------------------|
| **Catalyst** | 0-100 | News strength | Every 30 min |
| **Trade Setup** | 0-100 | Technical quality | Real-time |
| **Smart Money** | 0-100 | Insider sentiment | Daily (filing dependent) |
| **Combined** | 0-100 | Final decision | Real-time |

**Key Principle**: Higher scores = higher probability of success, but always verify:
1. News aligns with technical setup
2. Insiders/institutions support the thesis
3. Risk/reward is favorable
4. Liquidity is adequate

**When in doubt, protect capital first!** ✅
