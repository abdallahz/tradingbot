from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from tradingbot.models import ThreeOptionWatchlist, TradeCard
from tradingbot.analysis.pattern_detector import format_patterns


def write_csv(path: Path, cards: list[TradeCard]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "symbol",
                "side",
                "score",
                "entry_price",
                "stop_price",
                "tp1_price",
                "tp2_price",
                "invalidation_price",
                "session_tag",
                "reasons",
            ],
        )
        writer.writeheader()
        for card in cards:
            writer.writerow(
                {
                    "symbol": card.symbol,
                    "side": card.side,
                    "score": card.score,
                    "entry_price": card.entry_price,
                    "stop_price": card.stop_price,
                    "tp1_price": card.tp1_price,
                    "tp2_price": card.tp2_price,
                    "invalidation_price": card.invalidation_price,
                    "session_tag": card.session_tag,
                    "reasons": "|".join(card.reason),
                }
            )


def write_markdown(path: Path, morning: list[TradeCard], midday: list[TradeCard]) -> None:
    lines: list[str] = [f"Generated: {datetime.utcnow().isoformat()}Z", "", "## Morning Watchlist"]
    lines.extend(_section_rows(morning))
    lines.append("")
    lines.append("## Midday Watchlist")
    lines.extend(_section_rows(midday))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _section_rows(cards: list[TradeCard]) -> list[str]:
    if not cards:
        return ["- No qualified setups"]
    rows = []
    for card in cards:
        rows.append(
            f"- {card.symbol} ({card.side}) | score={card.score} | entry={card.entry_price} | "
            f"tp1={card.tp1_price} | tp2={card.tp2_price} | stop={card.stop_price} | "
            f"why={','.join(card.reason)}"
        )
        if card.patterns:
            rows.append(f"  patterns: {format_patterns(card.patterns)}")
        if card.chart_path:
            rows.append(f"  ![{card.symbol} Chart]({card.chart_path})")
    return rows


def write_three_option_markdown(path: Path, morning: ThreeOptionWatchlist, midday: ThreeOptionWatchlist) -> None:
    """Write comprehensive 3-option daily playbook with recommendations."""
    lines: list[str] = [
        f"# Daily Trading Playbook - {datetime.utcnow().strftime('%Y-%m-%d')}",
        f"Generated: {datetime.utcnow().isoformat()}Z",
        "",
        "---",
        "",
    ]
    
    # Morning session
    lines.extend(_format_three_option_section("Morning Pre-Market", morning))
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # Midday session
    lines.extend(_format_three_option_section("Midday Re-Scan", midday))
    
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _format_three_option_section(title: str, watchlist: ThreeOptionWatchlist) -> list[str]:
    """Format a single session with 3 trading options."""
    lines = [
        f"## {title}",
        "",
        f"**Market Conditions:** {watchlist.market_volatility.upper()} volatility",
        f"- Average Gap: {watchlist.average_gap:.2f}%",
        f"- Gappers (≥2%): {watchlist.gappers_count}",
        "",
    ]
    
    # Recommendation banner
    rec_emoji = {"night_research": "🔍", "relaxed_filters": "📊", "strict_filters": "✅"}
    rec_name = {
        "night_research": "Option 1: News Research Catalysts",
        "relaxed_filters": "Option 2: Relaxed Filters", 
        "strict_filters": "Option 3: Strict Filters (High Probability)"
    }
    
    lines.extend([
        f"### {rec_emoji[watchlist.recommended_option]} RECOMMENDED: {rec_name[watchlist.recommended_option]}",
        f"**Why:** {watchlist.recommendation_reason}",
        "",
        "---",
        "",
    ])
    
    # Option 1: News Research (night + morning combined)
    lines.extend([
        "### 🔍 Option 1: News Research - Catalyst-Driven Picks",
        "**Strategy:** Focus on stocks with strong news catalysts from overnight + pre-market research.",
        "**Best for:** Low volatility days when patience is key.",
        "",
    ])
    
    if watchlist.night_research_picks:
        for pick in watchlist.night_research_picks:
            # Base info
            main_line = f"- **{pick.symbol}** (catalyst={pick.catalyst_score:.0f}"
            
            # Add smart money score if available
            if pick.smart_money_score != 50.0:
                sentiment = "🟢" if pick.smart_money_score >= 70 else "🟡" if pick.smart_money_score >= 50 else "🔴"
                main_line += f" | smart_money={pick.smart_money_score:.0f}{sentiment}"
            
            # Add signals if available
            signals = []
            if pick.insider_signal:
                signal_emoji = "👥🟢" if pick.insider_signal == "buying" else "👥🔴" if pick.insider_signal == "selling" else "👥⚪"
                signals.append(f"{signal_emoji} {pick.insider_signal}")
            if pick.institutional_signal:
                signal_emoji = "🏦🟢" if pick.institutional_signal == "accumulating" else "🏦🔴" if pick.institutional_signal == "reducing" else "🏦⚪"
                signals.append(f"{signal_emoji} {pick.institutional_signal}")
            
            if signals:
                main_line += f" | {' | '.join(signals)}"
            
            main_line += ")"
            
            # Add reasons
            if pick.reasons:
                reasons_str = " | ".join(pick.reasons)
                main_line += f" | {reasons_str}"
            else:
                main_line += " | Strong catalyst"
            
            lines.append(main_line)
    else:
        lines.append("- No significant catalysts detected")
    
    lines.extend(["", "---", ""])
    
    # Option 2: Relaxed Filters
    lines.extend([
        "### 📊 Option 2: Relaxed Filters - More Opportunities",
        "**Strategy:** Lower thresholds (gap≥1%, vol≥100k) to find more setups.",
        "**Best for:** Testing the full pipeline or medium volatility days.",
        "",
    ])
    
    if watchlist.relaxed_filter_cards:
        for card in watchlist.relaxed_filter_cards:
            lines.append(
                f"- **{card.symbol}** ({card.side.upper()}) | score={card.score:.0f} | "
                f"entry=${card.entry_price:.2f} | tp1=${card.tp1_price:.2f} | tp2=${card.tp2_price:.2f} | "
                f"stop=${card.stop_price:.2f}"
            )
            lines.append(f"  _{', '.join(card.reason)}_")
            if card.patterns:
                lines.append(f"  **Patterns:** {format_patterns(card.patterns)}")
            if card.chart_path:
                lines.append(f"  ![{card.symbol} Chart]({card.chart_path})")
    else:
        lines.append("- No setups even with relaxed filters")
    
    lines.extend(["", "---", ""])
    
    # Option 3: Strict Filters
    lines.extend([
        "### ✅ Option 3: Strict Filters - High Probability Setups",
        "**Strategy:** Conservative approach (gap≥4%, vol≥500k). Only highest-quality setups.",
        "**Best for:** High volatility days, protecting capital on slow days.",
        "",
    ])
    
    if watchlist.strict_filter_cards:
        for card in watchlist.strict_filter_cards:
            lines.append(
                f"- **{card.symbol}** ({card.side.upper()}) | score={card.score:.0f} | "
                f"entry=${card.entry_price:.2f} | tp1=${card.tp1_price:.2f} | tp2=${card.tp2_price:.2f} | "
                f"stop=${card.stop_price:.2f}"
            )
            lines.append(f"  _{', '.join(card.reason)}_")
            if card.patterns:
                lines.append(f"  **Patterns:** {format_patterns(card.patterns)}")
            if card.chart_path:
                lines.append(f"  ![{card.symbol} Chart]({card.chart_path})")
    else:
        lines.append("- No qualified setups with strict filters ✅ (Capital protection mode)")
    
    return lines
