# Execution Engine — Full Plan

## Overview

Automated order execution system for the Gap & Go day trading alert bot. Transitions from alert-only mode to fully automated paper/live trading via **Interactive Brokers (IBKR)** Trading API.

### Broker Migration: Alpaca → IBKR

| Aspect | Alpaca (current — alerts only) | IBKR (target — full execution) |
|---|---|---|
| Role | Data source for scanning | Data source + order execution |
| Canadian support | ❌ Blocked | ✅ Fully supported |
| Trading API | Not usable (Canadian) | Full bracket orders, trailing, OCA |
| Data quality | IEX free / SIP $9/mo | Free delayed / real-time $1.50/mo (waived at $10/mo commissions) |
| Commission | $0 | $0.005/share (min $1, max 0.5% of trade) |
| Connection model | REST API + API key | Socket via TWS/IB Gateway + `ib_insync` |
| Paper trading | Separate paper account | Separate paper account (port 4002) |

**Migration plan**: Build IBKR data client (`ibkr_client.py`) as drop-in replacement for `alpaca_client.py`. Both implement the same interface so the scanner, ranker, and all analysis modules remain untouched. Alpaca code is preserved but not used at runtime.

### Infrastructure: VPS Deployment

IBKR requires IB Gateway running as a persistent local process (cannot run on Heroku/Render).

| Component | Current | After IBKR |
|---|---|---|
| Web dashboard | Heroku ($7/mo) | Keep on Heroku (or move to VPS) |
| Cron jobs | Render (free, 6 jobs) | VPS crontab (free, no limits) |
| Data source | Alpaca REST API | IB Gateway socket (localhost:4001/4002) |
| Order execution | None (alert-only) | IB Gateway → real bracket orders |

**VPS options** (Ubuntu 22.04):

| Provider | Plan | Cost | Notes |
|---|---|---|---|
| Oracle Cloud | Free tier ARM | $0/mo | Good for testing |
| Hetzner CX22 | 2 vCPU / 2 GB | ~$4/mo | Best value, US East datacenter |
| DigitalOcean | Basic | $6/mo | Simple UI |
| Linode | Nanode | $5/mo | Toronto datacenter available |

**VPS stack**:
```
VPS (Ubuntu 22.04)
├── IB Gateway (always running, port 4001 live / 4002 paper)
├── IBC (open-source — auto-restarts Gateway daily, handles re-auth)
├── tradingbot (this codebase)
│   ├── cron: run-news     (20:00 ET)
│   ├── cron: run-morning  (08:45 ET)
│   ├── cron: run-midday   (12:00 ET)
│   ├── cron: run-close    (15:30 ET)
│   └── cron: trade-tracker (every 5 min, 9:30–4:00 ET)
├── Flask dashboard (optional, or keep on Heroku)
└── Supabase (unchanged — remote persistence)
```

**IBC (IB Controller)**: Open-source tool that auto-starts IB Gateway, handles the mandatory daily restart (11:45 PM ET), and re-authenticates. Required for unattended operation. See: https://github.com/IbcAlpha/IBC

---

## 1. Account & Scaling

| Stage | Account | Type | Trades/day |
|---|---|---|---|
| Phase 1 | IBKR Paper $1M virtual | Margin | Unlimited |
| Phase 2 | IBKR Live $1K | Margin (cash-only usage) | 3 per 5 rolling days (PDT) |
| Phase 3 | IBKR Live $10K | Margin (cash-only usage) | 3 per 5 rolling days (PDT) |
| Phase 4 | IBKR Live $25K+ | Margin | Unlimited |

- Never borrow on margin — allocator enforces trading within cash balance only.
- Margin account type is used solely for instant settlement (no T+2 delay).
- PDT rule (Pattern Day Trader): under $25K, max 3 day trades per 5 rolling business days.
- Canadian IBKR account trades US equities via routed orders to US exchanges.

---

## 2. Capital Allocation

| Rule | Value |
|---|---|
| Max concurrent positions | 3 |
| Max morning entries | 2 |
| Reserve for midday | At least 1 slot |
| Risk per trade | 0.5% of account |
| Max single position | 40% of account |
| Max notional per trade | $10K (or 50% of account, whichever is less) |
| Slots free instantly | On any close (stop, TP, trail, expire) |

- When multiple alerts fire in the same scan, rank by `card.score` (highest first) and take the best N that fit available capital.
- Alerts that can't be executed are still sent to Telegram tagged "📊 Alert Only" so full system performance can be tracked.

---

## 3. Order Types

| Session | Entry Order | Why |
|---|---|---|
| Morning (8:45–10:00 ET) | Limit at scan price + 0.1% buffer | Protects against opening volatility slippage |
| Midday (10:00–2:30 ET) | Market order | Calmer spreads, faster fills |
| Time-in-force | `day` | Auto-cancels at 4 PM if unfilled |

