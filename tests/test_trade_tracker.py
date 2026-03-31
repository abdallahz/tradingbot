"""Tests for TradeTracker — _evaluate() with bar-based high/low detection."""

from tradingbot.tracking.trade_tracker import TradeTracker


def _make_trade(
    *,
    symbol="TEST",
    entry=10.0,
    stop=9.50,
    tp1=10.50,
    tp2=11.0,
    status="open",
    side="long",
    alerted_at=None,
    **extra,
):
    """Helper to build a trade-outcome dict."""
    t = {
        "id": 1,
        "symbol": symbol,
        "side": side,
        "entry_price": entry,
        "stop_price": stop,
        "tp1_price": tp1,
        "tp2_price": tp2,
        "status": status,
        "alerted_at": alerted_at,
    }
    t.update(extra)
    return t


class TestEvaluateSnapshotOnly:
    """Original snapshot-price-only behaviour (no bar data)."""

    def setup_method(self):
        self.tracker = TradeTracker()
        # Stub _trail_stop_to_level so DB isn't required
        self.tracker._trail_stop_to_level = lambda trade, level: None

    def test_tp1_hit(self):
        trade = _make_trade()
        assert self.tracker._evaluate(trade, 10.55) == "tp1_hit"

    def test_tp2_hit(self):
        trade = _make_trade()
        assert self.tracker._evaluate(trade, 11.05) == "tp2_hit"

    def test_stopped(self):
        trade = _make_trade()
        assert self.tracker._evaluate(trade, 9.45) == "stopped"

    def test_trailed_out_when_stop_above_entry(self):
        """When stop has been trailed above entry, hitting it is a profit."""
        trade = _make_trade(entry=10.0, stop=10.30, tp1=10.50, tp2=11.0)
        assert self.tracker._evaluate(trade, 10.25) == "trailed_out"

    def test_no_change(self):
        trade = _make_trade()
        assert self.tracker._evaluate(trade, 10.20) is None

    def test_tp2_upgrade_from_tp1_hit(self):
        trade = _make_trade(status="tp1_hit")
        assert self.tracker._evaluate(trade, 11.05) == "tp2_hit"

    def test_tp1_locked_after_trail(self):
        """When stop is trailed to tp1 and price drops, tp1_locked."""
        trade = _make_trade(status="tp1_hit", stop=10.50)
        assert self.tracker._evaluate(trade, 10.45) == "tp1_locked"


