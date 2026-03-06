# File Persistence & Archiving Guide

**Last Updated:** March 6, 2026

This guide explains what files are saved, where they're stored, and how archiving works.

---

## 📁 File Structure

```
tradingbot/
├── outputs/                                    # Current run outputs (overwritten)
│   ├── catalyst_scores.json                    # ✅ Saved, ✅ Archived, ✅ Git tracked
│   ├── smart_money_signals_morning.json        # ✅ Saved, ✅ Archived, ✅ Git tracked
│   ├── smart_money_signals_midday.json         # ✅ Saved, ✅ Archived, ✅ Git tracked
│   ├── morning_watchlist.csv                   # ✅ Saved, ✅ Archived, ❌ Git excluded
│   ├── morning_playbook.md                     # ✅ Saved, ✅ Archived, ❌ Git excluded
│   ├── midday_watchlist.csv                    # ✅ Saved, ✅ Archived, ❌ Git excluded
│   ├── midday_playbook.md                      # ✅ Saved, ✅ Archived, ❌ Git excluded
│   ├── close_watchlist.csv                     # ✅ Saved, ✅ Archived, ❌ Git excluded
│   ├── close_playbook.md                       # ✅ Saved, ✅ Archived, ❌ Git excluded
│   ├── daily_playbook.md                       # ✅ Saved, ✅ Archived, ❌ Git excluded
│   └── archive/                                # ✅ Git tracked (entire folder)
│       └── 2026-03-06/                         # Daily archive folder
│           ├── INDEX.md                        # Auto-generated index
│           ├── catalyst_scores_200015.json     # Timestamped archives
│           ├── smart_money_signals_morning_084530.json
│           ├── smart_money_signals_midday_120015.json
│           ├── morning_watchlist_csv_084530.csv
│           ├── morning_playbook_md_084530.md
│           ├── midday_watchlist_csv_120015.csv
│           ├── midday_playbook_md_120015.md
│           └── ...
```

---

## 🔄 What Gets Saved & Archived

### 1. News Research (`run-news`)

**Saved Files:**
- `outputs/catalyst_scores.json`
  - Contains: Symbol → Catalyst Score mapping
  - Format: `{"NVDA": 84.5, "TSLA": 76.2, ...}`
  - Used by: All subsequent scans (morning, midday, close)

**Archived:**
- `outputs/archive/YYYY-MM-DD/catalyst_scores_HHMMSS.json`
  - Timestamped copy saved automatically
  - Example: `catalyst_scores_200015.json` (run at 20:00:15)

---

### 2. Smart Money Tracking (NEW!)

**Saved Files:**
- `outputs/smart_money_signals_morning.json`
- `outputs/smart_money_signals_midday.json`

**Content:**
```json
{
  "generated_at": "2026-03-06T08:45:30Z",
  "session_tag": "morning",
  "signals": {
    "NVDA": {
      "symbol": "NVDA",
      "smart_money_score": 78.5,
      "insider_trades": [
        {
          "insider_name": "Jensen Huang",
          "insider_title": "CEO",
          "transaction_date": "2026-03-05T00:00:00",
          "transaction_type": "Purchase (Open Market)",
          "shares": 10000,
          "price_per_share": 850.0,
          "total_value": 8500000.0,
          "is_significant": true
        }
      ],
      "institutional_positions": [
        {
          "institution_name": "ARK Investment Management",
          "shares_held": 2100000,
          "market_value": 1785000000.0,
          "percent_of_portfolio": 4.2,
          "change_from_prior_quarter": 685000,
          "percent_change": 32.7
        }
      ],
      "congressional_trades": []
    }
  }
}
```

**Archived:**
- `outputs/archive/YYYY-MM-DD/smart_money_signals_morning_HHMMSS.json`
- `outputs/archive/YYYY-MM-DD/smart_money_signals_midday_HHMMSS.json`

---

### 3. Morning Pre-Market Scan (`run-morning`)

**Saved Files:**
- `outputs/morning_watchlist.csv` - Trade setups in CSV format
- `outputs/morning_playbook.md` - Human-readable trading plan

