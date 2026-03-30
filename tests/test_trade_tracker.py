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