class TestEvaluateWithBarHighLow:
    """New bar-based detection: session_high / session_low catch between-poll hits."""

    def setup_method(self):
        self.tracker = TradeTracker()
        self.tracker._trail_stop_to_level = lambda trade, level: None

    # ── TP detection via bar high ─────────────────────────────────────

    def test_tp1_via_bar_high(self):
        """Current price below TP1 but bar high hit TP1 → tp1_hit."""
        trade = _make_trade(entry=2.71, stop=2.55, tp1=2.82, tp2=2.86)
        # price=2.70 is below TP1, but session_high=2.83 hit TP1
        result = self.tracker._evaluate(trade, 2.70, session_high=2.83)
        assert result == "tp1_hit"

    def test_tp2_via_bar_high(self):
        """Current price below TP2 but bar high hit TP2 → tp2_hit."""
        trade = _make_trade(entry=2.71, stop=2.55, tp1=2.82, tp2=2.86)
        # price=2.75 is below TP2, but session_high=2.88 hit TP2
        result = self.tracker._evaluate(trade, 2.75, session_high=2.88)
        assert result == "tp2_hit"

    def test_tp1_not_hit_when_bar_below(self):
        """Bar high didn't reach TP1, current price below → None."""
        trade = _make_trade(entry=2.71, stop=2.55, tp1=2.82, tp2=2.86)
        result = self.tracker._evaluate(trade, 2.75, session_high=2.79)
        assert result is None

    def test_tp2_upgrade_via_bar_from_tp1_hit(self):
        """Status=tp1_hit, bar high reached TP2 → tp2_hit."""
        trade = _make_trade(entry=2.71, stop=2.82, tp1=2.82, tp2=2.86, status="tp1_hit")
        result = self.tracker._evaluate(trade, 2.83, session_high=2.87)
        assert result == "tp2_hit"

    # ── Stop detection via bar low ────────────────────────────────────

    def test_stop_via_bar_low(self):
        """Current price above stop but bar low breached it → stopped."""
        trade = _make_trade(entry=10.0, stop=9.50, tp1=10.50, tp2=11.0)
        result = self.tracker._evaluate(trade, 9.80, session_low=9.45)
        assert result == "stopped"

    def test_trailed_out_via_bar_low(self):
        """Stop trailed above entry, bar low hit it → trailed_out."""
        trade = _make_trade(entry=10.0, stop=10.30, tp1=10.50, tp2=11.0)
        result = self.tracker._evaluate(trade, 10.35, session_low=10.25)
        assert result == "trailed_out"

    def test_stop_not_hit_when_bar_above(self):
        """Bar low above stop → no stop hit."""
        trade = _make_trade(entry=10.0, stop=9.50, tp1=10.50, tp2=11.0)
        result = self.tracker._evaluate(trade, 9.80, session_low=9.55)
        assert result is None

    # ── ATPC real-world scenario ──────────────────────────────────────

    def test_atpc_scenario(self):
        """ATPC entry=2.71, TP1=2.82, TP2=2.86, bar high hit 2.88 but
        snapshot only 2.65 → should detect tp2_hit."""
        trade = _make_trade(
            symbol="ATPC",
            entry=2.71,
            stop=2.55,
            tp1=2.82,
            tp2=2.86,
        )
        result = self.tracker._evaluate(trade, 2.65, session_high=2.88)
        assert result == "tp2_hit"

    # ── Trailing stop still works with bar data ───────────────────────

    def test_breakeven_trail_from_bar_high(self):
        """Bar high showed 0.75R move → should have trailed to breakeven.
        Then if eff_low is above stop, no stop hit."""
        trailed_to = []
        self.tracker._trail_stop_to_level = lambda t, lvl: trailed_to.append(lvl)

        # entry=10, stop=9.50, risk=0.50, 0.75R=10.375
        trade = _make_trade(entry=10.0, stop=9.50, tp1=10.50, tp2=11.0)
        # price below 0.75R but session_high hit 10.40 (>= 0.75R)
        result = self.tracker._evaluate(trade, 10.10, session_high=10.40)
        # The trailing should fire (breakeven)
        assert 10.0 in trailed_to  # trailed stop to entry

    def test_lock_1r_from_bar_high(self):
        """Bar high showed 1.5R move → should lock stop at entry+1R."""
        trailed_to = []
        self.tracker._trail_stop_to_level = lambda t, lvl: trailed_to.append(lvl)

        # entry=10, stop=9.50, risk=0.50, 1.5R=10.75
        trade = _make_trade(entry=10.0, stop=9.50, tp1=10.50, tp2=11.0)
        result = self.tracker._evaluate(trade, 10.30, session_high=10.80)
        # Should've trailed to entry + risk (10 + 0.50 = 10.50)
        assert 10.50 in trailed_to

    # ── Edge cases ────────────────────────────────────────────────────

    def test_zero_session_high_falls_back_to_price(self):
        """session_high=0.0 means no bar data — use only snapshot."""
        trade = _make_trade(entry=10.0, stop=9.50, tp1=10.50, tp2=11.0)
        result = self.tracker._evaluate(trade, 10.20, session_high=0.0)
        assert result is None  # 10.20 < 10.50

    def test_zero_session_low_falls_back_to_price(self):
        """session_low=0.0 means no bar data — use only snapshot."""
        trade = _make_trade(entry=10.0, stop=9.50, tp1=10.50, tp2=11.0)
        result = self.tracker._evaluate(trade, 9.80, session_low=0.0)
        assert result is None  # 9.80 > 9.50

    def test_both_tp_and_stop_hit_tp_wins(self):
        """If both bar high >= TP1 and bar low <= stop, TP takes
        priority (TP hit happened first in the session — trader
        would have taken partial)."""
        trade = _make_trade(entry=10.0, stop=9.50, tp1=10.50, tp2=11.0)
        result = self.tracker._evaluate(
            trade, 9.80, session_high=10.55, session_low=9.45
        )
        assert result == "tp1_hit"

    # ── Same-tick trail guard (ONDS bug fix) ──────────────────────────

    def test_no_false_breakeven_on_entry_bar(self):
        """ONDS scenario: bar high triggers 0.75R trail to entry, but
        bar low IS the entry price (the candle where we bought).
        Should NOT trigger breakeven in the same tick — the price
        never actually came back down.  entry=8.38, stop=8.17, R=0.21,
        0.75R trigger=$8.54."""
        trailed_to = []
        self.tracker._trail_stop_to_level = lambda t, lvl: trailed_to.append(lvl)

        trade = _make_trade(entry=8.38, stop=8.17, tp1=8.88, tp2=9.09)
        # Bar high >= $8.54 (triggers trail), bar low = $8.38 (entry bar)
        # Snapshot price = $8.60 (above entry, no stop hit on live price)
        result = self.tracker._evaluate(trade, 8.60, session_high=8.65, session_low=8.38)
        assert 8.38 in trailed_to  # trail to breakeven fired
        assert result is None  # should NOT be "breakeven"

    def test_real_breakeven_when_snapshot_at_entry(self):
        """If the live snapshot price IS at entry after trail, that IS
        a real breakeven — the current price confirms the stop hit."""
        trailed_to = []
        self.tracker._trail_stop_to_level = lambda t, lvl: trailed_to.append(lvl)

        trade = _make_trade(entry=8.38, stop=8.17, tp1=8.88, tp2=9.09)
        # Bar high triggers trail, AND live price is at/below entry
        result = self.tracker._evaluate(trade, 8.38, session_high=8.65, session_low=8.30)
        assert 8.38 in trailed_to
        assert result == "breakeven"  # live price confirms stop hit

    def test_no_false_lock1r_stop_on_same_tick(self):
        """1.5R trail fires and moves stop to entry+R, but bar low
        includes the entry candle.  Should not trigger stop."""
        trailed_to = []
        self.tracker._trail_stop_to_level = lambda t, lvl: trailed_to.append(lvl)

        # entry=10, stop=9.00, R=1.00, 1.5R=$11.50, lock at $11.00
        # TP1=$12.00, TP2=$13.00 — set high enough that 1.5R lock doesn't hit TP
        trade = _make_trade(entry=10.0, stop=9.00, tp1=12.00, tp2=13.0)
        # Bar high=11.60 triggers 1.5R lock (>=11.50), bar low near entry, snapshot above lock
        result = self.tracker._evaluate(trade, 11.20, session_high=11.60, session_low=9.90)
        assert 11.0 in trailed_to  # lock at entry+R
        assert result is None  # should NOT trigger stop from bar low