**Archived:**
- `outputs/archive/YYYY-MM-DD/morning_watchlist_csv_HHMMSS.csv`
- `outputs/archive/YYYY-MM-DD/morning_playbook_md_HHMMSS.md`

---

### 4. Midday Scan (`run-midday`)

**Saved Files:**
- `outputs/midday_watchlist.csv`
- `outputs/midday_playbook.md`

**Archived:**
- `outputs/archive/YYYY-MM-DD/midday_watchlist_csv_HHMMSS.csv`
- `outputs/archive/YYYY-MM-DD/midday_playbook_md_HHMMSS.md`

---

### 5. Close Scan (`run-close`)

**Saved Files:**
- `outputs/close_watchlist.csv`
- `outputs/close_playbook.md`

**Archived:**
- `outputs/archive/YYYY-MM-DD/close_watchlist_csv_HHMMSS.csv`
- `outputs/archive/YYYY-MM-DD/close_playbook_md_HHMMSS.md`

---

### 6. Full Day Run (`run-day`)

**Saved Files:**
- `outputs/daily_playbook.md` - Combined morning + midday playbook
- All morning and midday files (watchlists, playbooks, smart money)

**Archived:**
- `outputs/archive/YYYY-MM-DD/daily_playbook_HHMMSS.md`
- All morning and midday archives

---

## 🗄️ Archive System

### How Archives Work

1. **Automatic Archiving:** Every run creates a timestamped copy in `outputs/archive/YYYY-MM-DD/`
2. **Current Files Overwritten:** Files in `outputs/` are always current (latest run)
3. **Historical Data Preserved:** Archives never overwritten, accumulate over time
4. **Daily Folders:** One folder per day: `2026-03-06/`, `2026-03-07/`, etc.
5. **INDEX.md Generated:** Each day gets an auto-generated index of all runs

### Archive Filename Format

```
{type}_{format}_{timestamp}.{ext}

Examples:
- catalyst_scores_200015.json           # News research at 20:00:15
- smart_money_signals_morning_084530.json  # Smart money at 08:45:30
- morning_watchlist_csv_084530.csv      # Morning watchlist at 08:45:30
- morning_playbook_md_084530.md         # Morning playbook at 08:45:30
- midday_watchlist_csv_120015.csv       # Midday watchlist at 12:00:15
```

### Archive INDEX.md Example

```markdown
# Daily Trading Archive - 2026-03-06

## Archived Runs

### NEWS RESEARCH
- **20:00:15** - [catalyst_scores_200015.json](./catalyst_scores_200015.json)

### SMART MONEY TRACKING
- **12:00:15** - [smart_money_signals_midday_120015.json](./smart_money_signals_midday_120015.json)
- **08:45:30** - [smart_money_signals_morning_084530.json](./smart_money_signals_morning_084530.json)

### MORNING PRE-MARKET (8:45 AM)
- **08:45:30** - [morning_watchlist_csv_084530.csv](./morning_watchlist_csv_084530.csv)
- **08:45:30** - [morning_playbook_md_084530.md](./morning_playbook_md_084530.md)

### MIDDAY SCAN (12:00 PM)
- **12:00:15** - [midday_watchlist_csv_120015.csv](./midday_watchlist_csv_120015.csv)
- **12:00:15** - [midday_playbook_md_120015.md](./midday_playbook_md_120015.md)
```

---

## 🔒 Git Tracking

### What IS Tracked by Git

✅ **JSON Data Files:**
- `outputs/catalyst_scores.json`
- `outputs/smart_money_signals_*.json`
- All archived JSON files

✅ **Archive Folder:**
- `outputs/archive/` and all subfolders
- Timestamped historical data preserved in repo

✅ **Documentation:**
- All `.md` files in root directory

### What is NOT Tracked by Git

❌ **CSV Files:**
- `outputs/*.csv` (excluded by .gitignore)
- Generated fresh each run from data

❌ **Markdown Playbooks:**
- `outputs/*.md` (excluded by .gitignore)
- Human-readable reports regenerated each time

❌ **Secrets:**
- `config/broker.yaml` (API keys)

### Why This Approach?

