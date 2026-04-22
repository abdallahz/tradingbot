"""NightResearchSelector — builds Option-1 night-research picks.

Extracted from SessionRunner._get_night_research_picks so it can be
instantiated and tested independently of the full session machinery.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from tradingbot.models import NightResearchResult, SymbolSnapshot

log = logging.getLogger(__name__)


class NightResearchSelector:
    """Select and enrich the top catalyst picks for the night-research watchlist."""

    def __init__(
        self,
        min_catalyst_score: int = 40,
        output_dir: Path | None = None,
    ) -> None:
        self.min_catalyst_score = min_catalyst_score
        self.output_dir = output_dir

    def build_picks(
        self,
        snapshots: list[SymbolSnapshot],
        catalyst_scores: dict[str, float],
        session_tag: str,
    ) -> list[NightResearchResult]:
        """Return top catalyst picks enriched with optional smart money signals."""
        top_catalysts = sorted(catalyst_scores.items(), key=lambda x: x[1], reverse=True)[:10]
        snap_by_symbol = {s.symbol: s for s in snapshots}

        smart_money_signals: dict = {}
        try:
            from tradingbot.research.insider_tracking import SmartMoneyTracker
            tracker = SmartMoneyTracker()
            top_symbols = [sym for sym, score in top_catalysts if score >= self.min_catalyst_score]
            if top_symbols:
                smart_money_signals = tracker.get_smart_money_signals(top_symbols, days_lookback=7)
                if self.output_dir:
                    self._save_smart_money_signals(smart_money_signals, session_tag)
        except Exception as exc:
            log.warning(f"Could not fetch smart money signals: {exc}")

        picks: list[NightResearchResult] = []
        for symbol, score in top_catalysts:
            if score < self.min_catalyst_score:
                continue
            snap = snap_by_symbol.get(symbol)
            if snap is None:
                continue

            reasons: list[str] = []
            if snap.gap_pct and abs(snap.gap_pct) >= 2.0:
                reasons.append(f"Gap: {snap.gap_pct:+.1f}%")
            if snap.relative_volume >= 1.5:
                reasons.append(f"RelVol: {snap.relative_volume:.1f}x")

            sm_score, insider_signal, institutional_signal = 50.0, "", ""
            if symbol in smart_money_signals:
                sm = smart_money_signals[symbol]
                sm_score = sm.get("smart_money_score", 50.0)

                trades = sm.get("insider_trades", [])
                if trades:
                    buys = sum(1 for t in trades if "Purchase" in t.transaction_type)
                    sells = sum(1 for t in trades if "Sale" in t.transaction_type)
                    insider_signal = "buying" if buys > sells else "selling" if sells > buys else "neutral"

                positions = sm.get("institutional_positions", [])
                if positions:
                    inc = sum(1 for p in positions if p.change_from_prior_quarter and p.change_from_prior_quarter > 0)
                    dec = sum(1 for p in positions if p.change_from_prior_quarter and p.change_from_prior_quarter < 0)
                    institutional_signal = "accumulating" if inc > dec else "reducing" if dec > inc else "neutral"

            picks.append(NightResearchResult(
                symbol=symbol,
                catalyst_score=score,
                reasons=reasons or ["High catalyst score"],
                smart_money_score=sm_score,
                insider_signal=insider_signal,
                institutional_signal=institutional_signal,
            ))

        return picks

    def _save_smart_money_signals(self, signals: dict, session_tag: str) -> None:
        """Persist smart money signals to JSON for historical reference."""
        assert self.output_dir is not None
        self.output_dir.mkdir(parents=True, exist_ok=True)

        serializable: dict = {}
        for symbol, data in signals.items():
            entry: dict = {
                "symbol": symbol,
                "smart_money_score": data.get("smart_money_score", 50.0),
                "insider_trades": [],
                "institutional_positions": [],
                "congressional_trades": [],
            }
            for t in data.get("insider_trades", []):
                entry["insider_trades"].append({
                    "symbol": t.symbol,
                    "insider_name": t.insider_name,
                    "insider_title": t.insider_title,
                    "transaction_date": t.transaction_date.isoformat() if t.transaction_date else None,
                    "transaction_type": t.transaction_type,
                    "shares": t.shares,
                    "price_per_share": t.price_per_share,
                    "total_value": t.total_value,
                    "is_significant": t.is_significant,
                })
            for p in data.get("institutional_positions", []):
                entry["institutional_positions"].append({
                    "symbol": p.symbol,
                    "institution_name": p.institution_name,
                    "shares_held": p.shares_held,
                    "market_value": p.market_value,
                    "percent_of_portfolio": p.percent_of_portfolio,
                    "filing_date": p.filing_date.isoformat() if p.filing_date else None,
                    "change_from_prior_quarter": p.change_from_prior_quarter,
                    "percent_change": p.percent_change,
                })
            for t in data.get("congressional_trades", []):
                entry["congressional_trades"].append({
                    "symbol": t.symbol,
                    "politician_name": t.politician_name,
                    "position": t.position,
                    "party": t.party,
                    "transaction_date": t.transaction_date.isoformat() if t.transaction_date else None,
                    "transaction_type": t.transaction_type,
                    "amount_range": t.amount_range,
                    "estimated_value": t.estimated_value,
                })
            serializable[symbol] = entry

        path = self.output_dir / f"smart_money_signals_{session_tag}.json"
        with path.open("w", encoding="utf-8") as f:
            json.dump({
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "session_tag": session_tag,
                "signals": serializable,
            }, f, indent=2)
        log.info(f"Saved smart money signals to {path}")
