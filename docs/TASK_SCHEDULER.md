# TradingBot - Windows Task Scheduler Automation

## Overview

Automated daily execution of the TradingBot using Windows Task Scheduler. All four daily scans run automatically at their scheduled times with full logging.

## Scheduled Tasks

| Task | Time (ET) | Purpose | Output Files |
|------|-----------|---------|--------------|
| **News Research** | 12:00 AM | Overnight news aggregation and catalyst scoring | `catalyst_scores.json`, `social_proxy_signals_news.json` |
| **Morning Scan** | 8:45 AM | Pre-market gap analysis and opportunity identification | `morning_watchlist.csv`, `morning_playbook.md` |
| **Midday Scan** | 12:00 PM | Mid-day re-scan for new setups | `midday_watchlist.csv`, `midday_playbook.md` |
| **Close Scan** | 3:50 PM | End-of-day opportunity scan | `close_watchlist.csv`, `close_playbook.md` |

## Quick Reference

### View All Tasks
```powershell
schtasks /Query /FO TABLE | Select-String "TradingBot"
```

### Run a Task Manually
```powershell
schtasks /Run /TN "\TradingBot\TradingBot_News"
schtasks /Run /TN "\TradingBot\TradingBot_Morning"
schtasks /Run /TN "\TradingBot\TradingBot_Midday"
schtasks /Run /TN "\TradingBot\TradingBot_Close"
```

### Check Task Status
```powershell
schtasks /Query /TN "\TradingBot\TradingBot_News" /V /FO LIST
```

### View Recent Logs
```powershell
Get-ChildItem C:\tradingbot\logs\ | Sort-Object LastWriteTime -Descending | Select-Object -First 10
```

### View Latest Log
```powershell
Get-Content (Get-ChildItem C:\tradingbot\logs\ | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
```

### Open Task Scheduler GUI
```powershell
taskschd.msc
```

## File Structure

```
C:\tradingbot\
├── scripts/
│   ├── run_news.ps1          # News research automation
│   ├── run_morning.ps1       # Morning scan automation
│   ├── run_midday.ps1        # Midday scan automation
│   ├── run_close.ps1         # Close scan automation
│   └── setup_scheduler.ps1   # One-time setup script
├── logs/
│   ├── news_YYYYMMDD_HHMMSS.log
│   ├── morning_YYYYMMDD_HHMMSS.log
│   ├── midday_YYYYMMDD_HHMMSS.log
│   └── close_YYYYMMDD_HHMMSS.log
├── outputs/
│   ├── catalyst_scores.json
│   ├── social_proxy_signals_news.json
│   ├── *_watchlist.csv
│   ├── *_playbook.md
│   └── archive/
│       └── YYYY-MM-DD/
│           ├── INDEX.md
│           ├── catalyst_scores_HHMMSS.json
│           ├── social_proxy_signals_news_HHMMSS.json
│           ├── *_watchlist_HHMMSS.csv
│           └── *_playbook_HHMMSS.md
└── config/
    └── broker.yaml           # Configuration with API keys
```

## Logging