1. **Data Preserved:** JSON files contain structured data worth versioning
2. **Reduce Clutter:** CSV/MD regenerated from data, no need to track
3. **Historical Record:** Archives show trading activity over time
4. **Reproducibility:** Can regenerate reports from archived JSON
5. **Security:** API keys never tracked

---

## 📊 Data Lifecycle

### Current Files (Overwritten)
```
[Morning Run 08:45 AM]
  → outputs/morning_watchlist.csv         (OVERWRITTEN)
  → outputs/morning_playbook.md           (OVERWRITTEN)
  → outputs/smart_money_signals_morning.json  (OVERWRITTEN)

[Midday Run 12:00 PM]
  → outputs/midday_watchlist.csv          (OVERWRITTEN)
  → outputs/midday_playbook.md            (OVERWRITTEN)
  → outputs/smart_money_signals_midday.json   (OVERWRITTEN)
```

### Archived Files (Preserved Forever)
```
outputs/archive/2026-03-06/
  ├── morning_watchlist_csv_084530.csv
  ├── morning_playbook_md_084530.md
  ├── smart_money_signals_morning_084530.json
  ├── midday_watchlist_csv_120015.csv
  ├── midday_playbook_md_120015.md
  └── smart_money_signals_midday_120015.json

outputs/archive/2026-03-05/
  └── [previous day's runs...]

outputs/archive/2026-03-04/
  └── [older runs...]
```

---

## 🔧 Usage Examples

### View Current Data
```bash
# Latest catalyst scores
cat outputs/catalyst_scores.json

# Latest smart money signals (morning)
cat outputs/smart_money_signals_morning.json

# Latest morning playbook
cat outputs/morning_playbook.md
```

### View Historical Data
```bash
# List all archived days
ls outputs/archive/

# View specific day's index
cat outputs/archive/2026-03-06/INDEX.md

# View archived smart money data
cat outputs/archive/2026-03-06/smart_money_signals_morning_084530.json
```

### Programmatic Access
```python
import json
from pathlib import Path

# Load current smart money signals
signals_path = Path("outputs/smart_money_signals_morning.json")
with signals_path.open() as f:
    data = json.load(f)
    
print(f"Generated at: {data['generated_at']}")
print(f"Symbols tracked: {list(data['signals'].keys())}")

for symbol, info in data['signals'].items():
    print(f"\n{symbol}:")
    print(f"  Smart Money Score: {info['smart_money_score']}")
    print(f"  Insider Trades: {len(info['insider_trades'])}")
    print(f"  Institutional Positions: {len(info['institutional_positions'])}")
```

---

## 🎯 Summary

| File Type | Saved? | Archived? | Git Tracked? | Purpose |
|-----------|--------|-----------|--------------|---------|
| `catalyst_scores.json` | ✅ | ✅ | ✅ | News catalyst data |
| `smart_money_signals_*.json` | ✅ | ✅ | ✅ | Insider/institutional trades |
| `*_watchlist.csv` | ✅ | ✅ | ❌ | Trade setup data |
| `*_playbook.md` | ✅ | ✅ | ❌ | Human-readable reports |
| `archive/` folder | N/A | ✅ | ✅ | Historical preservation |
| `INDEX.md` | Generated | ✅ | ✅ | Daily index |

**Key Points:**
- ✅ **Everything is saved** to disk
- ✅ **Everything is archived** with timestamps
- ✅ **Structured data (JSON) is tracked** in git
- ✅ **Historical data is preserved** in archive folders
- ✅ **Current files are always up-to-date** (overwritten each run)
- ✅ **Smart money/insider data now persisted!** (NEW feature)

---

## 🚀 Next Steps

1. **Run a scan** to generate files:
   ```bash
   python -m tradingbot.cli run-morning
   ```

2. **Check outputs** folder:
   ```bash
   ls outputs/
   ```

3. **View archives**:
   ```bash
   ls outputs/archive/$(date +%Y-%m-%d)/
   cat outputs/archive/$(date +%Y-%m-%d)/INDEX.md
   ```

4. **Commit to git** (JSON files will be tracked):
   ```bash
   git add outputs/*.json outputs/archive/
   git commit -m "Save trading data"
   git push
   ```

---

**Generated:** March 6, 2026  
**Everything is now persisted and archived!** 🎉
