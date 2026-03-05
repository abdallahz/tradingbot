# Phase 4 Implementation Plan

**Goal**: Add scheduled job execution, real news sources, and production deployment preparation

**Status**: Planning Complete ✅  
**Start Date**: March 5, 2026  
**Approach**: Careful, step-by-step implementation with testing at each phase

---

## Overview

### Schedule
```
8:00 PM   → Night News Research (SEC, RSS, Twitter)
8:00 AM   → Early Morning News Update (catch overnight news)
8:45 AM   → Pre-Market Scan
12:00 PM  → Midday Market Scan
3:50 PM   → After-Hours Close Scan
```

### News Sources (All Free)
1. **SEC EDGAR API** - Official filings, no auth needed
2. **RSS Feeds** - Yahoo Finance, SeekingAlpha, financial news
3. **Twitter/X API** - Real-time stock mentions and sentiment

### Deployment
- **Phase 4**: Windows Task Scheduler
- **Phase 5**: Migrate to Heroku

---

## Phase 4A: Split CLI Commands (PUSH TO GITHUB AFTER THIS)

### Goal
Split `run-day` into separate commands for different times of day.

### New CLI Commands
```bash
# Night research only (8 PM & 8 AM)
python -m tradingbot.cli --real-data run-news

# Pre-market scan (8:45 AM)
python -m tradingbot.cli --real-data run-morning

# Midday scan (12:00 PM)
python -m tradingbot.cli --real-data run-midday

# After-hours scan (3:50 PM)
python -m tradingbot.cli --real-data run-close

# Full day (all of the above) - KEEP FOR BACKWARD COMPATIBILITY
python -m tradingbot.cli --real-data run-day
```

### Files to Modify

#### 1. `src/tradingbot/cli.py`
**Changes:**
- Add 4 new subcommands: `run-news`, `run-morning`, `run-midday`, `run-close`
- Keep `run-day` for backward compatibility
- Update help text

**New structure:**
```python
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TradingBot alert-only scanner")
    parser.add_argument("--real-data", action="store_true", help="Use real Alpaca API")
    sub = parser.add_subparsers(dest="command", required=True)
    
    sub.add_parser("schedule", help="Show configured schedule")
    sub.add_parser("run-news", help="Run night/morning news research only")
    sub.add_parser("run-morning", help="Run pre-market scan (8:45 AM)")
    sub.add_parser("run-midday", help="Run midday scan (12:00 PM)")
    sub.add_parser("run-close", help="Run after-hours scan (3:50 PM)")
    sub.add_parser("run-day", help="Run all: news + morning + midday + close")
    
    return parser
```

#### 2. `src/tradingbot/app/scheduler.py`
**Add new methods:**
```python
def run_news_only(self) -> dict[str, float]:
    """Run night research, return catalyst scores."""
    
def run_morning_only(self) -> ThreeOptionWatchlist:
    """Run pre-market scan only."""
    
def run_midday_only(self) -> ThreeOptionWatchlist:
    """Run midday scan only."""
    
def run_close_only(self) -> ThreeOptionWatchlist:
    """Run after-hours scan only."""
```

#### 3. `src/tradingbot/app/session_runner.py`
**Add method:**
```python
def run_single_session(
    self, 
    session_type: Literal["morning", "midday", "close"],
    catalyst_scores: dict[str, float]
) -> ThreeOptionWatchlist:
    """Run a single scan session with pre-computed catalyst scores."""
```

#### 4. `config/schedule.yaml`
**Update with all 5 job times:**
```yaml
schedule:
  timezone: "America/New_York"
  night_research: "20:00"       # 8 PM - News scan
  morning_news: "08:00"         # 8 AM - Early news update
  premarket_scan: "08:45"       # 8:45 AM - Pre-market
  midday_scan: "12:00"          # 12 PM - Midday
  close_scan: "15:50"           # 3:50 PM - After-hours
```

#### 5. `outputs/` Directory Structure
**New output files:**
```
outputs/
  catalyst_scores.json         # From run-news (8 PM, 8 AM)
  morning_watchlist.csv        # From run-morning (8:45 AM)
  morning_playbook.md
  midday_watchlist.csv         # From run-midday (12 PM)
  midday_playbook.md
  close_watchlist.csv          # From run-close (3:50 PM)
  close_playbook.md
  daily_summary.md             # Combined summary
```