class TestExpireWithBarCheck:
    """expire_open_trades should detect TP hits via bars before expiring."""

    def test_expire_detects_tp_via_bars(self):
        """ATPC scenario: snapshot below entry but bars show TP2 hit.
        expire_open_trades should record tp2_hit, not expired."""
        tracker = TradeTracker()
        tracker._trail_stop_to_level = lambda t, lvl: None

        trades = [_make_trade(
            symbol="ATPC", entry=2.71, stop=2.55,
            tp1=2.82, tp2=2.86, alerted_at="2026-03-27T14:00:00+00:00",
        )]
        # _fetch_quotes → snapshot below entry
        tracker._fetch_quotes = lambda syms: {"ATPC": 2.60}
        # _fetch_session_bars → high shows TP2 was hit
        tracker._fetch_session_bars = lambda t: {"ATPC": {"high": 2.88, "low": 2.54}}

        recorded: list[dict] = []

        def fake_load():
            return trades

        def fake_update(**kwargs):
            recorded.append(kwargs)

        def patched_expire():
            from tradingbot.web import alert_store as _as
            _orig_load = _as.load_open_outcomes
            _orig_update = _as.update_outcome
            _as.load_open_outcomes = fake_load
            _as.update_outcome = fake_update
            try:
                return tracker.expire_open_trades()
            finally:
                _as.load_open_outcomes = _orig_load
                _as.update_outcome = _orig_update

        count = patched_expire()

        assert count == 1
        assert len(recorded) == 1
        assert recorded[0]["status"] == "tp2_hit"
        assert recorded[0]["exit_price"] == 2.86  # TP2 price, not snapshot
        assert recorded[0]["pnl_pct"] > 0  # should be a win

    def test_expire_no_tp_expires_normally(self):
        """When bars don't show a TP hit, expire normally."""
        tracker = TradeTracker()
        tracker._trail_stop_to_level = lambda t, lvl: None

        trades = [_make_trade(
            symbol="FAIL", entry=10.0, stop=9.50,
            tp1=10.50, tp2=11.0, alerted_at="2026-03-27T14:00:00+00:00",
        )]
        tracker._fetch_quotes = lambda syms: {"FAIL": 9.80}
        tracker._fetch_session_bars = lambda t: {"FAIL": {"high": 10.20, "low": 9.55}}

        recorded: list[dict] = []

        def fake_load():
            return trades

        def fake_update(**kwargs):
            recorded.append(kwargs)

        def patched_expire():
            from tradingbot.web import alert_store as _as
            _orig_load = _as.load_open_outcomes
            _orig_update = _as.update_outcome
            _as.load_open_outcomes = fake_load
            _as.update_outcome = fake_update
            try:
                return tracker.expire_open_trades()
            finally:
                _as.load_open_outcomes = _orig_load
                _as.update_outcome = _orig_update

        count = patched_expire()

        assert count == 1
        assert len(recorded) == 1
        assert recorded[0]["status"] == "expired"
        assert recorded[0]["exit_price"] == 9.80
        assert recorded[0]["pnl_pct"] < 0  # loss

    def test_expire_tp_exit_uses_tp_price_not_snapshot(self):
        """APLZ scenario: stock ran 30% (snapshot=$31), but TP2=$25.73.
        Exit price must be TP2, not the inflated snapshot."""
        tracker = TradeTracker()
        tracker._trail_stop_to_level = lambda t, lvl: None

        trades = [_make_trade(
            symbol="APLZ", entry=24.38, stop=23.00,
            tp1=25.36, tp2=25.73, alerted_at="2026-03-30T14:00:00+00:00",
        )]
        # Snapshot is way above TP2 — stock kept running
        tracker._fetch_quotes = lambda syms: {"APLZ": 31.07}
        tracker._fetch_session_bars = lambda t: {"APLZ": {"high": 32.0, "low": 24.00}}

        recorded: list[dict] = []

        def fake_load():
            return trades

        def fake_update(**kwargs):
            recorded.append(kwargs)

        def patched_expire():
            from tradingbot.web import alert_store as _as
            _orig_load = _as.load_open_outcomes
            _orig_update = _as.update_outcome
            _as.load_open_outcomes = fake_load
            _as.update_outcome = fake_update
            try:
                return tracker.expire_open_trades()
            finally:
                _as.load_open_outcomes = _orig_load
                _as.update_outcome = _orig_update

        count = patched_expire()

        assert count == 1
        assert len(recorded) == 1
        assert recorded[0]["status"] == "tp2_hit"
        assert recorded[0]["exit_price"] == 25.73  # TP2, not $31.07 snapshot
        # Blended PnL: half@TP1(25.36) + half@TP2(25.73)
        tp1_pnl = ((25.36 - 24.38) / 24.38) * 100
        tp2_pnl = ((25.73 - 24.38) / 24.38) * 100
        expected_pnl = round((tp1_pnl + tp2_pnl) / 2, 2)
        assert recorded[0]["pnl_pct"] == expected_pnl  # ~4.78%, blended


