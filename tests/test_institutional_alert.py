"""Tests for institutional_alert.py — score alignment between Telegram and dashboard."""
from __future__ import annotations

from tradingbot.analysis.institutional_alert import (
    InstitutionalContext,
    format_institutional_alert,
)
from tradingbot.models import TradeCard


def _make_card(**overrides) -> TradeCard:
    defaults = dict(
        symbol="TEST",
        score=82.5,
        entry_price=10.0,
        stop_price=9.50,
        tp1_price=11.0,
        tp2_price=12.0,
        invalidation_price=9.40,
        session_tag="morning",
        reason=["gap_up"],
        patterns=["bull_flag"],
        catalyst_score=70.0,
        confluence_grade="B",
        confluence_score=55.0,  # deliberately different from card.score
    )
    defaults.update(overrides)
    return TradeCard(**defaults)


class TestScoreAlignment:
    """Telegram institutional alert must display card.score (blended ranker),
    NOT ctx.confluence_score (confluence engine score)."""

    def test_telegram_shows_card_score_not_ctx_confluence(self):
        card = _make_card(score=82.5, confluence_score=55.0)
        ctx = InstitutionalContext(confluence_score=55.0, confluence_grade="B")
        text = format_institutional_alert(card, ctx)

        # Must show the blended ranker score (83, rounded from 82.5)
        assert "83/100" in text or "82/100" in text
        # Must NOT show the confluence engine score
        assert "55/100" not in text

    def test_score_line_format(self):
        card = _make_card(score=91.0)
        ctx = InstitutionalContext(confluence_score=40.0, confluence_grade="A")
        text = format_institutional_alert(card, ctx)

        assert "Score: <code>91/100</code>" in text