### Testing Checklist

- [ ] Run `python -m tradingbot.cli run-news` (mock data)
- [ ] Verify `catalyst_scores.json` is created
- [ ] Run `python -m tradingbot.cli run-morning` (mock data)
- [ ] Verify morning outputs created
- [ ] Run `python -m tradingbot.cli run-midday` (mock data)
- [ ] Verify midday outputs created
- [ ] Run `python -m tradingbot.cli run-close` (mock data)
- [ ] Verify close outputs created
- [ ] Run `python -m tradingbot.cli run-day` (mock data)
- [ ] Verify all outputs created
- [ ] Repeat all above with `--real-data` flag
- [ ] Run pytest suite (ensure no regressions)
- [ ] Check README is updated with new commands

### Expected Duration
**2-3 hours** (careful implementation + testing)

---

## Phase 4B: SEC EDGAR Integration

### Goal
Replace mocked SEC filings with real data from SEC EDGAR API.

### New Files to Create

#### 1. `src/tradingbot/research/sec_filings.py`
**Purpose:** Fetch real SEC filings from EDGAR API

**Key functions:**
```python
class SECFilingsFetcher:
    """Fetch real SEC filings from EDGAR API (no auth needed)."""
    
    def __init__(self, company_ticker_map: dict[str, str]):
        """Map stock symbols to CIK codes."""
        
    def fetch_recent_filings(
        self, 
        symbols: list[str], 
        hours_lookback: int = 24
    ) -> list[dict]:
        """
        Fetch recent SEC filings for given symbols.
        
        Returns:
        [
            {
                "symbol": "AAPL",
                "form_type": "8-K",
                "filed_date": "2026-03-05T10:30:00",
                "description": "Current report",
                "url": "https://..."
            },
            ...
        ]
        """
```

**API Endpoint:**
```
https://www.sec.gov/cgi-bin/browse-edgar
Parameters:
  - action=getcompany
  - CIK={company_cik}
  - type=&dateb=&owner=exclude&count=10
```

**Important Notes:**
- SEC requires User-Agent header with contact email
- Rate limit: ~10 requests/second
- No API key needed
- Free forever

#### 2. `src/tradingbot/research/cik_mapping.py`
**Purpose:** Map stock symbols to SEC CIK codes

```python
# Static mapping (expand as needed)
SYMBOL_TO_CIK = {
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "GOOGL": "0001652044",
    "NVDA": "0001045810",
    "TSLA": "0001318605",
    # ... add more
}

def get_cik(symbol: str) -> str | None:
    """Get CIK code for a stock symbol."""
```

### Files to Modify

#### 1. `src/tradingbot/research/news_aggregator.py`
**Changes:**
- Add `sec_fetcher: SECFilingsFetcher` attribute
- Update `fetch_news()` to use real SEC data when available
- Keep mocked data as fallback

```python
def fetch_news(self, symbols: list[str]) -> list[NewsItem]:
    news_items = []
    
    # Real SEC filings (if enabled)
    if self.sec_enabled:
        try:
            sec_filings = self.sec_fetcher.fetch_recent_filings(symbols)
            news_items.extend(self._convert_sec_to_news_items(sec_filings))
        except Exception as e:
            logger.warning(f"SEC fetch failed: {e}, using fallback")
            news_items.extend(self._mock_sec_filings(symbols))
    
    # ... rest of sources
```

#### 2. `config/broker.yaml`
**Add SEC configuration:**
```yaml
news:
  # SEC EDGAR
  sec_filings: true
  sec_user_agent: "TradingBot/1.0 (your-email@example.com)"  # REQUIRED by SEC
  
  # Other sources
  earnings_calendar: true
  press_releases: true
  twitter_enabled: false
  
  max_age_hours: 24
```

### Testing Checklist