Each task creates a timestamped log file in `C:\tradingbot\logs\`:

- **Success logs**: Show execution summary, scores, and archive location
- **Error logs**: Capture exceptions and failure details
- **Retention**: Logs are automatically kept; clean up manually as needed

Example log filename: `news_20260306_112557.log`

## Task Management

### Disable a Task
```powershell
schtasks /Change /TN "\TradingBot\TradingBot_News" /DISABLE
```

### Enable a Task
```powershell
schtasks /Change /TN "\TradingBot\TradingBot_News" /ENABLE
```

### Delete a Task
```powershell
schtasks /Delete /TN "\TradingBot\TradingBot_News" /F
```

### Change Schedule Time
```powershell
# Delete and recreate with new time
schtasks /Delete /TN "\TradingBot\TradingBot_News" /F
schtasks /Create /TN "\TradingBot\TradingBot_News" /TR "powershell.exe -ExecutionPolicy Bypass -File 'C:\tradingbot\scripts\run_news.ps1'" /SC DAILY /ST 01:00 /RU %USERNAME%
```

## Troubleshooting

### Task Doesn't Run

1. **Check task status:**
   ```powershell
   schtasks /Query /TN "\TradingBot\TradingBot_News" /V /FO LIST
   ```

2. **Verify task is enabled:**
   - Open Task Scheduler GUI: `taskschd.msc`
   - Navigate to `\TradingBot\` folder
   - Right-click task → Properties → Ensure "Enabled" is checked

3. **Check last run result:**
   - In Task Scheduler, view "Last Run Result" column
   - `0x0` = Success
   - Other codes indicate errors

### Script Fails

1. **Check the log file:**
   ```powershell
   Get-Content C:\tradingbot\logs\[most_recent_log].log
   ```

2. **Test script manually:**
   ```powershell
   cd C:\tradingbot
   powershell.exe -ExecutionPolicy Bypass -File "C:\tradingbot\scripts\run_news.ps1"
   ```

3. **Verify Python environment:**
   ```powershell
   C:\Python310\python.exe --version
   C:\Python310\python.exe -c "import tradingbot"
   ```

### Missing Dependencies

If you see `ModuleNotFoundError`:
```powershell
cd C:\tradingbot
C:\Python310\python.exe -m pip install -r requirements.txt
```

### API Errors

- **SEC 503 errors**: Normal during high traffic; bot continues with other sources
- **Benzinga SSL errors**: Network issue; bot continues with other RSS feeds
- **Alpaca errors**: Check API keys in `config/broker.yaml`

## Setup on New Machine

1. **Clone repository:**
   ```powershell
   git clone https://github.com/abdallahz/tradingbot.git C:\tradingbot
   cd C:\tradingbot
   ```

2. **Install Python 3.10+:**
   - Download from python.org
   - Install to `C:\Python310\`

3. **Install dependencies:**
   ```powershell
   C:\Python310\python.exe -m pip install -r requirements.txt
   ```

4. **Configure API keys:**
   - Edit `config/broker.yaml`
   - Add Alpaca API credentials

5. **Create scheduled tasks:**
   ```powershell
   # Run each command individually
   $user = $env:USERNAME
   schtasks /Create /TN "\TradingBot\TradingBot_News" /TR "powershell.exe -ExecutionPolicy Bypass -File 'C:\tradingbot\scripts\run_news.ps1'" /SC DAILY /ST 00:00 /RU $user /F
   schtasks /Create /TN "\TradingBot\TradingBot_Morning" /TR "powershell.exe -ExecutionPolicy Bypass -File 'C:\tradingbot\scripts\run_morning.ps1'" /SC DAILY /ST 08:45 /RU $user /F
   schtasks /Create /TN "\TradingBot\TradingBot_Midday" /TR "powershell.exe -ExecutionPolicy Bypass -File 'C:\tradingbot\scripts\run_midday.ps1'" /SC DAILY /ST 12:00 /RU $user /F
   schtasks /Create /TN "\TradingBot\TradingBot_Close" /TR "powershell.exe -ExecutionPolicy Bypass -File 'C:\tradingbot\scripts\run_close.ps1'" /SC DAILY /ST 15:50 /RU $user /F
   ```

6. **Test manually:**
   ```powershell
   schtasks /Run /TN "\TradingBot\TradingBot_News"
   ```

## Monitoring

### Daily Checks

1. **View today's archive:**
   ```powershell
   $today = Get-Date -Format "yyyy-MM-dd"
   Get-Content "C:\tradingbot\outputs\archive\$today\INDEX.md"
   ```

2. **Check latest playbook:**
   ```powershell
   Get-Content C:\tradingbot\outputs\morning_playbook.md
   ```

3. **Verify all tasks ran:**
   ```powershell
   schtasks /Query /FO TABLE | Select-String "TradingBot"
   ```

### Weekly Maintenance

1. **Review logs for errors:**
   ```powershell
   Get-ChildItem C:\tradingbot\logs\ -Filter "*.log" | 
       ForEach-Object { 
           $content = Get-Content $_.FullName -Raw
           if ($content -match "Error|Exception|Failed") {
               Write-Host $_.Name -ForegroundColor Red
           }
       }
   ```

2. **Clean old logs (optional):**
   ```powershell
   # Delete logs older than 30 days
   Get-ChildItem C:\tradingbot\logs\ -Filter "*.log" | 
       Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } | 
       Remove-Item
   ```

3. **Archive cleanup (optional):**
   ```powershell
   # Delete archives older than 90 days
   Get-ChildItem C:\tradingbot\outputs\archive\ | 
       Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-90) } | 
       Remove-Item -Recurse
   ```

## Configuration

Edit `config/broker.yaml` to customize:

```yaml
news:
  sec_filings: true           # Enable SEC EDGAR filings
  rss_feeds: true             # Enable RSS news feeds
  social_proxy_enabled: true  # Enable Stocktwits + Reddit social signals
  max_age_hours: 24           # News recency threshold
```

## Execution Flow

```
12:00 AM → News Research
           └─ Fetches: SEC filings, RSS feeds, social signals
           └─ Generates: catalyst_scores.json (28 symbols)
           └─ Archives: All outputs with timestamp

8:45 AM  → Morning Scan
           └─ Loads: catalyst_scores.json
           └─ Filters: Symbols with score >= 60
           └─ Analyzes: Pre-market gaps and volume
           └─ Generates: morning_watchlist.csv, morning_playbook.md
           └─ Archives: All outputs with timestamp

12:00 PM → Midday Scan
           └─ Loads: catalyst_scores.json
           └─ Re-scans: Market for new setups
           └─ Generates: midday_watchlist.csv, midday_playbook.md
           └─ Archives: All outputs with timestamp

3:50 PM  → Close Scan
           └─ Loads: catalyst_scores.json
           └─ Scans: End-of-day opportunities
           └─ Generates: close_watchlist.csv, close_playbook.md
           └─ Archives: All outputs with timestamp
```

## Support

For issues or questions:
- Check logs: `C:\tradingbot\logs\`
- View errors: Run scripts manually to see detailed output
- Test commands: Verify Python and dependencies are installed correctly
