# Algorithm Improvements Tracker

> **Last updated:** April 3, 2026

## Status Key
- [ ] Not started
- [x] Completed
- [~] In progress

## Summary Table

| # | Issue | Verdict | Fix Status |
|---|-------|---------|------------|
| 0 | TradeCard.side crash | **CRITICAL** | **FIXED** (2026-03-31) |
| 0b | Fakeout guard wrong field names | **CRITICAL** | **FIXED** (2026-03-31) |
| 1 | EMA hold too loose | REAL but nuanced | Deferred |
| 2 | abs() in gap scanner | REAL | **FIXED** (2026-03-31) |
| 3 | Relvol overflow | PARTIAL | Won't fix |
| 4 | NaN kills scores | REAL | **FIXED** (2026-03-31) |
| 5 | RSI peak at 60 | DEBATABLE | Won't fix |
| 6 | Gap quality cliff at 6% | NEGLIGIBLE | Won't fix |
| 7 | OBV binary scoring | NEGLIGIBLE | Won't fix |
| 8 | Midday multiplier 1.3x | REAL but low | Won't fix now |
| 9 | No trend filter | New feature | Backlog |
| 10 | Stop too tight at open | Partially addressed | Backlog |
| 11 | Dynamic R:R | Tuning preference | Backlog |
| 12 | Dollar volume 5x estimate | RARELY HIT | Won't fix |
| 13 | No volume decay detection | New feature | Backlog |
| 14 | Streak scaling ignores quality | BY DESIGN | Won't fix |
| 15 | Gap fill probability model | New feature | Backlog |
| 16 | First 5-min volatility check | New feature | Backlog |
| 17 | Sector correlation filter | New feature | Backlog |
| 18 | Volume profile time-of-day | New feature | Backlog |
| 19 | Entry timing signal | New feature | Backlog |

### Additional Fixes Applied (2026-04-01 – 2026-04-02)

These fixes were applied based on live performance analysis and are not in the original tracker:

| Fix | Commit | Date | Description |
|-----|--------|------|-------------|
| min_score 40→50 | `1268d82` | Apr 1 | Raise ranker floor to filter marginal setups |
| price_min $1→$5 | `1268d82` | Apr 1 | Eliminate penny stocks (both strict + relaxed) |
| max_candidates 10→8 | `1268d82` | Apr 1 | Focus on highest-quality picks per session |
| Market guard tightened | `1268d82` | Apr 1 | Yellow threshold -0.5%→-0.3% (catch weakness earlier) |
| Confluence scoring fix | `1268d82` | Apr 1 | 60/40 blend of ranker + confluence engine |
| Gap fade detection | `1268d82` | Apr 1 | Block gapped-up stocks trading below VWAP |
| Screener catalyst default | `7d92f82` | Apr 1 | Screener movers without catalyst data get 30 (not 50) |
| O2 relaxed price_min=$5 | `7d92f82` | Apr 1 | Option 2 was bypassing price floor |
| Telegram retry logic | `8a4a054` | Apr 1 | 1.5s delay, 2 retries with 429 backoff |
| Inverse/VIX ETF blocker | `8a4a054` | Apr 1 | Block TZA, SQQQ, SPXS, SDOW, UVIX, UVXY in long-only mode |
| WORKER_ENABLED gate | `21514df` | Apr 2 | Prevent duplicate scans from Heroku+Render |
| Missing inverse ETFs | `6852030` | Apr 2 | Added SPDN, SH, DOG, RWM, PSQ, SRTY, HDGE |
| Secondary price guard | `6852030` | Apr 2 | Hard floor at scanner.price_min in `_build_cards` |
| Yellow regime penalty | `1268d82` | Apr 1 | +5 point score floor during yellow market regime |

### Performance Impact

5-day analysis (Mar 27 – Apr 2): 72 trades, 47% WR. 
- 12 preventable losses identified: 6 low-price (<$5), 6 inverse/VIX ETFs
- All 12 now blocked by the fixes above
- Estimated WR improvement: ~47% → ~57%+ with fixes active
- Catalyst gate 40→50 analysis: ZERO trades had catalyst < 50, so no additional impact

---

## Tier 0: Runtime Crashes