**Bracket order structure** (one API call per trade via IBKR):
- **Entry**: limit or market (per above)
- **Stop loss**: stop order at `card.stop_price`
- **Take profit**: limit sell at `card.tp1_price`
- IBKR uses **OCA (One-Cancels-All)** groups — when stop or TP fills, the other auto-cancels.

```python
# IBKR bracket order example (ib_insync)
parent = LimitOrder('BUY', qty, entry_price)
tp     = LimitOrder('SELL', qty, tp1_price)
stop   = StopOrder('SELL', qty, stop_price)
ib.placeOrder(contract, parent)  # parent order
ib.placeOrder(contract, tp)      # OCA child
ib.placeOrder(contract, stop)    # OCA child
```

---

## 4. Morning Deadline (10:30 AM ET)

Morning trades must resolve by 10:30 AM to free capital for midday:

| Condition at 10:30 AM | Action |
|---|---|
| Price > entry + 0.1% (winning) | Trail stop to breakeven, let it ride |
| Price < entry (losing) | Cancel stop + TP orders, market sell immediately |
| Price ≈ entry ±0.1% (flat) | Cancel stop + TP orders, market sell immediately |

**Rationale**: Gap & Go momentum plays typically move within 30–60 minutes. By 10:30 (one hour after open), winners are already running and losers are dead money. First midday scans fire at 10:00–10:30 — freed capital is immediately available.

---

## 5. Trailing Stages (Real Orders)

Same logic as the current simulated tracker, but executed as real IBKR order modifications:

| Trigger | Action |
|---|---|
| 1R gain | Modify stop order → entry price (breakeven) |
| 2R gain | Modify stop order → entry + 1R (lock profit) |
| TP1 hit (limit fills on IBKR) | Modify stop order → TP1 price (lock TP1) |
| Price drops below trailed stop | Stop order fills automatically on IBKR |
| Expire (3:30 PM ET) | Cancel all pending orders, market sell remaining shares |

Checked every 5 minutes by the existing tracker cron job. IBKR order modifications use `ib.placeOrder()` with the same `orderId` to replace the existing stop.

### 5b. Below-VWAP Scalp Mode

When a trade card has the `BELOW VWAP` warning (price below VWAP after a gap-up), the execution engine treats it as a **scalp-only trade**:

| Rule | Normal Trade | Below-VWAP Scalp |
|------|-------------|-------------------|
| TP1 sell | 50% of position | **100% of position** |
| TP2 | Remaining 50% trails | **Not used** |
| Trail after TP1 | Stop moves to TP1 | **Full exit at TP1** |

**Rationale**: Stocks trading below VWAP after a gap-up face institutional selling pressure. The bounce toward VWAP (TP1) is realistic; continuation to TP2 is statistically unlikely. Exiting 100% at TP1 preserves the 1:1 R:R while avoiding the common scenario of giving back gains.

Implementation: `OrderExecutor` checks `card.false_positive_flags` for `BELOW VWAP`. If present, the bracket order places the full position size on the TP1 limit sell (no partial). The trailing stop logic is skipped — TP1 fill closes the trade entirely.

---

## 6. Risk Protection

| Layer | Rule | Status |
|---|---|---|
| Streak scaling | 1 loss → 75% size, 2 → 50%, 3 → 35% | ✅ Built |
| Max consecutive losses | 3 losses in a row → locked out for day | ✅ Built |
| Daily loss lockout | -1.5% daily PnL → locked out for day | ✅ Built |
| Max trades/day | 5 total | ✅ Built |
| PDT counter | 3 day trades per 5 rolling days (under $25K) | 🔨 To build |
| Kill switch | Telegram `/killall` → cancel all orders + flatten all positions | 🔨 To build |

**Tilt protection**: After consecutive losses, keep trading at reduced size (streak scaling) rather than pausing. The position size reduction is gradual enough to preserve capital while allowing recovery.

---

## 7. Expire Flow (3:30 PM ET)

1. Cancel all pending stop and TP limit orders on IBKR via `ib.cancelOrder()`
2. Place market sell for all remaining open positions via `ib.placeOrder()`
3. Update `trade_outcomes` in Supabase with actual exit price and P&L
4. Send Telegram notification per closed trade with final result

---

## 8. Safety & Reliability

