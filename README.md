# TradingBot MVP (Alert-Only)

Your day trading assistant that generates 2-3 stock opportunities daily targeting 3-5% gains. Designed for traders who make quick entries on pullbacks and cash out same-day.

## What This Tool Does

**Daily Workflow:**
1. **Night Research** (20:00 ET): Scores stocks based on news catalysts (SEC filings, earnings, press releases)
2. **Pre-Market Scan** (08:45 ET): Finds gappers with strong momentum (gap ≥4%, volume ≥500k)
3. **Signal Confirmation**: Validates setups using 3 indicators (Volume spike + EMA9/EMA20 + VWAP)
4. **Pullback Entry**: Waits for healthy pullback to support before triggering entry
5. **Trade Cards**: Outputs exact entry price, TP1, TP2, and 1% stop-loss for each setup
6. **Midday Re-Scan** (11:00 ET): Finds fresh opportunities with stricter filters
7. **Risk Management**: Enforces max 2-3 trades/day, 1.5% daily loss lockout, consecutive loss limits

**Outputs Generated:**
- `outputs/morning_watchlist.csv` - Pre-market opportunities
- `outputs/midday_watchlist.csv` - Mid-session opportunities  
- `outputs/daily_playbook.md` - Human-readable summary

**Implementation:**
- **Phase 1**: Config-driven scanner with mock data (complete)
- **Phase 2**: Alpaca API + multi-source news aggregation (complete, tested)

See [PHASE2_GUIDE.md](PHASE2_GUIDE.md) for detailed integration documentation.

## Quick start

1. Create and activate virtual environment:

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

3. Configure API credentials (optional for real data):

Edit `config/broker.yaml` and add your Alpaca credentials:
```yaml
alpaca:
  api_key: "YOUR_ALPACA_API_KEY"
  api_secret: "YOUR_ALPACA_SECRET_KEY"
  paper: true
```

Get free paper trading credentials at https://alpaca.markets

4. Generate today's watchlists:

```bash
# Option A: All-in-one command (legacy)
python -m tradingbot.cli run-day              # Mock data
python -m tradingbot.cli run-day --real-data  # Real Alpaca data

# Option B: Split commands for scheduled runs (Phase 4A)
# Step 1: Run news research (8 PM night / 8 AM morning)
python -m tradingbot.cli run-news

# Step 2: Run individual scans using cached news scores
python -m tradingbot.cli run-morning  # 8:45 AM pre-market
python -m tradingbot.cli run-midday   # 12:00 PM midday
python -m tradingbot.cli run-close    # 3:50 PM close

# Check schedule configuration
python -m tradingbot.cli schedule
```

5. Optional: cloud cron deployment (Phase 5)

Set environment variables (instead of committing secrets):
```bash
ALPACA_API_KEY=...
ALPACA_API_SECRET=...
ALPACA_PAPER=true
SEC_USER_AGENT="TradingBot/1.0 (you@example.com)"
```

Use the included cloud files:
- `render.yaml` (Render cron blueprint)
- `Procfile` (Heroku-style scheduler command)
- `.env.example` (variable template)

See [docs/CLOUD_DEPLOYMENT.md](docs/CLOUD_DEPLOYMENT.md) for full setup.

**Outputs:**
- `run-news`: `outputs/catalyst_scores.json`
- `run-morning`: `outputs/morning_watchlist.csv`, `outputs/morning_playbook.md`
- `run-midday`: `outputs/midday_watchlist.csv`, `outputs/midday_playbook.md`
- `run-close`: `outputs/close_watchlist.csv`, `outputs/close_playbook.md`
- `run-day`: `outputs/morning_watchlist.csv`, `outputs/midday_watchlist.csv`, `outputs/daily_playbook.md`

6. Run tests:

```bash
pytest tests/ -v
```

Outputs are written to:
-  See outputs listed above based on command used

## 3-Option Trading System

The bot now provides **3 different trading approaches** in every session with an intelligent recommendation:

### 🔍 Option 1: Night Research - Catalyst-Driven Picks
- **Strategy**: Focus on stocks with strong news catalysts (SEC filings, earnings, press releases)
- **Best for**: Low volatility days when patience and catalyst-driven momentum are key
- **Filters**: Top 10 stocks with catalyst score ≥60

### 📊 Option 2: Relaxed Filters - More Opportunities
- **Strategy**: Lower thresholds to find more setups (gap≥1%, volume≥100k)
- **Best for**: Testing the pipeline or medium volatility days
- **Filters**: Relaxed from strict to capture more potential trades

