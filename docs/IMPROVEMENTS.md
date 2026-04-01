# Algorithm Improvements Tracker

## Status Key
- [ ] Not started
- [x] Completed
- [~] In progress

## Validation Summary (2026-03-31)

All issues reviewed and triaged. Verdicts:

| # | Issue | Verdict | Fix Status |
|---|-------|---------|------------|
| 0 | TradeCard.side crash | **CRITICAL** — 9 refs to `.side` which didn't exist | **FIXED** — added `side: str = "long"` to TradeCard + CloseHoldPick |
| NEW | Fakeout guard wrong field names | **CRITICAL** — `card.stop_loss`/`card.entry` don't exist on TradeCard | **FIXED** — corrected to `card.stop_price`/`card.entry_price` |
| 1 | EMA hold too loose | **REAL** but nuanced — pullback_low is invalidation level, often below EMA9 by design | Deferred — reverted, needs more analysis |
| 2 | abs() in gap scanner | **REAL** (low impact) — negative gaps waste slots | **FIXED** — removed abs(), long-only |
| 3 | Relvol overflow | **PARTIAL** — scoring already caps at 100, gate check is fine | Won't fix |
| 4 | NaN kills scores | **REAL** — NaN from ta library silently drops candidates | **FIXED** — NaN guard defaults to 50.0 + logs warning |
| 5 | RSI peak at 60 | **DEBATABLE** — RSI 60 is defensible for gap-and-go; weight is only 10% | Won't fix |
| 6 | Gap quality cliff at 6% | **NEGLIGIBLE** — 8% weight × 20pt diff = 1.6pt impact | Won't fix |
| 7 | OBV binary scoring | **NEGLIGIBLE** — 6% weight × 55pt diff = 3.3pt; most gappers score 80 anyway | Won't fix |
| 8 | Midday multiplier 1.3x | **REAL but low** — has fallback path (relvol + 50K premarket) | Won't fix now |
| 9 | No trend filter | Valid improvement — new feature, not a bug | Backlog |
| 10 | Stop too tight at open | Partially addressed by fakeout guard (now fixed) | Backlog |
| 11 | Dynamic R:R | Tuning preference, MIN_RR=1.5 is standard | Backlog |
| 12 | Dollar volume 5x estimate | **RARELY HIT** — dead code for any stock with 1+ day history | Won't fix |
| 13 | No volume decay detection | New feature | Backlog |
| 14 | Streak scaling ignores quality | **BY DESIGN** — conservative sizing after losses is intentional | Won't fix |

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
**Fix applied**: Added `_safe()` method that defaults NaN/Inf to 50.0 (neutral) and logs a warning. Applied to all 10 scoring components in both ranker classes.
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
3. ~~#4 NaN guard~~ — `_safe()` method defaults NaN to 50.0 + logs
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
