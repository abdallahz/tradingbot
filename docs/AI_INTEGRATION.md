# AI Integration Guide (Phase 6)

This document explains how to enhance your trading bot with AI-powered analysis and other ML/AI tools.

---

##  Free Tools (Implemented)

### 1. FinBERT  Free AI News Sentiment (No API Key)

**What it is:** A BERT model fine-tuned on financial text by Prosus AI.
**Hosted at:** https://huggingface.co/ProsusAI/finbert
**Cost:** 100% free  runs locally on your machine / cloud.

**Install:**
```powershell
pip install transformers torch
```

**Enable in `config/broker.yaml`:**
```yaml
news:
  ai_sentiment_enabled: true
  ai_sentiment_provider: "finbert"   # no API key needed
```

**How it works:**
- Downloads the model once (~500 MB cached locally)
- Classifies each headline as `positive / negative / neutral`
- Converts to a 0100 sentiment score
- Falls back to keyword analysis if `transformers` not installed

**Example improvement over keywords:**
> "GameStop surges despite analyst downgrades"  FinBERT: **positive** (price action focus)
> vs keywords: bearish (spotted "downgrade")

---

### 2. `ta` Library  Enhanced Technical Indicators (Free)

**What it is:** Pure-Python technical analysis library, 100+ indicators.
**GitHub:** https://github.com/bukosabino/ta
**Cost:** Free (already added to requirements.txt)

**Already integrated!** The bot now computes:

| Indicator | What it tells you |
|-----------|------------------|
| **RSI(14)** | Overbought (>70) / Oversold (<35) |
| **MACD** | Trend direction + crossover signals |
| **ATR** | Volatility  better stop-loss levels |
| **Bollinger Bands** | Price extremes relative to 20-day average |
| **EMA 9/20/50** | Short, medium, long trend alignment |
| **VWAP** | Fair value for the day |
| **OBV** | Volume confirms price moves |
| **Support/Resistance** | Auto-detected key levels from recent bars |

**With DEBUG=1**, you see all signals in logs:
```
[DEBUG] NVDA indicators: RSI=42.3, MACD=0.018, ATR=4.21, signals=['above_vwap', 'macd_bullish_cross']
```

---

### 3. Congressional Trades  Free Public Data

US politicians must disclose stock trades within 45 days (STOCK Act).
Already partially tracked via `smart_money_signals` in insider tracking.

**Free sources:**
- https://efts.sec.gov (SEC direct, free)
- https://www.quiverquant.com/sources/congresstrading (free tier)

---

##  Paid Tools (Future Reference)

Documented for future use when budget allows:

| Tool | Cost | Benefit |
|------|------|---------|
| OpenAI GPT-4o-mini sentiment | ~$5/month | Better context than FinBERT |
| Anthropic Claude Haiku sentiment | ~$4/month | Alternative LLM option |
| Unusual Whales (options flow) | $50-200/month | See institutional bets |
| Quiver Quant premium | $30-100/month | Extra alternative data |
| OptionMetrics | $200+/month | Deep options analytics |
| Estimize (earnings model) | $50+/month | Beat/miss prediction |

To enable paid LLM providers in the future:
```yaml
news:
  ai_sentiment_enabled: true
  ai_sentiment_provider: "openai"  # or "anthropic"
```
Set `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` environment variable.

**Cost estimate for paid LLMs:**
- OpenAI gpt-4o-mini: ~$0.0005/headline
- Daily: ~28 symbols x 3 headlines x 4 scans = ~$0.17/day (~$5/month)

---

##  Free Upgrade Roadmap

| Phase | Tool | Install | Impact |
|-------|------|---------|--------|
| **6A** done | `ta` indicators | `pip install ta` | Better entry/stop levels |
| **6B** done | FinBERT sentiment | `pip install transformers torch` | Smarter news scoring |
| **6C** | Chart pattern CNN | Local model training | Visual pattern detection |
| **6D** | RL trading agent | TensorTrade (free) | Self-optimizing strategy |

---

##  Notes

1. **FinBERT first run:** Downloads ~500 MB model. Subsequent runs instant (cached).
2. **Render cloud:** FinBERT may be slow on free tier (no GPU). Disable if run times exceed 30s.
3. **`ta` library:** Already active  no action needed beyond `pip install ta` locally.
4. **Fallbacks:** Everything degrades gracefully to keyword analysis if libraries not installed.
5. **Over-fitting warning:** ML models trained on <100 trades will overfit. Need 500+ samples.