- [ ] Test `SECFilingsFetcher` with known symbols (AAPL, MSFT)
- [ ] Verify User-Agent header is set
- [ ] Check rate limiting works (don't hammer SEC)
- [ ] Test with symbols not in CIK map (fallback)
- [ ] Run `python -m tradingbot.cli run-news --real-data`
- [ ] Verify real SEC filings appear in catalyst scores
- [ ] Check fallback to mocked data if SEC fails
- [ ] Run pytest suite

### Expected Duration
**2-3 hours** (API integration + error handling + testing)

---

## Phase 4C: RSS Feeds Integration

### Goal
Add free RSS news feeds for market news.

### New Files to Create

#### 1. `src/tradingbot/research/rss_feeds.py`
**Purpose:** Fetch and parse RSS feeds from free sources

```python
class RSSFeedFetcher:
    """Fetch news from free RSS feeds."""
    
    FEEDS = {
        "yahoo_finance": "https://finance.yahoo.com/news/rssindex",
        "seeking_alpha": "https://seekingalpha.com/api/sa/combined/{symbol}.xml",
        "marketwatch": "https://www.marketwatch.com/rss/realtimeheadlines",
        "benzinga": "https://www.benzinga.com/feed",
    }
    
    def fetch_feeds(
        self, 
        symbols: list[str], 
        hours_lookback: int = 24
    ) -> list[dict]:
        """
        Fetch RSS feeds and filter for relevant stock news.
        
        Returns:
        [
            {
                "symbol": "AAPL",
                "title": "Apple announces new product",
                "published": "2026-03-05T09:30:00",
                "source": "yahoo_finance",
                "url": "https://..."
            },
            ...
        ]
        """
```

**Key Features:**
- Parse XML/RSS with feedparser library
- Filter articles by stock symbol mentions
- Extract publish date, title, summary
- Detect sentiment keywords (bullish, bearish, etc.)

#### 2. Update `requirements.txt`
```txt
feedparser>=6.0.10
```

### Files to Modify

#### 1. `src/tradingbot/research/news_aggregator.py`
**Add RSS integration:**
```python
def fetch_news(self, symbols: list[str]) -> list[NewsItem]:
    news_items = []
    
    # SEC filings
    if self.sec_enabled:
        # ... existing SEC code
    
    # RSS feeds (NEW)
    if self.rss_enabled:
        try:
            rss_articles = self.rss_fetcher.fetch_feeds(symbols)
            news_items.extend(self._convert_rss_to_news_items(rss_articles))
        except Exception as e:
            logger.warning(f"RSS fetch failed: {e}")
    
    # ... rest
```

#### 2. `config/broker.yaml`
```yaml
news:
  sec_filings: true
  sec_user_agent: "TradingBot/1.0 (your-email@example.com)"
  
  # RSS Feeds (NEW)
  rss_feeds: true
  rss_sources:
    - yahoo_finance
    - seeking_alpha
    - marketwatch
  
  earnings_calendar: true
  press_releases: true
  twitter_enabled: false
  max_age_hours: 24
```

### Testing Checklist

- [ ] Install feedparser: `pip install feedparser`
- [ ] Test `RSSFeedFetcher` with each source
- [ ] Verify symbol filtering works
- [ ] Check date parsing is correct
- [ ] Test with network errors (fallback)
- [ ] Run `python -m tradingbot.cli run-news --real-data`
- [ ] Verify RSS articles appear in catalyst scores
- [ ] Combine SEC + RSS scores
- [ ] Run pytest suite

### Expected Duration
**2-3 hours** (RSS parsing + multi-source handling + testing)

---

## Phase 4D: Twitter/X Integration

### Goal
Add Twitter/X API for real-time stock mentions and sentiment.

### Prerequisites
**User provides:**
- Twitter Developer account (apply at https://developer.twitter.com/)
- Bearer Token (from Twitter Developer Portal)
- Add to `config/broker.yaml`

### New Files to Create

#### 1. `src/tradingbot/research/twitter_scanner.py`
**Purpose:** Search Twitter for stock mentions and sentiment

```python
class TwitterScanner:
    """Search Twitter/X for stock mentions using API v2."""
    
    def __init__(self, bearer_token: str):
        """Initialize with Twitter API bearer token."""
        self.bearer_token = bearer_token
        self.base_url = "https://api.twitter.com/2/tweets/search/recent"
    
    def search_stock_mentions(
        self, 
        symbols: list[str], 
        hours_lookback: int = 24
    ) -> list[dict]:
        """
        Search Twitter for stock symbol mentions.
        
        Query examples:
        - $AAPL OR #AAPL (cashtag and hashtag)
        - "Apple stock" OR "AAPL earnings"
        
        Returns:
        [
            {
                "symbol": "AAPL",
                "tweet_count": 1523,
                "positive_count": 890,
                "negative_count": 234,
                "neutral_count": 399,
                "top_keywords": ["earnings", "beat", "revenue"],
                "sentiment_score": 0.65,  # -1 to 1
            },
            ...
        ]
        """
    
    def _calculate_sentiment(self, tweets: list[dict]) -> float:
        """
        Calculate sentiment from tweet text.
        
        Positive keywords: bull, bullish, moon, rocket, strong buy, breakout
        Negative keywords: bear, bearish, sell, crash, dump, weak
        """
```

**API Details:**
- Endpoint: `GET /2/tweets/search/recent`
- Free tier: 500,000 tweets/month (plenty for 2 runs/day)
- Query format: `($SYMBOL OR #SYMBOL) -is:retweet lang:en`
- Returns: tweet text, created_at, public_metrics

#### 2. Update `requirements.txt`
```txt
tweepy>=4.14.0  # Official Twitter API library
```

### Files to Modify

#### 1. `src/tradingbot/research/news_aggregator.py`
**Add Twitter integration:**
```python
def fetch_news(self, symbols: list[str]) -> list[NewsItem]:
    news_items = []
    
    # SEC filings
    if self.sec_enabled:
        # ... existing
    
    # RSS feeds
    if self.rss_enabled:
        # ... existing
    
    # Twitter/X mentions (NEW)
    if self.twitter_enabled:
        try:
            twitter_data = self.twitter_scanner.search_stock_mentions(symbols)
            news_items.extend(self._convert_twitter_to_news_items(twitter_data))
        except Exception as e:
            logger.warning(f"Twitter fetch failed: {e}")
    
    return news_items
```

#### 2. `src/tradingbot/research/catalyst_scorer.py`
**Update scoring to include Twitter sentiment:**
```python
def score_symbols(self, symbols: list[str]) -> dict[str, float]:
    """
    Composite score:
    - SEC filings: 30%
    - RSS news: 30%
    - Twitter sentiment: 40%
    """
```

#### 3. `config/broker.yaml`
```yaml
news:
  sec_filings: true
  sec_user_agent: "TradingBot/1.0 (your-email@example.com)"
  
  rss_feeds: true
  rss_sources:
    - yahoo_finance
    - seeking_alpha
  
  # Twitter/X (NEW)
  twitter_enabled: false  # Set to true when you have API key
  twitter_bearer_token: ""  # Add your token here
  twitter_search_lookback_hours: 24
  
  earnings_calendar: true
  press_releases: true
  max_age_hours: 24
```

#### 4. `config/broker.example.yaml`
**Add Twitter example:**
```yaml
news:
  # ... existing
  
  # Twitter/X API (optional, apply at https://developer.twitter.com/)
  twitter_enabled: false
  twitter_bearer_token: "YOUR_TWITTER_BEARER_TOKEN"
  twitter_search_lookback_hours: 24
```

### Testing Checklist

**Without API Key (Mocked):**
- [ ] Install tweepy: `pip install tweepy`
- [ ] Test `TwitterScanner` with mocked responses
- [ ] Verify sentiment calculation logic
- [ ] Test graceful failure when `twitter_enabled: false`
- [ ] Run `python -m tradingbot.cli run-news` (no errors)

**With API Key (Real - when user provides):**
- [ ] Add bearer token to `config/broker.yaml`
- [ ] Set `twitter_enabled: true`
- [ ] Test search for known trending stocks
- [ ] Verify rate limiting is respected
- [ ] Check sentiment scores are reasonable
- [ ] Run full news cycle: `python -m tradingbot.cli run-news --real-data`
- [ ] Verify all 3 sources (SEC + RSS + Twitter) combine correctly
- [ ] Run pytest suite

### Expected Duration
**3-4 hours** (API integration + sentiment analysis + testing)

---

## Phase 4E: Windows Task Scheduler Setup

### Goal
Create batch files and instructions for automated execution.

### Files to Create

#### 1. `scripts/run_news.bat`
```bat
@echo off
REM Run night news research
cd /d C:\tradingbot
.\venv\Scripts\python.exe -m tradingbot.cli --real-data run-news
echo News research complete at %date% %time% >> logs\scheduler.log
```

#### 2. `scripts/run_morning.bat`
```bat
@echo off
REM Run pre-market scan
cd /d C:\tradingbot
.\venv\Scripts\python.exe -m tradingbot.cli --real-data run-morning
echo Morning scan complete at %date% %time% >> logs\scheduler.log
```

#### 3. `scripts/run_midday.bat`
```bat
@echo off
REM Run midday scan
cd /d C:\tradingbot
.\venv\Scripts\python.exe -m tradingbot.cli --real-data run-midday
echo Midday scan complete at %date% %time% >> logs\scheduler.log
```

#### 4. `scripts/run_close.bat`
```bat
@echo off
REM Run after-hours scan
cd /d C:\tradingbot
.\venv\Scripts\python.exe -m tradingbot.cli --real-data run-close
echo Close scan complete at %date% %time% >> logs\scheduler.log
```

#### 5. `WINDOWS_TASK_SCHEDULER_SETUP.md`
**Complete guide with:**
- Screenshots of Task Scheduler wizard
- Step-by-step instructions
- Trigger configuration (daily at specific times)
- Action configuration (run batch file)
- Error handling and logging
- How to check if jobs ran successfully

**Key sections:**
```markdown
### Step 1: Open Task Scheduler
Start → type "Task Scheduler" → Open

### Step 2: Create Basic Task
1. Click "Create Basic Task"
2. Name: "TradingBot - Night News"
3. Description: "Run news research at 8 PM"

### Step 3: Set Trigger
1. Daily
2. Start: Today at 8:00 PM
3. Recur every: 1 days

### Step 4: Set Action
1. Start a program
2. Program: C:\tradingbot\scripts\run_news.bat
3. Start in: C:\tradingbot

### Step 5: Repeat for Each Job
- Night News: 8:00 PM (run_news.bat)
- Morning News: 8:00 AM (run_news.bat)
- Pre-Market: 8:45 AM (run_morning.bat)
- Midday: 12:00 PM (run_midday.bat)
- Close: 3:50 PM (run_close.bat)
```

#### 6. `logs/` Directory
Create for storing execution logs:
```
logs/
  scheduler.log      # All job executions
  errors.log         # Errors only
```

### Testing Checklist

- [ ] Create `scripts/` directory
- [ ] Create all 4 batch files
- [ ] Test each batch file manually (double-click)
- [ ] Verify outputs are created
- [ ] Create Task Scheduler tasks (all 5)
- [ ] Set to run once in next 5 minutes (test)
- [ ] Verify tasks execute successfully
- [ ] Check `logs/scheduler.log` for entries
- [ ] Review WINDOWS_TASK_SCHEDULER_SETUP.md for clarity
- [ ] Test wake-from-sleep behavior (if applicable)

### Expected Duration
**1-2 hours** (batch files + Task Scheduler setup + documentation)

---

## Phase 4F: Documentation & README Update

### Goal
Document all Phase 4 changes for GitHub.

### Files to Update

#### 1. `README.md`
**Add sections:**

```markdown
## 🕐 Automated Scheduling

### Daily Job Schedule
- **8:00 PM** - Night news research (SEC + RSS + Twitter)
- **8:00 AM** - Morning news update
- **8:45 AM** - Pre-market scan
- **12:00 PM** - Midday market scan
- **3:50 PM** - After-hours close scan

### Run Manually
```bash
# News research only
python -m tradingbot.cli --real-data run-news

# Individual scans
python -m tradingbot.cli --real-data run-morning
python -m tradingbot.cli --real-data run-midday
python -m tradingbot.cli --real-data run-close

# Full day (all 5 jobs)
python -m tradingbot.cli --real-data run-day
```

### Set Up Windows Task Scheduler
See [WINDOWS_TASK_SCHEDULER_SETUP.md](WINDOWS_TASK_SCHEDULER_SETUP.md) for complete instructions.

## 📰 News Sources

### Real-Time Catalyst Detection
1. **SEC EDGAR** - Official filings (8-K, 10-K, earnings)
2. **RSS Feeds** - Yahoo Finance, SeekingAlpha, MarketWatch
3. **Twitter/X** - Real-time stock mentions and sentiment

### Twitter API Setup
1. Apply at https://developer.twitter.com/ (free tier)
2. Get Bearer Token from Developer Portal
3. Add to `config/broker.yaml`:
   ```yaml
   news:
     twitter_enabled: true
     twitter_bearer_token: "YOUR_TOKEN_HERE"
   ```

## 📊 Output Files

```
outputs/
  catalyst_scores.json         # News research results
  morning_watchlist.csv        # Pre-market setups
  morning_playbook.md
  midday_watchlist.csv         # Midday opportunities
  midday_playbook.md
  close_watchlist.csv          # After-hours setups
  close_playbook.md
  daily_summary.md             # Combined day summary
```

## 🚀 Deployment

### Current: Windows Task Scheduler
Runs locally on your PC. See setup guide above.

### Future: Heroku (Phase 5)
- Cloud-based execution
- No PC required
- 24/7 reliability
- Heroku Scheduler add-on
```

#### 2. `PRE_PUSH_CHECKLIST.md`
**Update with Phase 4 items:**
```markdown
## ✅ Phase 4 Complete

- [x] CLI split into 5 commands (news, morning, midday, close, day)
- [x] SEC EDGAR integration (real filings)
- [x] RSS feeds integration (Yahoo, SeekingAlpha)
- [x] Twitter/X framework (ready for API key)
- [x] Windows Task Scheduler batch files
- [x] Complete setup documentation
- [x] All tests passing
- [x] README updated
```

#### 3. `.gitignore`
**Ensure logs are excluded:**
```
# Logs
logs/*.log

# Outputs
outputs/*.csv
outputs/*.md
outputs/*.json
```

### Testing Checklist

- [ ] Review all README changes for accuracy
- [ ] Test all example commands in README
- [ ] Verify links work (internal docs)
- [ ] Check markdown formatting
- [ ] Spellcheck all documentation
- [ ] Run complete workflow end-to-end
- [ ] Take screenshots for Task Scheduler guide
- [ ] Review PRE_PUSH_CHECKLIST.md

### Expected Duration
**1-2 hours** (documentation + final review)

---

## Summary Timeline

### Phase 4A: CLI Split
**Time**: 2-3 hours  
**Push to GitHub**: ✅ YES (after this phase)

### Phase 4B: SEC EDGAR
**Time**: 2-3 hours  
**Push to GitHub**: After completion

### Phase 4C: RSS Feeds
**Time**: 2-3 hours  
**Push to GitHub**: After completion

### Phase 4D: Twitter/X
**Time**: 3-4 hours  
**Push to GitHub**: After completion

### Phase 4E: Task Scheduler
**Time**: 1-2 hours  
**Push to GitHub**: After completion

### Phase 4F: Documentation
**Time**: 1-2 hours  
**Final Push**: ✅ Complete Phase 4

---

## Total Estimated Time
**12-17 hours** over several days (taking it slow and careful)

---

## Risk Mitigation

### API Rate Limits
- SEC: Max 10 req/sec (plenty for our use)
- Twitter: 500k tweets/month (2 runs/day = ~100/day)
- RSS: No limits (public feeds)

### Error Handling
- Each news source has fallback to mocked data
- Network errors logged but don't stop execution
- Missing API keys = graceful degradation

### Testing Strategy
- Test each phase independently
- Run full integration tests before pushing
- Keep pytest suite at 100% passing
- Manual testing of batch files

### Backup Plan
If any news source fails:
- Fallback to mocked data (existing behavior)
- Continue with other sources
- Log warnings for debugging

---

## Post-Phase 4 (Future)

### Phase 5: Heroku Deployment
- Environment variables for config
- Heroku Scheduler add-on
- Cloud-based execution
- No local PC needed

### Phase 6: Enhanced Features
- Email/Telegram alerts
- Position tracking
- P&L journaling
- Backtesting engine

---

**Ready to start Phase 4A when you give the go-ahead!** 🚀
