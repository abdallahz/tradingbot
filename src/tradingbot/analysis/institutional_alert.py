"""
institutional_alert.py — Transform basic Telegram alerts into institutional
decision-dashboard pings.

Adds to every alert:
  1. Float / Short Interest / Relative Volume contextual data
  2. Letter grade (A/B/C) based on confluence score
  3. Auto-calculated Profit Target + Stop Loss from 5-min candle structure
  4. Risk/Reward ratio with dollar-risk per share
  5. Volume classification + false-positive warnings
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from tradingbot.models import TradeCard, SymbolSnapshot

logger = logging.getLogger(__name__)


# ── FMP float-data cache ───────────────────────────────────────────────
# In-memory TTL cache to avoid burning API quota on repeated look-ups
# within the same session.  Format: {symbol: (timestamp, data_dict)}
_float_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 3600  # 1 hour — float doesn't change intra-day


@dataclass
class InstitutionalContext:
    """Enriched context data for institutional-grade alerts."""
    # Float & short data (populated from external source or estimated)
    float_shares: float = 0.0          # millions
    short_interest_pct: float = 0.0    # percent of float
    shares_short: float = 0.0          # millions
    days_to_cover: float = 0.0

    # Volume intelligence
    relative_volume: float = 0.0
    volume_classification: str = ""    # accumulation/distribution/climax/thin_fade
    volume_quality_score: float = 0.0

    # Confluence grading
    confluence_grade: str = "C"
    confluence_score: float = 0.0
    confluence_summary: str = ""

    # Exit planning
    atr_stop: float = 0.0
    structure_stop: float = 0.0
    recommended_stop: float = 0.0
    tp1_conservative: float = 0.0
    tp2_aggressive: float = 0.0
    risk_per_share: float = 0.0
    reward_per_share: float = 0.0
    risk_reward_ratio: float = 0.0

    # False-positive warnings
    warnings: list[str] = field(default_factory=list)


def _fetch_fmp_float(symbol: str) -> dict | None:
    """Call Financial Modeling Prep and return raw float data, or None on failure.

    Requires env var ``FMP_API_KEY``.  Free tier allows 250 requests/day
    which is plenty for a gap scanner processing ~30 symbols per session.

    Endpoints used (one call each, both included in free tier):
      /api/v3/key-metrics/{symbol}?limit=1
      /api/v3/profile/{symbol}
    """
    api_key = os.getenv("FMP_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        import requests

        base = "https://financialmodelingprep.com/api/v3"
        headers = {"Accept": "application/json"}
        timeout = 6

        # 1. Company profile — floatShares, mktCap, sharesOutstanding
        profile_url = f"{base}/profile/{symbol}?apikey={api_key}"
        resp = requests.get(profile_url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        profile_list = resp.json()
        if not profile_list:
            logger.info(f"[fmp] No profile data for {symbol}")
            return None

        profile = profile_list[0] if isinstance(profile_list, list) else profile_list

        float_shares = float(profile.get("floatShares") or 0)
        shares_outstanding = float(profile.get("sharesOutstanding") or 0)
        mkt_cap = float(profile.get("mktCap") or 0)

        # 2. Key metrics — short interest not in free tier, but
        #    we can grab additional confirmation of float
        #    (skip if profile already has float data to save quota)
        if float_shares == 0 and shares_outstanding > 0:
            float_shares = shares_outstanding * 0.70  # fallback estimate

        return {
            "float_shares": float_shares,
            "shares_outstanding": shares_outstanding,
            "mkt_cap": mkt_cap,
            "source": "fmp",
        }

    except Exception as exc:
        logger.warning(f"[fmp] Float fetch failed for {symbol}: {exc}")
        return None


def estimate_float_data(
    symbol: str,
    market_cap: float = 0.0,
    current_price: float = 0.0,
    avg_volume: float = 0.0,
) -> dict:
    """Get float/short data from FMP API, with in-memory cache + fallback.

    Priority order:
      1. In-memory cache (1-hour TTL)
      2. FMP API call (if FMP_API_KEY env var is set)
      3. Local estimation from market_cap / avg_volume

    Returns dict with keys:
      float_shares_m, short_interest_pct, days_to_cover, data_source
    """
    now = time.time()

    # ── Check cache ────────────────────────────────────────────────
    cached = _float_cache.get(symbol)
    if cached and (now - cached[0]) < _CACHE_TTL:
        return cached[1]

    # ── Try FMP API ────────────────────────────────────────────────
    fmp_data = _fetch_fmp_float(symbol)
    if fmp_data and fmp_data["float_shares"] > 0:
        result = {
            "float_shares_m": round(fmp_data["float_shares"] / 1e6, 2),
            "short_interest_pct": 0.0,   # not in FMP free tier
            "days_to_cover": 0.0,
            "data_source": "fmp",
        }
        _float_cache[symbol] = (now, result)
        logger.info(
            f"[float] {symbol}: {result['float_shares_m']}M shares float (FMP)"
        )
        return result

    # ── Fallback: local estimate ───────────────────────────────────
    if current_price > 0 and market_cap > 0:
        total_shares = market_cap / current_price
        float_shares = total_shares * 0.70
    elif current_price > 0 and avg_volume > 0:
        float_shares = avg_volume * 40
    else:
        float_shares = 0.0

    result = {
        "float_shares_m": round(float_shares / 1e6, 2) if float_shares else 0.0,
        "short_interest_pct": 0.0,
        "days_to_cover": 0.0,
        "data_source": "estimated",
    }
    _float_cache[symbol] = (now, result)
    return result


def compute_exit_levels(
    bars_data: list[Any],
    current_price: float,
    atr: float,
    key_support: float,
    key_resistance: float,
) -> dict:
    """Calculate stop-loss and profit targets from 5-minute candle structure.

    Professional approach:
      1. STOP = below the nearest swing low visible on 5-min chart
                (or ATR-based if no clear swing)
      2. TP1  = nearest resistance or 2R from entry (whichever is closer)
      3. TP2  = next resistance level or 3R extension

    We analyze the last 12 bars (≈ 1 hour of 5-min candles) for swing lows.
    """
    if not bars_data or len(bars_data) < 3:
        # Fallback to ATR-based levels
        stop = round(current_price - atr * 1.0, 2)
        tp1 = round(current_price + atr * 2.0, 2)
        tp2 = round(current_price + atr * 3.0, 2)
        risk = current_price - stop
        return {
            "atr_stop": stop,
            "structure_stop": stop,
            "recommended_stop": stop,
            "tp1_conservative": tp1,
            "tp2_aggressive": tp2,
            "risk_per_share": round(risk, 2),
            "reward_per_share": round(tp1 - current_price, 2),
            "risk_reward_ratio": round((tp1 - current_price) / risk, 2) if risk > 0 else 0,
            "method": "atr_fallback",
        }

    try:
        lows = [float(b.low) for b in bars_data]
        highs = [float(b.high) for b in bars_data]
        closes = [float(b.close) for b in bars_data]
    except AttributeError:
        stop = round(current_price - atr * 1.0, 2)
        tp1 = round(current_price + atr * 2.0, 2)
        tp2 = round(current_price + atr * 3.0, 2)
        risk = current_price - stop
        return {
            "atr_stop": stop,
            "structure_stop": stop,
            "recommended_stop": stop,
            "tp1_conservative": tp1,
            "tp2_aggressive": tp2,
            "risk_per_share": round(risk, 2),
            "reward_per_share": round(tp1 - current_price, 2),
            "risk_reward_ratio": round((tp1 - current_price) / risk, 2) if risk > 0 else 0,
            "method": "atr_fallback",
        }

    # ── Find swing lows in the recent bars (last 12 bars) ──────────
    lookback = min(12, len(lows))
    recent_lows = lows[-lookback:]
    recent_highs = highs[-lookback:]

    # Swing low: a bar whose low is lower than both neighbors
    swing_lows = []
    for i in range(1, len(recent_lows) - 1):
        if recent_lows[i] < recent_lows[i - 1] and recent_lows[i] < recent_lows[i + 1]:
            swing_lows.append(recent_lows[i])

    # Swing highs for resistance targets
    swing_highs = []
    for i in range(1, len(recent_highs) - 1):
        if recent_highs[i] > recent_highs[i - 1] and recent_highs[i] > recent_highs[i + 1]:
            swing_highs.append(recent_highs[i])

    # ── STOP LOSS ──────────────────────────────────────────────────
    # ATR stop: 1 ATR below entry
    atr_stop = round(current_price - atr, 2)

    # Structure stop: below nearest swing low (with small buffer)
    valid_swing_lows = [sl for sl in swing_lows if sl < current_price]
    if valid_swing_lows:
        nearest_swing_low = max(valid_swing_lows)  # highest swing low below price
        structure_stop = round(nearest_swing_low - atr * 0.15, 2)  # small buffer
    else:
        structure_stop = atr_stop

    # Use the TIGHTER stop (less risk) but not tighter than 0.3 ATR
    min_stop_distance = atr * 0.3
    recommended_stop = max(atr_stop, structure_stop)
    if current_price - recommended_stop < min_stop_distance:
        recommended_stop = round(current_price - min_stop_distance, 2)

    # Also respect key_support as a floor for the stop
    support_stop = round(key_support - atr * 0.15, 2) if key_support > 0 else 0
    if support_stop > 0 and support_stop > recommended_stop:
        recommended_stop = support_stop

    risk = current_price - recommended_stop

    # ── PROFIT TARGETS ─────────────────────────────────────────────
    # TP1: nearest swing high above price, or key_resistance, or 2R
    above_highs = sorted([sh for sh in swing_highs if sh > current_price * 1.003])
    if above_highs:
        tp1_structure = above_highs[0]
    elif key_resistance > current_price:
        tp1_structure = key_resistance
    else:
        tp1_structure = current_price + risk * 2

    tp1_rr = current_price + risk * 2  # 2R target
    tp1 = round(min(tp1_structure, tp1_rr), 2)  # Take the nearer of the two

    # TP2: next resistance above TP1, or 3R
    above_tp1 = [sh for sh in above_highs if sh > tp1 * 1.003]
    if above_tp1:
        tp2 = round(above_tp1[0], 2)
    else:
        tp2 = round(current_price + risk * 3, 2)

    rr = round((tp1 - current_price) / risk, 2) if risk > 0 else 0

    return {
        "atr_stop": atr_stop,
        "structure_stop": structure_stop,
        "recommended_stop": recommended_stop,
        "tp1_conservative": tp1,
        "tp2_aggressive": tp2,
        "risk_per_share": round(risk, 2),
        "reward_per_share": round(tp1 - current_price, 2),
        "risk_reward_ratio": rr,
        "method": "swing_structure" if valid_swing_lows else "atr_derived",
    }


def build_institutional_context(
    card: "TradeCard",
    snapshot: "SymbolSnapshot",
    confluence_result: Any | None = None,
    volume_profile: Any | None = None,
    spy_change: float = 0.0,
    qqq_change: float = 0.0,
) -> InstitutionalContext:
    """Build the full institutional context for a trade alert.

    This aggregates all enrichment data into a single object that the
    Telegram formatter can use to build the institutional-grade alert.
    """
    ctx = InstitutionalContext()

    # ── Float/Short data (estimated or from API) ───────────────────
    float_data = estimate_float_data(
        symbol=card.symbol,
        current_price=snapshot.price,
        avg_volume=snapshot.avg_volume_20 * 390 if snapshot.avg_volume_20 > 0 else 0,
    )
    ctx.float_shares = float_data["float_shares_m"]

    # ── Volume intelligence ────────────────────────────────────────
    ctx.relative_volume = snapshot.relative_volume
    if volume_profile:
        ctx.volume_classification = volume_profile.classification
        ctx.volume_quality_score = volume_profile.score

    # ── Confluence grading ─────────────────────────────────────────
    if confluence_result:
        ctx.confluence_grade = confluence_result.grade
        ctx.confluence_score = confluence_result.composite_score
        ctx.confluence_summary = confluence_result.summary
        ctx.warnings = list(confluence_result.false_positive_flags)

    # ── Exit planning ──────────────────────────────────────────────
    exit_levels = compute_exit_levels(
        bars_data=getattr(snapshot, "raw_bars", []),
        current_price=snapshot.price,
        atr=snapshot.atr,
        key_support=snapshot.key_support,
        key_resistance=snapshot.key_resistance,
    )
    ctx.atr_stop = exit_levels["atr_stop"]
    ctx.structure_stop = exit_levels["structure_stop"]
    ctx.recommended_stop = exit_levels["recommended_stop"]
    ctx.tp1_conservative = exit_levels["tp1_conservative"]
    ctx.tp2_aggressive = exit_levels["tp2_aggressive"]
    ctx.risk_per_share = exit_levels["risk_per_share"]
    ctx.reward_per_share = exit_levels["reward_per_share"]
    ctx.risk_reward_ratio = exit_levels["risk_reward_ratio"]

    return ctx


def format_institutional_alert(
    card: "TradeCard",
    ctx: InstitutionalContext,
) -> str:
    """Format an institutional-grade HTML alert for Telegram.

    Compared to the basic alert, this adds:
      - Confluence grade badge (🅰 / 🅱 / 🅲)
      - Volume classification
      - Float / Short Interest
      - Structure-derived exit levels
      - Risk $ per share + R:R
      - False-positive warnings
    """
    from tradingbot.analysis.pattern_detector import format_patterns

    # ── Grade badge ────────────────────────────────────────────────
    grade_badges = {
        "A": "🅰️ GRADE A — HIGH CONVICTION",
        "B": "🅱️ GRADE B — ACCEPTABLE",
        "C": "©️ GRADE C — MARGINAL",
        "F": "❌ GRADE F — AVOID",
    }
    grade_line = grade_badges.get(ctx.confluence_grade, f"Grade {ctx.confluence_grade}")

    # ── Volume classification emoji ────────────────────────────────
    vol_emojis = {
        "accumulation": "🟢 Accumulation",
        "distribution": "🔴 Distribution",
        "climax": "⚡ Climax",
        "thin_fade": "⚠️ Thin Fade",
        "mixed": "🟡 Mixed",
    }
    vol_label = vol_emojis.get(ctx.volume_classification, "—")

    # ── Risk badge ─────────────────────────────────────────────────
    risk_lvl = getattr(card, "risk_level", "low")
    risk_icons = {"low": "✅ Low", "medium": "⚡ Medium", "high": "⚠️ High"}

    # ── AI badge ───────────────────────────────────────────────────
    ai_conf = getattr(card, "ai_confidence", 0)
    if ai_conf >= 7:
        ai_line = f"🤖 <b>AI</b>      : <code>{ai_conf}/10</code> ✅"
    elif ai_conf >= 5:
        ai_line = f"🤖 <b>AI</b>      : <code>{ai_conf}/10</code> ⚠️"
    elif ai_conf > 0:
        ai_line = f"🤖 <b>AI</b>      : <code>{ai_conf}/10</code> ❌"
    else:
        ai_line = ""

    # ── Position size ──────────────────────────────────────────────
    pos_size = getattr(card, "position_size", 0)
    dollarRisk = round(ctx.risk_per_share * pos_size, 2) if pos_size else 0

    patterns = format_patterns(getattr(card, "patterns", []))
    signals = ", ".join(card.reason) if card.reason else "—"

    # ── Source badge (VPS vs Render) ───────────────────────────────
    import os
    _provider = os.getenv("DATA_PROVIDER", "alpaca").lower()
    _src_badge = "🖥 VPS/IBKR" if _provider == "ibkr" else "☁️ Render/Alpaca"

    lines = [
        f"🚨 <b>TRADE ALERT — {card.symbol}</b>  [{_src_badge}]",
        f"<b>{grade_line}</b>",
        f"Score: <code>{card.score:.0f}/100</code>  |  Session: {card.session_tag.upper()}",
        "",
        "━━━━━ 📊 <b>MARKET CONTEXT</b> ━━━━━",
        f"Volume   : <code>{ctx.relative_volume:.1f}x</code> rel — {vol_label}",
        f"Float    : <code>{ctx.float_shares:.1f}M</code> shares",
        f"Catalyst : <code>{getattr(card, 'catalyst_score', 0):.0f}/100</code>",
        "",
        "━━━━━ 🎯 <b>TRADE PLAN</b> ━━━━━",
        f"Entry     : <code>${card.entry_price:.2f}</code>",
        f"Stop Loss : <code>${card.stop_price:.2f}</code>",
        f"TP 1      : <code>${card.tp1_price:.2f}</code>  ({card.risk_reward:.1f}R)",
        f"TP 2      : <code>${card.tp2_price:.2f}</code>  (extended)",
        "",
        f"Risk/Share: <code>${abs(card.entry_price - card.stop_price):.2f}</code>",
        f"R:R Ratio : <code>{card.risk_reward:.1f}:1</code>",
        f"Support   : <code>${getattr(card, 'key_support', 0):.2f}</code>",
        f"Resistance: <code>${getattr(card, 'key_resistance', 0):.2f}</code>",
        "",
        "━━━━━ 📐 <b>POSITION SIZE</b> ━━━━━",
        f"Shares    : <code>{pos_size}</code>",
        f"Total Risk: <code>${dollarRisk:.2f}</code>",
        f"Risk Level: {risk_icons.get(risk_lvl, risk_lvl)}",
        "",
        f"📊 <b>Patterns</b>: {patterns}",
        f"📝 <b>Signals</b> : {signals}",
    ]

    if ai_line:
        lines.append("")
        lines.append(ai_line)
        ai_reasoning = getattr(card, "ai_reasoning", "")
        if ai_reasoning:
            lines.append(f"    💬 {ai_reasoning[:200]}")

    # ── False-positive warnings ────────────────────────────────────
    if ctx.warnings:
        lines.append("")
        lines.append("━━━━━ ⚠️ <b>WARNINGS</b> ━━━━━")
        for w in ctx.warnings[:4]:
            lines.append(f"⚠️ {w}")

    return "\n".join(lines)