| Concern | Solution |
|---|---|
| Order rejected by IBKR | Retry once, then send Telegram error alert, mark as alert-only |
| Partial fill | Adjust stop/TP quantity to match actual filled shares |
| Network outage | Heartbeat monitor — if tracker misses 2 consecutive cycles, Telegram alert |
| IB Gateway disconnect | IBC auto-restarts; bot detects disconnect and pauses until reconnected |
| Manual close in IBKR TWS/app | Reconciliation check each cron cycle detects position drift |
| Position DB vs IBKR mismatch | Reconcile every 5-min cycle; IBKR is the source of truth |
| Halted stock | Detect halt, notify via Telegram, keep orders pending until unhalt |
| Daily Gateway restart (11:45 PM) | IBC handles automatically; no trades at risk (after market hours) |

---

## 9. Dashboard Changes

### Alert Cards — Execution Badges

| Badge | Meaning |
|---|---|
| 📊 **Alert Only** | Generated but not executed (no capital / no slot / PDT limit) |
| ✅ **Executed** | Real order placed and filled |
| ⏳ **Pending** | Order placed, waiting for fill |
| ❌ **Rejected** | Order failed (halted stock, insufficient funds, etc.) |

### Executed Trade Details

Each executed trade card shows:
- **Fill price** — actual entry from IBKR (not scan price)
- **Slippage** — fill price minus scan price
- **Shares** — actual filled quantity
- **Current P&L** — live from IBKR position data
- **Status** — open / TP1 hit / stopped / trailed out / expired
- **Exit price** — actual fill on close
- **Actual P&L** — real dollar and percentage result

### Stats Page — Two Views