class TestResolveExitPrice:
    """_resolve_exit_price should return the limit/stop level, not snapshot."""

    def test_tp2_uses_tp2_price(self):
        trade = _make_trade(entry=10.0, stop=9.50, tp1=10.50, tp2=11.0)
        assert TradeTracker._resolve_exit_price(trade, "tp2_hit", 12.50) == 11.0

    def test_tp1_uses_tp1_price(self):
        trade = _make_trade(entry=10.0, stop=9.50, tp1=10.50, tp2=11.0)
        assert TradeTracker._resolve_exit_price(trade, "tp1_hit", 10.80) == 10.50

    def test_stopped_uses_stop_price(self):
        trade = _make_trade(entry=10.0, stop=9.50, tp1=10.50, tp2=11.0)
        assert TradeTracker._resolve_exit_price(trade, "stopped", 9.30) == 9.50

    def test_breakeven_uses_stop_at_entry(self):
        """NOW scenario: breakeven means stop was trailed to entry.
        Exit should be stop_price (=entry), not inflated snapshot."""
        trade = _make_trade(entry=101.80, stop=101.80, tp1=103.0, tp2=104.0)
        # Snapshot is $105.06 but we exited at the breakeven stop
        assert TradeTracker._resolve_exit_price(trade, "breakeven", 105.06) == 101.80

    def test_trailed_out_uses_stop_price(self):
        trade = _make_trade(entry=10.0, stop=10.30, tp1=10.50, tp2=11.0)
        assert TradeTracker._resolve_exit_price(trade, "trailed_out", 10.15) == 10.30

    def test_tp1_locked_uses_stop_at_tp1(self):
        trade = _make_trade(entry=10.0, stop=10.50, tp1=10.50, tp2=11.0, status="tp1_hit")
        assert TradeTracker._resolve_exit_price(trade, "tp1_locked", 10.40) == 10.50

    def test_fallback_to_snapshot(self):
        """Unknown status or missing prices → use snapshot."""
        trade = _make_trade(entry=10.0, stop=0, tp1=0, tp2=0)
        assert TradeTracker._resolve_exit_price(trade, "stopped", 9.80) == 9.80