### ✅ Option 3: Strict Filters - High Probability Setups
- **Strategy**: Conservative approach with strict filters (gap≥4%, volume≥500k)
- **Best for**: High volatility days, capital protection on slow days
- **Filters**: Only highest-quality setups pass

### Intelligent Recommendations

The bot analyzes market conditions each session and recommends the optimal option:

- **High Volatility** (avg gap ≥3%, 5+ gappers) → **Option 3 (Strict Filters)**
  - "High volatility with X strong gappers. Focus on premarket setups with quick entries."
  
- **Low Volatility** (avg gap <1.5%) → **Option 1 (Night Research)**
  - "Low volatility market. Focus on catalyst-driven picks. Wait for news-driven momentum."
  
- **Medium Volatility** → **Option 2 (Relaxed Filters)** or **Option 3**
  - Adapts based on available signals and market dynamics

**Example Output:**
```
MORNING PRE-MARKET (LOW volatility)
Market: Avg gap 0.61% | 0 gappers

✅ RECOMMENDED: NIGHT RESEARCH
   Low volatility market. Focus on catalyst-driven picks from night research.

Option 1 - Night Research: 3 catalyst picks
Option 2 - Relaxed Filters: 0 setups
Option 3 - Strict Filters:  0 setups
```

## Configuration

### Scanner Settings (`config/scanner.yaml`)

Pre-market filters:
- **Gap**: ≥4% (strong momentum signal)
- **Volume**: ≥500k premarket shares
- **Price**: $2-$30 range (excludes penny stocks and high-priced shares)
- **Dollar Volume**: ≥$20M (ensures liquidity)
- **Spread**: ≤0.35% (tight bid-ask for clean entries)

Midday filters (stricter):
- **Relative Volume**: ≥1.8x average
- **Dollar Volume**: ≥$35M
- **Spread**: ≤0.25%

### Risk Settings (`config/risk.yaml`)

- **Max Trades/Day**: 3
- **Daily Loss Lockout**: 1.5% of account
- **Consecutive Loss Limit**: 2 trades
- **Stop Loss**: Fixed 1% from entry
- **Risk Per Trade**: 0.5% of account

### Indicator Settings (`config/indicators.yaml`)

- **EMA Fast**: 9-period
- **EMA Slow**: 20-period
- **Volume Spike**: 1.8x for morning, 2.2x for midday
- **VWAP Hold**: 1 bar confirmation

## Usage

### Basic Commands

**Run with mock data (no API needed):**
```bash
python -m tradingbot.cli run-day
# Output: [MOCK DATA] Morning cards: 1 | Midday cards: 0
```

**Run with real Alpaca data:**
```bash
python -m tradingbot.cli --real-data run-day
# Output: [REAL DATA] Morning cards: X | Midday cards: Y
```

**Check schedule:**
```bash
python -m tradingbot.cli schedule
# Output: TZ=America/New_York | night=20:00 | premarket=08:45 | midday=11:00 | eod=15:50
```

### Understanding Results

**The 3-option system adapts to market conditions:**

Each daily run shows results from **all 3 options** side-by-side:
1. **Night Research**: Catalyst-driven picks (always shown, even on quiet days)
2. **Relaxed Filters**: More opportunities with lower thresholds (gap≥1%, vol≥100k)
3. **Strict Filters**: High-probability setups only (gap≥4%, vol≥500k)

The bot analyzes market volatility and **recommends which option to focus on** for that session.

**When you see 0 setups in Option 3 (Strict):**
This is **normal and expected** on low-volatility days. Your strict filters protect capital:
- Only stocks with ≥4% gaps qualify (not common on quiet days)
- Volume must exceed 500k premarket (eliminates low-liquidity names)
- Catalyst score must be ≥60 (requires recent meaningful news)

✅ **Zero strict-filter trades = Capital protection mode working correctly**

On these days, the bot will recommend **Option 1 (Night Research)** or **Option 2 (Relaxed Filters)** instead.

**Example output with qualified setups:**
```csv
symbol,side,score,entry_price,stop_price,tp1_price,tp2_price,invalidation_price
NVDA,long,84.5,892.50,883.57,901.43,910.36,888.20
```

## Using the 3-Option System

### Recommended Workflow

1. **Run the bot daily** (before market open or midday):
   ```bash
   python -m tradingbot.cli --real-data run-day
   ```

2. **Check the recommendation** in the terminal output:
   - HIGH volatility → Focus on **Option 3 (Strict Filters)**
   - LOW volatility → Focus on **Option 1 (Night Research)**
   - MEDIUM volatility → Check **Option 2 (Relaxed Filters)**

3. **Review the daily playbook** (`outputs/daily_playbook.md`):
   - All 3 options are shown with complete details
   - Recommended option is clearly marked with ✅
   - Each option explains the strategy and best use case