1. **Simulated** — all alerts, regardless of execution (shows system's full potential)
2. **Executed** — only real trades (shows actual performance with capital constraints)

**Metrics tracked**:
- Win rate (wins / total executed trades)
- Average win % and average loss %
- Profit factor (total gains / total losses)
- Total P&L in dollars and percent
- Best and worst trade
- Average slippage (fill price vs scan price)
- Executed vs alert-only count

---

## 10. Modules to Build

| # | Module | Location | Purpose |
|---|---|---|---|
| 0 | **IBKRClient** | `src/tradingbot/data/ibkr_client.py` | Drop-in replacement for `alpaca_client.py`. Connects via `ib_insync` to IB Gateway. Provides snapshots, bars, quotes using same interface. |
| 1 | **CapitalAllocator** | `src/tradingbot/risk/capital_allocator.py` | Query IBKR buying power, track open exposure, max concurrent positions, PDT counter, morning slot limits |
| 2 | **OrderExecutor** | `src/tradingbot/execution/order_executor.py` | Place bracket orders (OCA groups), modify stops for trailing, cancel + market sell for expire/deadline, kill switch |
| 3 | **PositionMonitor** | `src/tradingbot/execution/position_monitor.py` | Reconcile DB with IBKR positions, detect manual closes, handle partial fills |
| 4 | **Config** | `config/risk.yaml` + `config/broker.yaml` | Execution mode toggle, slot limits, deadlines, PDT settings |
| 5 | **Telegram commands** | `src/tradingbot/notifications/telegram_notifier.py` | `/killall` (flatten everything), `/status` (open positions + buying power) |
| 6 | **Order notifications** | Same as above | Fill/modify/cancel confirmations sent to Telegram |

---

## 11. Configuration

### risk.yaml additions

```yaml
execution:
  mode: alert_only              # alert_only / paper / live
  max_concurrent_positions: 3
  max_morning_entries: 2
  reserve_midday_slots: 1
  morning_deadline: "10:30"     # ET — sell losers/flat, trail winners to BE
  entry_order_buffer_pct: 0.1   # limit order buffer above scan price (morning)
  midday_use_market_order: true # use market orders for midday session
  expire_time: "15:30"          # ET — cancel all orders + market sell
  pdt_protection: true          # track 3/5 rolling day trade limit
  kill_switch_enabled: true     # enable Telegram /killall command
```

---

## 12. Execution Flow

```
Alert fires
  │
  ├─ execution_mode == alert_only?  → Send Telegram alert only (current behavior)
  │
  ▼ execution_mode == paper or live
  │
  ├─ RiskManager.allow_new_trade()? → No: locked out (losses/max trades)
  │
  ├─ CapitalAllocator.pdt_ok()?     → No: "skipped: PDT limit"
  │
  ├─ CapitalAllocator.has_slot()?   → No: "skipped: max positions"
  │     └─ Morning session? Check max_morning_entries (2)
  │
  ├─ CapitalAllocator.can_afford()? → No: "skipped: no buying power"
  │
  ▼ All checks pass
  │
  OrderExecutor.submit_bracket_order(card)
    → Entry: limit+buffer (morning) or market (midday)
    → Stop: stop order at card.stop_price
    → TP1: limit sell at card.tp1_price
  │
  ▼ Telegram: "✅ Bought 100 AAPL @ $185.23 | Stop $183.50 | TP1 $187.00"
```

---

## 13. Cron Schedule

| Cron | Time (ET) | Action |
|---|---|---|
| Morning scan | 8:45 AM | Generate alerts, execute top 1–2 |
| Tracker | Every 5 min, 9:30–4:00 | Check prices, modify stops, trail |
| Morning deadline | 10:30 AM | Sell losers/flat, trail winners to breakeven |
| Midday scan | Every 30 min, 10:00–2:30 | Generate alerts, execute if slots available |
| Close/expire | 3:30 PM | Cancel all pending orders, market sell remaining |

---

## 14. Build Order

| Step | What | Depends on |
|---|---|---|
| 1 | CapitalAllocator + tests | — |
| 2 | OrderExecutor + tests (paper mode) | Step 1 |
| 3 | PositionMonitor + reconciliation | Step 2 |
| 4 | Wire into session_runner + trade_tracker | Steps 1–3 |
| 5 | Morning deadline cron (10:30 AM) | Step 4 |
| 6 | Telegram commands (/killall, /status) | Step 2 |
| 7 | Order fill notifications | Step 2 |
| 8 | PDT counter | Step 1 |
| 9 | Dashboard execution badges + stats views | Steps 1–4 |
| 10 | Parallel validation: alerts + paper side by side | All above |
| 11 | Switch to live | After paper validation |

---

## 15. Fees (IBKR)

| Item | Cost |
|---|---|
| Commission | $0.005/share (min $1, max 0.5% of trade value) |
| API access | Free |
| Market data (delayed) | Free |
| Real-time US data | $1.50/mo (waived if ≥$10/mo commissions — ~10 trades) |
| Paper trading | Free, unlimited |
| SEC fee | ~$0.0000278 × sell amount (fractions of a penny) |
| FINRA TAF | $0.000166 per share sold (fractions of a penny) |
| Currency conversion (CAD→USD) | $2 per conversion (one-time when funding) |

Typical cost per trade: **~$1–2** round trip. Real cost is **slippage** (difference between scan price and fill price) + commission. Tracked automatically in the executed stats view.

### Cost comparison

| Scenario | Alpaca (old) | IBKR |
|---|---|---|
| 5 trades/day | $0 | ~$5–10/day in commissions |
| Market data | $9/mo (SIP) or free (IEX) | $0–1.50/mo (waived with activity) |
| Infrastructure | Heroku $7 + Render free | VPS $0–6/mo + Heroku $7 (optional) |
| Canadian access | ❌ | ✅ |

---

## 16. Worst-Case Daily Loss Scenario ($1K Account)

| Trade | Size Multiplier | Risk (0.5%) | Loss | Cumulative |
|---|---|---|---|---|
| #1 | 100% (full) | $5.00 | -$5.00 | -0.50% |
| #2 | 75% (1 loss) | $3.75 | -$3.75 | -0.875% |
| #3 | 50% (2 losses) | $2.50 | -$2.50 | -1.25% |
| #4 | **LOCKED OUT** (3 consecutive losses) | — | — | -1.25% |

Maximum daily loss before lockout: **~1.25%** (~$12.50 on $1K account).

---

---

## 17. IBKR Integration — Build Order

| Step | What | Status |
|---|---|---|
| 1 | Create IBKR individual account (Canadian) | ✅ Done — awaiting approval (~24h) |
| 2 | Set up paper trading account | ⬜ After approval |
| 3 | Build `ibkr_client.py` data client (drop-in for `alpaca_client.py`) | ⬜ Can start now |
| 4 | Build `order_executor.py` with bracket/OCA orders | ⬜ Can start now |
| 5 | Write tests with mock `ib_insync` | ⬜ Can start now |
| 6 | Set up VPS (Oracle free or Hetzner $4/mo) | ⬜ After approval |
| 7 | Install IB Gateway + IBC on VPS | ⬜ After VPS |
| 8 | End-to-end paper trading test | ⬜ After steps 3–7 |
| 9 | Run paper + alerts side by side (2–4 weeks) | ⬜ After step 8 |
| 10 | Go live with $1K | ⬜ After validation |

### Python Dependencies

```
# Add to requirements.txt
ib_insync>=0.9.86    # IBKR Python client (socket-based, async-ready)
```

### Data Client Interface (shared between Alpaca and IBKR)

```python
class BrokerClient(Protocol):
    """Common interface — both AlpacaClient and IBKRClient implement this."""
    def get_snapshots(self, symbols: list[str]) -> dict[str, SymbolSnapshot]: ...
    def get_bars(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame: ...
    def get_latest_quote(self, symbol: str) -> dict: ...
    def get_latest_trade(self, symbol: str) -> dict: ...
```

The scanner, ranker, pattern detection, confluence engine, and all analysis modules call this interface — they never know which broker is behind it.

---

*Last updated: April 8, 2026*