### 0. [x] TradeCard.side references crash at runtime
**Files**: `src/tradingbot/cli.py:167`, `src/tradingbot/notifications/telegram_notifier.py:162,269`, `src/tradingbot/reports/watchlist_report.py:34,63,180,205`, `src/tradingbot/analysis/ai_trade_validator.py:170`, `demo_phase7.py:192`
**Problem**: The `side` field was removed from `TradeCard` in commit `e2babcb` (long-only cleanup), but 9 references to `card.side` or `p.side` remain. Any code path that hits these lines will crash with `AttributeError`.
**Fix applied**: Added `side: str = "long"` as a constant default to both `TradeCard` and `CloseHoldPick` dataclasses.
**Impact**: Prevents crashes in Telegram notifications, CLI output, watchlist reports, and AI validation.

### NEW. [x] Fakeout guard uses wrong field names
**File**: `src/tradingbot/app/session_runner.py:645-649`
**Problem**: Lines referenced `card.stop_loss` and `card.entry` but `TradeCard` has `stop_price` and `entry_price`. The fakeout guard silently crashed on `card.stop_loss` (AttributeError) and the stop was never widened. **The fakeout guard has never worked.**
**Fix applied**: Changed to `card.stop_price` and `card.entry_price`.
**Impact**: Fakeout guard now actually widens stops during the 9:30-9:45 ET opening window.

---

## Tier 1: Broken Logic (actively hurting performance)

### 1. [ ] EMA hold is too loose — DEFERRED
**File**: `src/tradingbot/signals/indicators.py:12`
**Problem**: Current code: `stock.pullback_low >= stock.ema20 and stock.price >= stock.ema9`. This allows a stock to crash through EMA9, bounce off EMA20, and still call it a "hold." That's a bounce, not a hold — real holds never break the fast EMA.
**Validation verdict**: REAL but nuanced — `pullback_low` is the invalidation level (often below EMA9 by design). Tightening to EMA9 is a valid momentum filter but may be too aggressive given how `pullback_low` is computed (it factors in ATR). Needs more analysis before changing.
**Status**: Deferred — fix was applied then reverted. Left at `ema20` for now.

### 2. [x] Gap scanner uses abs() but system is long-only
**File**: `src/tradingbot/scanner/gap_scanner.py:39`
**Problem**: `abs()` lets -5% gaps pass the filter (they show as 5% gap magnitude). Negative gaps waste ranking slots for the long-only system.
**Validation verdict**: REAL but low impact — downstream checks (ranker signal alignment penalty + _build_cards negative gap block) would mostly catch them, but they still waste compute and slots.
**Fix applied**: Removed `abs()`. Negative gaps now fail naturally since they're below 0.5% threshold.
**Impact**: Stops wasting compute on stocks gapping the wrong direction.

### 3. [ ] Relative volume overflow on thin stocks — WON'T FIX
**File**: `src/tradingbot/signals/pullback_setup.py:34`, `src/tradingbot/ranking/ranker.py:46-53`
**Validation verdict**: PARTIALLY handled. The ranker's `_normalize_rel_vol` already caps scoring at 100 (80 + 20 bonus). A 100x relvol scores the same as 10x. The pullback gate check (`relative_volume >= multiplier`) doesn't overflow — it just passes. The catalyst gate requires `premarket_volume >= 50_000` as a secondary check, which filters most illiquid names. Lower severity than originally stated.

### 4. [x] NaN kills scores silently
**File**: `src/tradingbot/ranking/ranker.py` (both `Ranker.score()` and `CatalystWeightedRanker.score()`)
**Problem**: If any indicator (RSI, MACD, OBV) returns NaN, the entire weighted sum becomes NaN. `NaN >= self.min_score` returns False, so the stock silently vanishes.
**Validation verdict**: REAL. Confirmed: `ta` library returns NaN for early bars, `float(NaN)` propagates, NaN is truthy (bypasses `not rsi` guard), all comparisons return False.
**Fix applied**: Added `math.isfinite()` guards at the data boundary — in `_normalize_rsi()`, `_normalize_macd()`, and the `catalyst_score` read in `score()`. NaN/Inf values default to 50.0 (neutral). No wrapping of every scoring call needed since NaN only enters through `tech_indicators` dict and `catalyst_score`.
**Impact**: Stops losing valid candidates to one missing data point.

