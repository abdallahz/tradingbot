# Pre-Push Checklist

## Status: CURRENT  March 10, 2026

All checks verified against commit `2d63601`.

---

##  Tests

```
pytest tests/ -v
18 passed, 1 warning in ~9s
```

Tests cover:
- `test_config_env.py`  env-var override of broker config
- `test_risk_manager.py`  trade gating, consecutive loss lockout
- `test_trade_card.py`  long/short price levels (entry > stop, TP2 > TP1 > entry)
- `test_session_runner.py`  mock-mode full run + real-mode init
- `test_news_aggregator.py`  catalyst scoring pipeline
- `test_insider_tracking.py`  smart money signal parsing

---

##  Code Quality

- [x] **No syntax errors**  `pytest` imports every module on startup; 0 import errors
- [x] **No dead code**  `cast()` removed from session_runner; `card_count=-1` branch removed from notifier
- [x] **DRY scheduler**  `_load_catalyst_scores()` + `_run_scan_session()` replace 3x duplicate methods
- [x] **Clean imports**  `json` and `logging` at module level in session_runner; no inline imports left
- [x] **Type annotations correct**  `run_morning/midday/close_only` return `int` (not `None`)
- [x] **`TradeCard` enhanced**  `risk_reward: float` and `generated_at: str` fields added
- [x] **`_alerts_sent_count`** initialized in `__init__` (not via fragile `getattr` fallback)

---

##  Security

- [x] `config/broker.yaml` is in `.gitignore`  not tracked
- [x] No API keys hardcoded anywhere in source
- [x] All secrets in Heroku Config Vars (production)
- [x] `config/broker.example.yaml` provides a safe template

**Verify before every push:**
```bash
git status   # broker.yaml must NOT appear
git diff --cached --name-only   # same check for staged files
```

---

##  Functionality

### Local mock mode
```bash
python -m tradingbot.cli run-day
# [MOCK DATA] Morning cards: 1 | Midday cards: 0
```

### Heroku live
- Web dyno: `https://aztradingbot-c8a5462555f3.herokuapp.com/api/health` -> `{"status":"ok"}`
- Worker dyno: fires 5 jobs per day at scheduled ET times
- Telegram: trade alerts, news summaries, "no setups found" messages all confirmed working

---

##  Architecture Reference

```
src/tradingbot/
 app/
    scheduler.py       Scheduler; _load_catalyst_scores + _run_scan_session helpers
    session_runner.py  SessionRunner; _fetch_snapshots, _get_night_research_picks, _build_cards
    worker.py          Persistent 60-second loop; 5 job handlers; _find_root()
 analysis/
    chart_generator.py
    market_conditions.py
    pattern_detector.py
    technical_indicators.py
 data/
    alpaca_client.py
    mock_data.py
 models.py              SymbolSnapshot, TradeCard (+ risk_reward, generated_at), RiskState, ...
 notifications/
    telegram_notifier.py    send_trade_alert, send_text, send_news_summary, send_session_summary
 ranking/ranker.py
 reports/
    archive_manager.py
    watchlist_report.py
 research/
    catalyst_scorer.py
    insider_tracking.py
    news_aggregator.py
    ...
 risk/risk_manager.py
 scanner/gap_scanner.py
 signals/
    indicators.py
    pullback_setup.py
 strategy/trade_card.py     build_trade_card; sets risk_reward + generated_at
 web/
    alert_store.py         JSONL persistence; card_to_dict includes risk_reward
    app.py                 Flask routes; _find_root()
    templates/dashboard.html
 cli.py
 config.py
```

---

##  Heroku Deployment

**Procfile:**
```
web:    PYTHONPATH=src gunicorn --workers 2 --bind 0.0.0.0:$PORT tradingbot.web.app:app
worker: PYTHONPATH=src python -m tradingbot.app.worker
```

**Required Config Vars:**
```
ALPACA_API_KEY
ALPACA_API_SECRET
ALPACA_PAPER=true
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
SEC_USER_AGENT
```

**Python version:** `3.10` (pinned via `.python-version`)

**Known Heroku constraints:**
- `torch` / `transformers` excluded from `requirements.txt` (slug size limit)
- FinBERT degrades gracefully to keyword-based sentiment
- Alert store (`alerts.jsonl`) is ephemeral per dyno  web and worker have separate files

---

##  Files NOT to Commit

```
config/broker.yaml        # live API credentials
venv/                     # virtual environment
__pycache__/              # Python cache
*.pyc
.pytest_cache/
outputs/*.csv             # generated reports (auto-archived)
```

---

## Push Command

```bash
git add -A
git status   # verify broker.yaml absent
git commit -m "feat: <description>"
git push
```

Heroku auto-deploys from `main` if GitHub integration is enabled,
or trigger manually: Heroku dashboard -> Deploy tab -> Deploy Branch.

---

**Last verified:** March 10, 2026 | Commit `2d63601` | 18/18 tests passing
