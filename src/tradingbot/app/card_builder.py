"""CardBuilder — encapsulates the pre-card filter chain for _build_cards.

Each filter is a standalone method that takes only what it needs, making
the filter chain testable without a full SessionRunner instance.
"""
from __future__ import annotations

import logging

from tradingbot.models import SymbolSnapshot
from tradingbot.data.etf_metadata import is_etf, get_etf_family
from tradingbot.signals.pullback_reentry import evaluate_pullback_reentry

log = logging.getLogger(__name__)

# ── Defaults (overridden by scanner.yaml at runtime) ──────────────────
_MAX_ETF_ALERTS = 3
_VWAP_DISTANCE_MORNING = 3.0
_VWAP_DISTANCE_MIDDAY = 5.0
_MAX_INTRADAY_CHANGE = 6.0


class CardBuilder:
    """Holds configuration for the filter chain; each filter is a pure method."""

    def __init__(
        self,
        catalyst_bypass_score: int = 70,
        max_etf_alerts: int = _MAX_ETF_ALERTS,
        vwap_distance_morning: float = _VWAP_DISTANCE_MORNING,
        vwap_distance_midday: float = _VWAP_DISTANCE_MIDDAY,
        max_intraday_change: float = _MAX_INTRADAY_CHANGE,
    ) -> None:
        self.catalyst_bypass_score = catalyst_bypass_score
        self.max_etf_alerts = max_etf_alerts
        self.vwap_distance_morning = vwap_distance_morning
        self.vwap_distance_midday = vwap_distance_midday
        self.max_intraday_change = max_intraday_change

    # ── Filters ────────────────────────────────────────────────────────

    def passes_dedup(
        self,
        symbol: SymbolSnapshot,
        already_alerted: dict[str, float],
        dropped: list[tuple[str, str]] | None,
        stopped_data: dict[str, dict] | None = None,
        alert_counts: dict[str, int] | None = None,
    ) -> bool:
        """True if the symbol should (re-)alert; False to skip.

        First-time symbols always pass.  Previously-alerted symbols must
        show a qualifying pullback re-entry (30-70% dip, holding support,
        recovering above EMA9, entry ≥0.5% better than prior alert).

        Two additional guards for re-entries:
        - Re-entry cap: max one re-entry per symbol per day (initial alert
          + one re-entry = 2 total).  Prevents revenge-trading a stopped
          position into further losses.
        - Reclaim check (long-only): price must have recovered back above
          the original stop level.  Below the stop means the breakdown is
          still in progress, not a shakeout.
        """
        prev_entry = already_alerted.get(symbol.symbol)
        if prev_entry is None or prev_entry <= 0:
            return True

        # Re-entry cap: block if alerted 2+ times today
        if (alert_counts or {}).get(symbol.symbol, 0) >= 2:
            if dropped is not None:
                dropped.append((symbol.symbol, "dedup:reentry_cap"))
            log.info(f"[DROP] {symbol.symbol}: re-entry cap — already alerted 2× today")
            return False

        sym_stopped = (stopped_data or {}).get(symbol.symbol, {})
        intraday_hod = sym_stopped.get("hod") or None
        original_stop = sym_stopped.get("stop", 0.0)

        # Reclaim check (long-only): price must be back above the stop level.
        # Still below stop = breakdown ongoing, not a shakeout.
        if original_stop > 0 and symbol.price < original_stop:
            if dropped is not None:
                dropped.append((
                    symbol.symbol,
                    f"dedup:below_stop:{symbol.price:.2f}<{original_stop:.2f}",
                ))
            log.info(
                f"[DROP] {symbol.symbol}: price ${symbol.price:.2f} still below "
                f"original stop ${original_stop:.2f} — breakdown, not shakeout"
            )
            return False

        signal = evaluate_pullback_reentry(
            symbol, prev_entry_price=prev_entry, intraday_hod=intraday_hod
        )
        if signal.qualifies:
            hod_tag = f", hod=${intraday_hod:.2f}" if intraday_hod else ""
            log.info(
                f"[RE-ENTRY] {symbol.symbol}: pullback re-entry qualified "
                f"(score={signal.reentry_score:.0f}, depth={signal.pullback_depth_pct:.0f}%, "
                f"prev_entry=${prev_entry:.2f}, new=${symbol.price:.2f}{hod_tag})"
            )
            return True

        if dropped is not None:
            dropped.append((symbol.symbol, f"dedup:{signal.reason}"))
        return False

    def passes_etf_limits(
        self,
        symbol: SymbolSnapshot,
        etf_count: int,
        selected_families: set[str],
        dropped: list[tuple[str, str]] | None,
    ) -> bool:
        """True if ETF concentration / family limits allow this symbol."""
        if not is_etf(symbol.symbol):
            return True
        if etf_count >= self.max_etf_alerts:
            if dropped is not None:
                dropped.append((symbol.symbol, "etf_concentration_cap"))
            log.info(f"[DROP] {symbol.symbol}: ETF concentration cap reached ({etf_count}/{self.max_etf_alerts})")
            return False
        family = get_etf_family(symbol.symbol)
        if family and family in selected_families:
            if dropped is not None:
                dropped.append((symbol.symbol, f"etf_family_dup:{family}"))
            log.info(f"[DROP] {symbol.symbol}: ETF family '{family}' already selected")
            return False
        return True

    def passes_intraday_extension(
        self,
        symbol: SymbolSnapshot,
        dropped: list[tuple[str, str]] | None,
        tuning_overrides: dict | None = None,
    ) -> bool:
        """True if price has NOT moved too far from today's open.

        Stocks already up >max_intraday_change% have made their primary move.
        Exception: a qualifying pullback re-entry pattern is allowed through.
        """
        if symbol.intraday_change_pct <= 0:
            return True
        max_move = (tuning_overrides or {}).get("max_intraday_change_pct", self.max_intraday_change)
        if symbol.intraday_change_pct > max_move:
            signal = evaluate_pullback_reentry(symbol)
            if signal.qualifies:
                log.info(
                    f"[PULLBACK_PASS] {symbol.symbol}: extended +{symbol.intraday_change_pct:.1f}% "
                    f"but qualifies as pullback re-entry (depth={signal.pullback_depth_pct:.0f}%, "
                    f"score={signal.reentry_score:.0f})"
                )
                return True
            if dropped is not None:
                dropped.append((
                    symbol.symbol,
                    f"intraday_extended:{symbol.intraday_change_pct:.1f}%>{max_move:.0f}%",
                ))
            log.info(
                f"[DROP] {symbol.symbol}: already up {symbol.intraday_change_pct:.1f}% "
                f"from open (max {max_move:.0f}%) — too extended"
            )
            return False
        return True

    def passes_vwap_distance(
        self,
        symbol: SymbolSnapshot,
        dropped: list[tuple[str, str]] | None,
        session_tag: str = "midday",
        market_condition=None,
        tuning_overrides: dict | None = None,
    ) -> bool:
        """True if price is within max VWAP distance (session-adaptive).

        Morning: tighter 3% limit (VWAP barely established).
        Midday/close: 5% because stocks naturally drift from VWAP.
        """
        session_default = (
            self.vwap_distance_morning if session_tag == "morning" else self.vwap_distance_midday
        )
        max_vwap = market_condition.max_vwap_distance_pct if market_condition else session_default
        if session_tag != "morning" and max_vwap < self.vwap_distance_midday:
            max_vwap = self.vwap_distance_midday
        max_vwap = (tuning_overrides or {}).get("max_vwap_distance_pct", max_vwap)
        if symbol.vwap > 0 and symbol.price > 0:
            vwap_dist_pct = abs(symbol.price - symbol.vwap) / symbol.vwap * 100
            if vwap_dist_pct > max_vwap:
                if dropped is not None:
                    dropped.append((symbol.symbol, f"vwap_extended:{vwap_dist_pct:.1f}%"))
                log.info(
                    f"[DROP] {symbol.symbol}: price too far from VWAP "
                    f"({vwap_dist_pct:.1f}% > {max_vwap}%)"
                )
                return False
        return True

    def passes_catalyst_gate(
        self,
        symbol: SymbolSnapshot,
        can_long: bool,
        dropped: list[tuple[str, str]] | None,
        market_condition=None,
        tuning_overrides: dict | None = None,
    ) -> bool:
        """True if catalyst or volume conviction is sufficient."""
        min_catalyst = market_condition.min_catalyst_score if market_condition else 40
        min_catalyst = (tuning_overrides or {}).get("min_catalyst_score", min_catalyst)
        min_rvol = market_condition.min_relative_volume if market_condition else 3.0
        min_rvol = (tuning_overrides or {}).get("min_relative_volume", min_rvol)
        if symbol.catalyst_score < min_catalyst:
            has_strong_volume = (
                symbol.relative_volume >= min_rvol
                and symbol.premarket_volume >= 100_000
            )
            if not (has_strong_volume and can_long):
                if dropped is not None:
                    dropped.append((
                        symbol.symbol,
                        f"low_catalyst_weak_vol:{symbol.catalyst_score:.0f}/rv={symbol.relative_volume:.1f}",
                    ))
                log.info(
                    f"[DROP] {symbol.symbol}: catalyst={symbol.catalyst_score:.0f} < {min_catalyst} "
                    f"and volume not convincing (relvol={symbol.relative_volume:.1f}, "
                    f"pm_vol={symbol.premarket_volume})"
                )
                return False
        return True

    def passes_gap_fade_check(
        self,
        symbol: SymbolSnapshot,
        dropped: list[tuple[str, str]] | None,
        relaxed: bool = False,
    ) -> bool:
        """False if the gap is fading (price below VWAP after a positive gap).

        Skipped in relaxed mode — catalyst-driven entries tolerate more drift.
        """
        if relaxed or symbol.gap_pct <= 0:
            return True
        if symbol.vwap > 0 and symbol.price < symbol.vwap:
            if dropped is not None:
                dropped.append((
                    symbol.symbol,
                    f"gap_fade:price={symbol.price:.2f}<vwap={symbol.vwap:.2f}",
                ))
            log.info(
                f"[DROP] {symbol.symbol}: gap fading — price ${symbol.price:.2f} "
                f"below VWAP ${symbol.vwap:.2f} (gap was +{symbol.gap_pct:.1f}%)"
            )
            return False
        return True

    def passes_trend_filter(
        self,
        symbol: SymbolSnapshot,
        dropped: list[tuple[str, str]] | None,
        relaxed: bool = False,
    ) -> bool:
        """False if the stock is gapping up inside a daily downtrend.

        Bypass: relaxed mode and stocks with strong catalysts (score ≥
        catalyst_bypass_score) — a significant news event can override a
        weak daily trend.
        """
        if relaxed or symbol.daily_ema50 <= 0:
            return True
        if symbol.price >= symbol.daily_ema50:
            return True
        if symbol.catalyst_score >= self.catalyst_bypass_score:
            log.info(
                f"[TREND_BYPASS] {symbol.symbol}: below daily EMA50 "
                f"(${symbol.price:.2f} < ${symbol.daily_ema50:.2f}) but "
                f"catalyst={symbol.catalyst_score:.0f} overrides"
            )
            return True
        if dropped is not None:
            dropped.append((
                symbol.symbol,
                f"daily_downtrend:price={symbol.price:.2f}<ema50={symbol.daily_ema50:.2f}",
            ))
        log.info(
            f"[DROP] {symbol.symbol}: daily downtrend — price ${symbol.price:.2f} "
            f"below daily EMA50 ${symbol.daily_ema50:.2f} (bear rally risk)"
        )
        return False