---

## Tier 2: Scoring Calibration (leaving edge on the table)

### 5. [ ] RSI scoring peaks at 60, should peak at 65-70 — DEBATABLE, WON'T FIX
**File**: `src/tradingbot/ranking/ranker.py:71-93`
**Validation verdict**: For gap-and-go stocks, RSI 60 = healthy trend without being overextended. RSI 70+ means the stock already moved significantly — higher gap-fill risk. The current curve is defensible. RSI weight is only 10%. This is a tuning preference, not a bug.

### 6. [ ] Gap quality has a step function at 6% and 10% — NEGLIGIBLE, WON'T FIX
**File**: `src/tradingbot/ranking/ranker.py:228-233`
**Validation verdict**: Gap quality weight is 8%. Max score difference at the cliff is ~20pts × 0.08 = 1.6 points out of 100. Cannot meaningfully change any ranking. Not worth the code complexity.

### 7. [ ] OBV divergence is binary (80 or 25) — NEGLIGIBLE, WON'T FIX
**File**: `src/tradingbot/ranking/ranker.py:149-197`
**Validation verdict**: Weight is 6%. Binary 80 vs 25 = 55pt diff × 0.06 = 3.3 points. Most gap-and-go stocks have confirming OBV (gap up = volume up), so almost everything scores 80. Rarely matters.

### 8. [ ] Midday volume multiplier is too low (1.3x) — LOW PRIORITY
**File**: `config/indicators.yaml`
**Validation verdict**: REAL but uncertain impact. 1.3x is low for midday, but `has_valid_setup` has a fallback: if `relative_volume >= multiplier AND premarket >= 50K`, it passes even without a volume spike. The 1.3x isn't the only path. Worth raising to 1.8x eventually but not critical.

---

## Tier 3: Missing Signals (new edge)

### 9. [ ] No higher-timeframe trend check
**Problem**: A stock gapping +5% that's been in a downtrend for 2 weeks is a bear rally, not continuation. The system has no concept of whether the gap is WITH or AGAINST the larger trend.
**Fix**: Add a daily trend filter: require `daily_close > daily_ema20` for longs. Stocks gapping up into a downtrend get penalized or filtered.
**Where**: Add check in `_build_cards` or as a new scoring component in the ranker.
**Impact**: Eliminates gap-fill traps where the stock gaps up, attracts longs, then resumes its downtrend. This is likely the single biggest edge improvement.

### 10. [ ] Stop too tight at market open
**File**: `src/tradingbot/strategy/trade_card.py:86`
**Problem**: 0.5x ATR buffer for stop placement. In the first 15 minutes, the opening cross creates wicks that routinely span 1-2x ATR. Gets stopped out by noise then the stock continues.
**Fix**: Time-aware buffer:
- Before 10:00 AM: `atr_buffer = atr * 1.0`
- After 10:00 AM: `atr_buffer = atr * 0.5`
Or pass session context into `build_trade_card` and let it decide.
**Impact**: Reduces whipsaw stops during opening volatility. Keeps you in trades that work.

### 11. [ ] Dynamic R:R minimum based on signal strength
**File**: `src/tradingbot/strategy/trade_card.py:10`
**Problem**: Flat MIN_RR = 1.5 for all trades. A 90-score setup with 1.3 R:R gets rejected. A 42-score setup with 1.5 R:R gets accepted. This is backwards.
**Fix**: Scale MIN_RR by score:
- Score >= 80: MIN_RR = 1.2
- Score 60-80: MIN_RR = 1.5
- Score < 60: MIN_RR = 2.0
**Impact**: Accepts high-conviction setups that have slightly lower R:R but high win probability. Rejects weak setups that need big moves to work.

### 12. [ ] Dollar volume estimate uses rough 5× multiplier — WON'T FIX
**File**: `src/tradingbot/data/alpaca_client.py`
**Validation verdict**: RARELY HIT. Only triggers when `prev_volume` and `prev_close` are both missing/zero from Alpaca. For any stock with at least 1 day history, this path never runs. Dead code in practice.

