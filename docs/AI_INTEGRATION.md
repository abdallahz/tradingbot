# AI Integration Guide (Phase 6)

This document explains how to enhance your trading bot with AI-powered analysis and other ML/AI tools.

## 🤖 Phase 6A: AI Sentiment Analysis (Implemented)

### What it does
Replaces simple keyword matching with **LLM-powered sentiment analysis** of news headlines using OpenAI GPT or Anthropic Claude.

### Benefits
- **Deeper understanding**: LLMs can interpret context, sarcasm, and nuance
- **Better accuracy**: "Stock down on profit-taking" vs "Stock down on earnings miss" are both negative headlines, but AI understands the difference
- **Reasoning**: Get explanations for sentiment scores

### Setup

1. **Install required package

**:
```powershell
pip install openai        # For OpenAI GPT
# OR
pip install anthropic     # For Anthropic Claude
```

2. **Get API key**:
   - OpenAI: https://platform.openai.com/api-keys (pay-as-you-go, ~$0.0005/headline)
   - Anthropic: https://console.anthropic.com/ (similar pricing)

3. **Set environment variable**:
```powershell
# For OpenAI
$env:OPENAI_API_KEY="sk-proj-xxxxxxxxxxxxx"

# For Anthropic Claude
$env:ANTHROPIC_API_KEY="sk-ant-xxxxxxxxxxxxx"
```

4. **Enable in config** (`config/broker.yaml`):
```yaml
news:
  ai_sentiment_enabled: true
  ai_sentiment_provider: "openai"  # or "anthropic"
```

### Cost estimate
- **OpenAI gpt-4o-mini**: ~$0.0005 per headline
- **Anthropic claude-3-haiku**: ~$0.0004 per headline
- **Daily cost**: ~28 symbols × 3 headlines × 4 scans = **$0.17/day** (~$5/month)

### Fallback behavior
If API fails or quota exceeded, automatically falls back to keyword-based sentiment.

---

## 🔍 Other AI Trading Tools (Recommendations)

### 1. **Chart Pattern Recognition**

**Tool:** TensorTrade + Custom CNN
**What it does:** Detects head-and-shoulders, flags, triangles automatically
**Integration:** 
- Train CNN on labeled chart images
- Run on intraday charts from Alpaca
- Add pattern confidence score to `TradeCard`

**Implementation difficulty:** Medium
**Value:** High (visual patterns humans miss)

---

### 2. **Predictive Price Models**

**Tool:** Prophet (Facebook) or LSTM (TensorFlow/PyTorch)
**What it does:** Forecasts next-day price movement probability
**Integration:**
- Train on historical OHLCV data
- Output: probability(up), probability(down)
- Filter watchlist to symbols with >60% up probability

**Implementation difficulty:** High
**Value:** Very High (directional edge)

**Example libraries:**
- `fbprophet` (time series forecasting)
- `scikit-learn` (RandomForest classifier)
- `xgboost` (gradient boosting)

---

### 3. **Options Flow Analysis**

**Tool:** Unusual Whales API or OptionMetrics
**What it does:** Detects large institutional options trades (smart money)
**Integration:**
- Subscribe to options flow data
- Flag symbols with unusual call/put activity
- Boost catalyst score for symbols with institutional interest

**Implementation difficulty:** Medium
**Cost:** $50-200/month
**Value:** High (follow the smart money)

---

### 4. **Alternative Data Sources**

**Tool:** Quiver Quant, Social Blade, Google Trends
**What it does:** Non-traditional sentiment signals
**Examples:**
- Congressional trading (politicians' recent buys/sells)
- Google search volume spikes
- TikTok/Instagram finance influencer mentions
- Retail investor positioning (Robinhood API)

**Integration:**
- Add to social proxy signals
- Weight by data quality

**Implementation difficulty:** Low-Medium
**Value:** Medium-High (unique edge)

---

### 5. **Earnings Surprise Prediction**

**Tool:** Estimize API or custom ML model
**What it does:** Predicts whether company will beat/miss earnings
**Integration:**
- Fetch analyst estimates
- Run ML model on company fundamentals
- Boost score for predicted beats, filter predicted misses

**Implementation difficulty:** High
**Value:** Very High (earnings moves are predictable with good models)

---

### 6. **NLP News Summarization**

**Tool:** GPT-4 or Claude with longer context
**What it does:** Summarizes 10+ articles per symbol into actionable insights
**Integration:**
- Batch headlines per symbol
- Ask LLM: "Is this bullish/bearish overall? Key risks?"
- Add summary to playbook markdown

**Implementation difficulty:** Easy
**Cost:** ~$0.01 per symbol summary
**Value:** Medium (saves time reading)

---

### 7. **Risk Scoring with ML**

**Tool:** XGBoost + historical trade data
**What it does:** Predicts win rate based on setup features
**Integration:**
- Train on your past trades (if available)
- Features: gap%, volume, catalyst_score, time_of_day
- Output: expected_win_rate%
- Filter setups with <40% predicted win rate

**Implementation difficulty:** High
**Value:** Very High (prevents bad trades)

---

## 📊 Phase 6B: Chart Pattern Recognition (Coming Soon)

### Planned features
1. **Candlestick patterns**: Detect doji, hammer, engulfing
2. **Support/Resistance**: Auto-identify key levels
3. **Breakout detection**: Flag when price breaks resistance
4. **Trend strength**: Quantify momentum using AI

### Implementation approach
```python
# Pseudocode
from tradingbot.research.chart_ai import ChartPatternDetector

detector = ChartPatternDetector()
patterns = detector.analyze(symbol="AAPL", timeframe="5m")
# Returns: ["ascending_triangle", "volume_breakout"]
```

---

## 🚀 Recommended Roadmap

**Phase 6A** ✅ - AI Sentiment Analysis (completed)
**Phase 6B** - Chart Pattern Recognition (CNN-based)
**Phase 6C** - Predictive Price Model (LSTM/XGBoost)
**Phase 6D** - Options Flow Integration
**Phase 6E** - Alternative Data Sources

---

## ⚠️ Important Notes

1. **No over-fitting**: ML models trained on <100 trades will overfit. Need 500+ samples minimum.
2. **Data quality**: AI is only as good as training data. Bad data = bad predictions.
3. **Cost control**: Set daily API limits to avoid surprise bills.
4. **Regulatory**: Some data sources (e.g., congressional trades) have reporting delays - not real-time.
5. **Backtesting**: Always backtest AI changes before deploying to live automation.

---

## 📚 Resources

- **OpenAI API Docs**: https://platform.openai.com/docs
- **Anthropic Claude**: https://docs.anthropic.com/
- **FinBERT** (financial sentiment model): https://huggingface.co/ProsusAI/finbert
- **TensorTrade** (RL trading): https://github.com/tensortrade-org/tensortrade
- **TA-Lib** (technical indicators): https://github.com/mrjbq7/ta-lib
- **Alpaca ML Examples**: https://alpaca.markets/learn/machine-learning-trading/

---

## 💰 Cost Optimization Tips

1. **Batch API calls**: Process 10 headlines per
 API request (saves 90% vs 1-per-request)
2. **Cache results**: Store AI analysis for 4 hours, reuse across scans
3. **Use cheaper models**: `gpt-4o-mini` is 60x cheaper than `gpt-4` with similar accuracy for sentiment
4. **Rate limiting**: Set max 100 API calls/day to cap costs at ~$5/month
5. **Fallback logic**: Only use AI for high-catalyst stocks (score >60), use keywords for others

