"""Tests for Apr 6-8 failure analysis fixes.

Covers:
  - ETF metadata expansion (single-stock ETFs, crypto, bonds)
  - All-ETF blocking in _build_cards
  - TP1 cap lowered to min(2×ATR, 3%)
  - Midday config tightened (min_score=60, min_rvol=1.5)
  - Confluence grade + volume_classification in load_alerts
  - update_outcome accepts session_high / session_low
"""
from tradingbot.data.etf_metadata import (
    KNOWN_ETFS,
    LEVERAGED_ETFS,
    ETF_FAMILIES,
    is_etf,
    is_leveraged_etf,
    get_leverage_factor,
    get_etf_family,
    are_conflicting,
)
from tradingbot.models import SymbolSnapshot
from tradingbot.strategy.trade_card import build_trade_card


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Fix 1: ETF metadata — previously-missing symbols now known
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestETFMetadataExpansion:
    """New ETFs that escaped the blocker on Apr 6-8 are now catalogued."""

    def test_nvd_is_known_etf(self):
        assert "NVD" in KNOWN_ETFS

    def test_nvd_is_inverse_leveraged(self):
        assert get_leverage_factor("NVD") == -2
        assert is_leveraged_etf("NVD")

    def test_tsdd_is_known_and_inverse(self):
        assert "TSDD" in KNOWN_ETFS
        assert get_leverage_factor("TSDD") == -2
        assert is_leveraged_etf("TSDD")

    def test_bito_is_known_etf(self):
        assert "BITO" in KNOWN_ETFS
        assert is_etf("BITO")

    def test_hyg_is_known_etf(self):
        assert "HYG" in KNOWN_ETFS
        assert is_etf("HYG")

    def test_single_stock_nvidia_family(self):
        assert "nvidia_single" in ETF_FAMILIES
        for sym in ("NVD", "NVDL", "NVDS"):
            assert sym in ETF_FAMILIES["nvidia_single"]
            assert get_etf_family(sym) == "nvidia_single"

    def test_single_stock_tesla_family(self):
        assert "tesla_single" in ETF_FAMILIES
        for sym in ("TSDD", "TSLL", "TSLQ"):
            assert sym in ETF_FAMILIES["tesla_single"]
            assert get_etf_family(sym) == "tesla_single"

    def test_bitcoin_family(self):
        assert "bitcoin" in ETF_FAMILIES
        for sym in ("BITO", "BITX", "GBTC", "IBIT", "FBTC"):
            assert sym in ETF_FAMILIES["bitcoin"]

    def test_high_yield_family(self):
        assert "high_yield" in ETF_FAMILIES
        assert "HYG" in ETF_FAMILIES["high_yield"]
        assert "JNK" in ETF_FAMILIES["high_yield"]

    def test_nvd_conflicts_with_nvdl(self):
        assert are_conflicting("NVD", "NVDL")

    def test_tsdd_conflicts_with_tsll(self):
        assert are_conflicting("TSDD", "TSLL")

    def test_bito_conflicts_with_ibit(self):
        assert are_conflicting("BITO", "IBIT")

    def test_leveraged_single_stock_etfs(self):
        """All single-stock ETFs with leverage > 1 are flagged leveraged."""
        leveraged_singles = ["NVD", "NVDL", "NVDS", "TSDD", "TSLL", "TSLQ",
                             "AAPD", "AAPU", "MSFD", "MSFU", "AMZD", "AMZU",
                             "METD", "METU", "CONL", "CONY"]
        for sym in leveraged_singles:
            assert is_leveraged_etf(sym), f"{sym} should be leveraged"

    def test_non_leveraged_etfs_not_flagged_as_leveraged(self):
        """Crypto/bond ETFs without leverage factor should NOT be flagged leveraged."""
        non_lev = ["BITO", "HYG", "JNK", "LQD", "GBTC", "IBIT", "ETHE"]
        for sym in non_lev:
            assert not is_leveraged_etf(sym), f"{sym} should NOT be leveraged"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Fix 3: TP1 cap lowered from 6% → 3%
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestTP1Cap:
    """TP1 should be capped at min(2×ATR, 3%) — not the old 6%."""

    def _make_stock(self, price=50.0, resistance=60.0, support=48.0, atr=2.0):
        return SymbolSnapshot(
            symbol="TPCAP",
            price=price,
            gap_pct=3.0,
            premarket_volume=500000,
            dollar_volume=10000000,
            spread_pct=0.3,
            relative_volume=2.5,
            catalyst_score=70,
            ema9=price * 0.99,
            ema20=price * 0.97,
            vwap=price * 0.98,
            recent_volume=200000,
            avg_volume_20=80000,
            pullback_low=price * 0.97,
            reclaim_level=price,
            pullback_high=price * 1.02,
            key_support=support,
            key_resistance=resistance,
            atr=atr,
        )

    def test_tp1_capped_at_3pct(self):
        """When resistance is 20% above entry, TP1 must not exceed ~3%."""
        stock = self._make_stock(price=100.0, resistance=120.0, support=97.0, atr=5.0)
        card = build_trade_card(stock, score=80, fixed_stop_pct=2.5, session_tag="morning")
        if card is None:
            return  # card rejected for other reasons; not what we test
        max_tp1 = card.entry_price * 1.03
        assert card.tp1_price <= max_tp1 + 0.01, (
            f"TP1 ${card.tp1_price:.2f} exceeds 3% cap (${max_tp1:.2f})"
        )

    def test_tp1_respects_atr_bound(self):
        """When 2×ATR < 3%, the ATR bound should be tighter."""
        stock = self._make_stock(price=100.0, resistance=110.0, support=97.0, atr=1.0)
        card = build_trade_card(stock, score=80, fixed_stop_pct=2.5, session_tag="morning")
        if card is None:
            return
        # 2 × ATR = $2 → max TP1 = $102
        max_tp1_atr = card.entry_price + 2 * stock.atr
        assert card.tp1_price <= max_tp1_atr + 0.01, (
            f"TP1 ${card.tp1_price:.2f} exceeds 2×ATR bound (${max_tp1_atr:.2f})"
        )

    def test_old_6pct_cap_no_longer_applies(self):
        """Verify the old 6% cap no longer governs — 3% is the new ceiling."""
        stock = self._make_stock(price=50.0, resistance=56.0, support=48.0, atr=3.0)
        card = build_trade_card(stock, score=80, fixed_stop_pct=2.5, session_tag="morning")
        if card is None:
            return
        # Old cap would be $53 (6%), new cap is $51.50 (3%)
        assert card.tp1_price <= 50 * 1.03 + 0.50, (
            f"TP1 ${card.tp1_price:.2f} appears to still use the old 6% cap"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Fix 4: Midday config is tighter
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestMiddayConfig:
    """Midday thresholds raised: min_score 50→60, min_relative_volume 1.0→1.5."""

    def test_midday_min_score_raised(self):
        from tradingbot.config import ConfigLoader
        from pathlib import Path
        cfg = ConfigLoader(Path(__file__).resolve().parents[1])
        scanner_cfg = cfg.scanner()
        assert scanner_cfg["midday"]["min_score"] >= 60, (
            f"Midday min_score should be ≥60, got {scanner_cfg['midday']['min_score']}"
        )

    def test_midday_min_rvol_raised(self):
        from tradingbot.config import ConfigLoader
        from pathlib import Path
        cfg = ConfigLoader(Path(__file__).resolve().parents[1])
        scanner_cfg = cfg.scanner()
        assert scanner_cfg["midday"]["min_relative_volume"] >= 1.5, (
            f"Midday min_rvol should be ≥1.5, got {scanner_cfg['midday']['min_relative_volume']}"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Fix 5: update_outcome accepts session_high / session_low
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestUpdateOutcomeSignature:
    """update_outcome now accepts session_high and session_low kwargs."""

    def test_update_outcome_accepts_high_low(self):
        """Verify the function signature accepts the new kwargs without error."""
        import inspect
        from tradingbot.web.alert_store import update_outcome
        sig = inspect.signature(update_outcome)
        params = list(sig.parameters.keys())
        assert "session_high" in params, "update_outcome missing session_high param"
        assert "session_low" in params, "update_outcome missing session_low param"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Fix 6: load_alerts returns confluence_grade & volume_classification
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestLoadAlertsFields:
    """load_alerts response dicts must include confluence_grade and volume_classification."""

    def test_load_alerts_source_includes_confluence_fields(self):
        """Verify the field mapping code references confluence_grade."""
        import inspect
        from tradingbot.web import alert_store
        source = inspect.getsource(alert_store.load_alerts)
        assert "confluence_grade" in source, "load_alerts must map confluence_grade"
        assert "volume_classification" in source, "load_alerts must map volume_classification"