4. **Trade according to your risk tolerance**:
   - Conservative: Only trade **Option 3** setups
   - Moderate: Follow the **recommended option**
   - Aggressive: Review all options and choose based on your read

### Additional Tools

### Run Diagnostic

Check what data Alpaca is returning and why stocks are filtered out:

```bash
python diagnostic.py
```

Output shows:
- How many symbols passed catalyst scoring
- Real-time prices, gaps, and volume
- Which filters eliminated each stock

### Manually Adjust Filters (Advanced)

If you want to permanently change filter thresholds, edit `config/scanner.yaml`:
```yaml
scanner:
  min_gap_pct: 4.0  # Increase for stricter, decrease for more signals
  min_premarket_volume: 500000  # Higher = better liquidity
  min_score: 70  # Ranking threshold
```

**Note:** The 3-option system already provides relaxed (1%/100k) and strict (4%/500k) variants,
so manual adjustments are rarely needed.

Then run:
```bash
python -m tradingbot.cli --real-data run-day
```

### Best Times to Run

For optimal results with any option, run during:
- **Pre-market hours** (6:00-9:30 AM ET) when gappers are most active
- **Earnings season** (more catalyst-driven moves)
- **High volatility days** (market news, Fed announcements, major economic data)

The 3-option system will detect high volatility and automatically recommend Option 3 (Strict Filters).

### Expand Universe (Advanced)

Edit `src/tradingbot/data/alpaca_client.py` to add more symbols to scan:
```python
def get_tradable_universe(self) -> list[str]:
    return [
        # Add more high-volume stocks here
        "AAPL", "MSFT", "GOOGL", ...
    ]
```

Larger universe = higher chance of finding gappers daily.

## Project Status

✅ **Phase 1 Complete:**
- Config-driven scanner with gap, volume, spread filters
- 3-indicator confirmation (Volume + EMA + VWAP)
- Risk management (daily lockout, trade count, stop-loss)
- Mock data mode for development/testing
- Alert-only output (CSV + Markdown)

✅ **Phase 2 Complete:**
- Alpaca API integration (tested with real credentials)
- Multi-source news aggregation (SEC, earnings, press releases)
- Catalyst scoring with keyword boost and recency weighting
- Dual-mode operation (mock vs. real data)
- Virtual environment with all dependencies

✅ **Phase 3 Complete:**
- 3-option trading system with intelligent recommendations
- Market condition analyzer (detects volatility levels)
- Night research catalyst picks (Option 1)
- Relaxed filter scan (Option 2: gap≥1%, vol≥100k)
- Strict filter scan (Option 3: gap≥4%, vol≥500k)
- Automatic recommendation based on market conditions

📋 **Phase 4 Planned:**
- Detailed implementation plan created (see [PHASE4_IMPLEMENTATION_PLAN.md](PHASE4_IMPLEMENTATION_PLAN.md))
- CLI split into 5 commands (news, morning, midday, close, full-day)
- Real news integration: SEC EDGAR + RSS Feeds + Twitter/X
- Windows Task Scheduler automation (5 daily runs)
- Timeline: 12-17 hours over several days

✅ **Validated:**
- All 7 tests pass
- Real Alpaca connection confirmed (March 5, 2026)
- Data pipeline functional (gaps, volume, indicators computed correctly)
- Filters working as designed (strict by default)
- 3-option system tested with both mock and real data
- Recommendation engine correctly identifies volatility levels

## Next Steps

### 📋 Phase 4: Automation & Real News

#### ✅ Phase 4A: CLI Split (Complete)

The bot now supports 5 separate commands for scheduled execution:
- `run-news` - News research only (saves catalyst_scores.json)
- `run-morning` - Pre-market scan (8:45 AM)
- `run-midday` - Midday scan (12:00 PM)
- `run-close` - After-hours scan (3:50 PM)
- `run-day` - Legacy all-in-one command

Each command supports `--real-data` flag and generates independent output files.

#### ✅ Phase 4B-4E: Real News & Automation (Complete)

See **[PHASE4_IMPLEMENTATION_PLAN.md](PHASE4_IMPLEMENTATION_PLAN.md)** for the complete implementation roadmap.

**Goals:**
- ✅ SEC EDGAR API integration for real filings (8-K, 10-K, 10-Q)
- ✅ Multi-source RSS feeds (Yahoo Finance, MarketWatch, Benzinga)
- ✅ Social proxy fallback (Stocktwits + Reddit) - free alternative to paid X API
- ✅ Windows Task Scheduler automation with 4 daily scheduled runs
- ✅ File persistence and archiving system with timestamps
- ✅ Smart money tracking (13F filings, congressional trades)