### 13. [ ] No volume decay detection
**Problem**: A stock gapping on strong volume at 9:30 but volume dropping 60% by 10:15 is losing momentum. The system only checks a snapshot of volume — it doesn't track if participation is sustaining or fading.
**Fix**: Add "volume holding" signal: compare last 5 bars' average volume to first 5 bars' average volume. If ratio < 0.4, flag as "volume_fading" and penalize in ranking or block in card building.
**Where**: New function in `src/tradingbot/signals/indicators.py`, referenced in `pullback_setup.py`.
**Impact**: Avoids buying into fading momentum. Especially valuable for midday re-scans.

### 14. [ ] Streak scaling ignores next setup's quality — BY DESIGN, WON'T FIX
**File**: `src/tradingbot/risk/risk_manager.py:28-48`
**Validation verdict**: Conservative sizing after losses is a risk management feature. Making it quality-aware partially defeats the purpose — you're supposed to reduce exposure after a losing streak regardless. Working as intended.

---

## Tier 4: Larger Features (future work)

### 15. [ ] Gap fill probability model
**Problem**: Some gaps fill (price returns to pre-gap level), others extend. Currently all gaps are treated as continuation bets. A +10% gap on a stock at 52-week highs with huge volume will likely extend. A +4% gap on a stock in a downtrend with moderate volume will likely fill.
**Fix**: Build a simple probability model using gap size, trend alignment, volume ratio, and historical gap-fill rates. Score 0-100 for "continuation probability." Use as a ranker component or filter.
**Impact**: Reduces entries on gap-fill candidates.

### 16. [ ] First 5-minute volatility check
**Problem**: Pre-market data doesn't predict 9:30-9:35 AM chaos. A stock with calm pre-market can explode at the open with 2-3% wicks in the first minute, making clean entries impossible.
**Fix**: Track first-5-min range: `opening_range = (high_5min - low_5min) / entry`. If > 1.5%, skip or delay entry. Flag as "wait for range to settle."
**Impact**: Avoids entries during the most chaotic period.

### 17. [ ] Sector correlation filter
**Problem**: When SPY, QQQ, financials, and semis all gap down 3%, it's a macro event — not individual alpha. Individual stock setups are contaminated by correlated market moves.
**Fix**: Compute sector average gap. If individual stock gap is within 1 standard deviation of sector average, flag as "correlated_move" and penalize score.
**Impact**: Focuses on idiosyncratic (stock-specific) moves which have higher continuation probability.

### 18. [ ] Volume profile / time-of-day awareness
**Problem**: The system treats all times equally. But 50% of daily volume happens in the first hour. A "volume spike" at 2 PM means something very different than at 9:45 AM.
**Fix**: Build a time-of-day volume profile. Normalize volume signals against expected volume for that time slot.
**Impact**: Better signal accuracy across the full trading day.

### 19. [ ] Entry timing signal (pullback entry window)
**Problem**: Entry is always "current market price." But waiting 5-10 minutes for a mini-pullback on a gapper typically offers 0.2-0.5% better entry — which directly improves R:R.
**Fix**: Instead of "buy now at $50," flag "optimal entry zone: $49.50-$49.80 (pullback to VWAP/EMA9)." Alert when price enters the zone.
**Impact**: Better fills, better R:R, fewer immediate drawdowns after entry.

---

## Implementation Priority

**DONE — Fixed (runtime crashes + real bugs):**
1. ~~#0 TradeCard.side crash~~ — added `side: str = "long"` default
2. ~~NEW Fakeout guard field names~~ — corrected to `stop_price`/`entry_price`
3. ~~#4 NaN guard~~ — inline `math.isfinite()` checks in normalize methods + catalyst read
4. ~~#2 abs() gap scanner~~ — removed abs(), long-only

**Deferred (needs more analysis):**
- #1 (EMA hold) — tightening to ema9 may be too aggressive; reverted

**Won't fix (negligible or by design):**
- #3 (relvol) — scoring already caps it
- #5-7 — impact is 1-3 points out of 100
- #8 — has fallback path, low priority
- #12 — dead code in practice
- #14 — working as designed

**Backlog (new features, not bugs):**
- #9 (trend filter) — biggest single improvement potential
- #10 (stop timing) — partially solved by fakeout guard fix
- #11 (dynamic R:R)
- #13 (volume decay detection)
- #15-19 (gap fill model, 5-min vol, sector correlation, etc.)