class TestBlendedPnL:
    """_calc_pnl should blend half@TP1 + half@exit when TP1 was taken."""

    def test_tp2_hit_blends_tp1_and_tp2(self):
        """Entry=10, TP1=10.50 (+5%), TP2=11.00 (+10%) → blended = 7.5%."""
        trade = _make_trade(entry=10.0, stop=9.50, tp1=10.50, tp2=11.0)
        # exit_price = TP2 = 11.0
        pnl = TradeTracker._calc_pnl(trade, 11.0, "tp2_hit")
        assert pnl == 7.5  # (5% + 10%) / 2

    def test_tp1_hit_no_blend(self):
        """TP1 hit: only first half sold, runner still open → no blend."""
        trade = _make_trade(entry=10.0, stop=9.50, tp1=10.50, tp2=11.0)
        pnl = TradeTracker._calc_pnl(trade, 10.50, "tp1_hit")
        assert pnl == 5.0  # just (10.50 - 10) / 10

    def test_tp1_locked_blends(self):
        """TP1 locked: half@TP1 + half@TP1 → same as TP1 PnL."""
        trade = _make_trade(entry=10.0, stop=10.50, tp1=10.50, tp2=11.0, status="tp1_hit")
        pnl = TradeTracker._calc_pnl(trade, 10.50, "tp1_locked")
        assert pnl == 5.0  # (5% + 5%) / 2 = 5%

    def test_stopped_after_tp1_blends(self):
        """Stopped from tp1_hit: half@TP1(+5%), half@stop(-5%) → 0%."""
        trade = _make_trade(entry=10.0, stop=9.50, tp1=10.50, tp2=11.0, status="tp1_hit")
        pnl = TradeTracker._calc_pnl(trade, 9.50, "stopped")
        assert pnl == 0.0  # (5% + -5%) / 2

    def test_stopped_from_open_no_blend(self):
        """Stopped from open (TP1 never hit): full position at stop."""
        trade = _make_trade(entry=10.0, stop=9.50, tp1=10.50, tp2=11.0, status="open")
        pnl = TradeTracker._calc_pnl(trade, 9.50, "stopped")
        assert pnl == -5.0  # no blend

    def test_expired_after_tp1_blends(self):
        """Expired from tp1_hit: half@TP1, half@market."""
        trade = _make_trade(entry=10.0, stop=9.50, tp1=10.50, tp2=11.0, status="tp1_hit")
        # Market at $10.20 at close
        pnl = TradeTracker._calc_pnl(trade, 10.20, "expired")
        # TP1 half: +5%, market half: +2% → blended = 3.5%
        assert pnl == 3.5

    def test_breakeven_after_tp1_blends(self):
        """Breakeven from tp1_hit: half@TP1(+5%), half@entry(0%) → 2.5%."""
        trade = _make_trade(entry=10.0, stop=10.0, tp1=10.50, tp2=11.0, status="tp1_hit")
        pnl = TradeTracker._calc_pnl(trade, 10.0, "breakeven")
        assert pnl == 2.5  # (5% + 0%) / 2

    def test_aplz_scenario_blended(self):
        """APLZ: entry=24.38, TP1=25.36, TP2=25.73.
        TP1 half: (25.36-24.38)/24.38 = 4.02%
        TP2 half: (25.73-24.38)/24.38 = 5.54%
        Blended: (4.02+5.54)/2 = 4.78%."""
        trade = _make_trade(entry=24.38, stop=23.00, tp1=25.36, tp2=25.73)
        pnl = TradeTracker._calc_pnl(trade, 25.73, "tp2_hit")
        expected = round(((((25.36-24.38)/24.38) + ((25.73-24.38)/24.38)) / 2) * 100, 2)
        assert pnl == expected