**Schedule:**
```
12:00 AM → Night news research (SEC + RSS + social proxy)
8:45 AM  → Pre-market scan (gap analysis + volume)
12:00 PM → Midday scan
3:50 PM  → Close scan (late opportunities)
```

**Documentation:**
- [TASK_SCHEDULER.md](docs/TASK_SCHEDULER.md) - Automation setup and troubleshooting
- [FILE_PERSISTENCE_GUIDE.md](FILE_PERSISTENCE_GUIDE.md) - File management and archiving

**Status:** Phase 4 complete! Ready for Phase 5 (Cloud Deployment)

---

### Immediate Actions (Current Phase 3)
1. Run during pre-market hours (6-9:30 AM ET) for best results
2. Monitor `outputs/daily_playbook.md` for entry signals
3. Manually execute trades based on generated cards

### Future Phases
**Phase 5: Heroku Deployment**
- Cloud-based execution (no PC required)
- Environment variables for config
- Heroku Scheduler add-on

**Phase 6: Advanced Features**
- Email/Telegram real-time alerts
- Position tracking and P&L journaling
- Backtesting on historical data
- Semi-automated order placement

**⚠️ Never use for live trading without extensive paper trading validation first.**

## Troubleshooting

**"No setups in Option 3 (Strict Filters)":**
- **This is normal and expected** on low-volatility days
- ✅ Check the bot's recommendation - it will suggest Option 1 (Night Research) or Option 2 (Relaxed Filters) instead
- All 3 options are shown; focus on the recommended one
- Run during pre-market hours (6-9:30 AM ET) or earnings season for best Option 3 signals

**"No setups in ANY option":**
- Very rare, indicates extremely quiet market
- Run diagnostic: `python diagnostic.py`
- Check if universe has enough symbols (see "Expand Universe" section)
- Verify Alpaca data is being fetched correctly

**API errors:**
- Verify credentials in `config/broker.yaml`
- Ensure `paper: true` is set
- Check Alpaca account status at https://alpaca.markets

**Unicode errors in diagnostic:**
- Windows terminal issue with special characters
- Run: `python diagnostic.py 2>&1 | Select-Object -First 50`

## Next Steps

### ✅ Phase 4: Automation & Real News (Complete)

**Completed:**
- ✅ Split CLI into separate commands (run-news, run-morning, run-midday, run-close)
- ✅ Real news sources: SEC EDGAR + RSS Feeds + Social Proxy (Stocktwits/Reddit)
- ✅ Windows Task Scheduler automation (4 daily runs)
- ✅ File persistence and archiving with timestamps
- ✅ Smart money tracking integration
- Prepare for future Heroku deployment

**Automated Schedule (Windows Task Scheduler):**
- 12:00 AM → News research (SEC + RSS + social signals)
- 8:45 AM  → Pre-market scan
- 12:00 PM → Midday scan
- 3:50 PM  → Close scan

See **[TASK_SCHEDULER.md](docs/TASK_SCHEDULER.md)** for automation setup.

### Phase 5: Cloud Deployment (Next)

**Goals:**
- Deploy to Heroku or cloud platform
- Scheduled job execution (no local PC required)
- Environment-based configuration

---

## Security

**Never commit credentials:**
- `config/broker.yaml` is in `.gitignore`
- Use `config/broker.example.yaml` as template
- Keep API keys secure and rotate periodically

## Documentation

- [README.md](README.md) - This file (overview + usage)
- [PHASE2_GUIDE.md](PHASE2_GUIDE.md) - Detailed Alpaca integration guide
- [PHASE4_IMPLEMENTATION_PLAN.md](PHASE4_IMPLEMENTATION_PLAN.md) - Phase 4 roadmap (automation + real news)
- [TASK_SCHEDULER.md](docs/TASK_SCHEDULER.md) - Windows Task Scheduler automation guide
- [CLOUD_DEPLOYMENT.md](docs/CLOUD_DEPLOYMENT.md) - Phase 5 cloud deployment runbook
- [FILE_PERSISTENCE_GUIDE.md](FILE_PERSISTENCE_GUIDE.md) - File management and archiving system
- [PRE_PUSH_CHECKLIST.md](PRE_PUSH_CHECKLIST.md) - Pre-commit verification checklist
- [config/broker.example.yaml](config/broker.example.yaml) - API credential template

## Notes

- Virtual environment configured at `venv/` with all dependencies
- `diagnostic.py` available for debugging Alpaca data flow
- Mock mode works without any API credentials
- Real data mode tested and validated with Alpaca paper trading account
