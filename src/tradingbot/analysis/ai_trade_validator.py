"""
AI Trade Validator — LLM "second opinion" on every trade card.

Before a trade card becomes a Telegram alert, this module sends the full
setup context to an LLM (OpenAI or Anthropic) and asks it to:
  1. Rate confidence 1–10
  2. Flag specific concerns
  3. Suggest any adjustments

Cards scoring below a configurable threshold (default: 5) are suppressed.

Usage:
    validator = AITradeValidator()  # reads API keys from env
    result = validator.validate(card, snapshot)
    if result.confidence >= 5:
        send_alert(card)

Cost: ~$0.001 per validation with GPT-4o-mini / Claude Haiku.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from tradingbot.models import SymbolSnapshot, TradeCard

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of AI trade validation."""
    confidence: int = 5          # 1-10 scale
    reasoning: str = ""          # LLM's analysis
    concerns: list[str] = field(default_factory=list)
    suggestion: str = ""         # e.g. "tighten stop to $142.50"
    approved: bool = True        # True if confidence >= threshold
    ai_provider: str = ""        # which LLM was used
    error: str = ""              # non-empty if validation failed


# Minimum confidence to let an alert through
DEFAULT_MIN_CONFIDENCE = 5


class AITradeValidator:
    """Send trade setups to an LLM for validation before alerting."""

    def __init__(
        self,
        min_confidence: int = DEFAULT_MIN_CONFIDENCE,
        provider: str | None = None,
    ) -> None:
        """
        Args:
            min_confidence: Minimum LLM confidence (1-10) to approve a card.
            provider: "openai" or "anthropic". Auto-detects from env if None.
        """
        self.min_confidence = min_confidence
        self.enabled = False
        self.provider = ""
        self.client: Any = None

        # Auto-detect provider from available API keys
        if provider:
            self._init_provider(provider)
        else:
            # Try OpenAI first (cheaper), then Anthropic
            if os.getenv("OPENAI_API_KEY"):
                self._init_provider("openai")
            elif os.getenv("ANTHROPIC_API_KEY"):
                self._init_provider("anthropic")

        if self.enabled:
            logger.info(f"[AITradeValidator] Active — provider={self.provider}, min_confidence={self.min_confidence}")
        else:
            logger.info("[AITradeValidator] Disabled — no API key found. Cards pass through unvalidated.")

    def _init_provider(self, provider: str) -> None:
        provider = provider.lower()
        if provider == "openai":
            api_key = os.getenv("OPENAI_API_KEY")
            if api_key:
                try:
                    import openai
                    self.client = openai.OpenAI(api_key=api_key)
                    self.provider = "openai"
                    self.enabled = True
                except ImportError:
                    logger.warning("[AITradeValidator] openai package not installed")
        elif provider == "anthropic":
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if api_key:
                try:
                    import anthropic
                    self.client = anthropic.Anthropic(api_key=api_key)
                    self.provider = "anthropic"
                    self.enabled = True
                except ImportError:
                    logger.warning("[AITradeValidator] anthropic package not installed")

    def validate(
        self,
        card: TradeCard,
        snapshot: SymbolSnapshot,
        catalyst_score: float = 0.0,
        news_headlines: list[str] | None = None,
    ) -> ValidationResult:
        """Validate a trade card using an LLM.

        Returns a ValidationResult with confidence score and reasoning.
        If the LLM is unavailable, returns an auto-approved result.
        """
        if not self.enabled:
            return ValidationResult(
                confidence=self.min_confidence,
                reasoning="AI validation disabled — auto-approved",
                approved=True,
                ai_provider="none",
            )

        try:
            prompt = self._build_prompt(card, snapshot, catalyst_score, news_headlines)
            raw_response = self._call_llm(prompt)
            result = self._parse_response(raw_response)
            result.approved = result.confidence >= self.min_confidence
            result.ai_provider = self.provider
            return result
        except Exception as exc:
            logger.warning(f"[AITradeValidator] Validation failed: {exc}")
            # Fail open — don't block trades if the LLM is down
            return ValidationResult(
                confidence=self.min_confidence,
                reasoning=f"AI validation error — auto-approved: {exc}",
                approved=True,
                ai_provider=self.provider,
                error=str(exc),
            )

    def _build_prompt(
        self,
        card: TradeCard,
        snapshot: SymbolSnapshot,
        catalyst_score: float,
        news_headlines: list[str] | None,
    ) -> str:
        """Build a structured prompt for the LLM."""
        indicators = snapshot.tech_indicators
        rsi = indicators.get("rsi", "N/A")
        macd_hist = indicators.get("macd_hist", "N/A")
        ema9 = indicators.get("ema9", "N/A")
        ema20 = indicators.get("ema20", "N/A")
        bb_upper = indicators.get("bb_upper", "N/A")
        bb_lower = indicators.get("bb_lower", "N/A")
        obv = indicators.get("obv", "N/A")

        headlines_text = ""
        if news_headlines:
            headlines_text = "\n".join(f"  - {h}" for h in news_headlines[:5])
        else:
            headlines_text = "  (none available)"

        return f"""You are a professional day trading analyst. Evaluate this trade setup and provide your assessment.

## Setup
- **Symbol**: {card.symbol}
- **Side**: {card.side.upper()}
- **Composite Score**: {card.score}/100
- **Session**: {card.session_tag}

## Price Levels
- **Current Price**: ${card.entry_price:.2f}
- **Stop Loss**: ${card.stop_price:.2f} (risk: {abs(card.entry_price - card.stop_price) / card.entry_price * 100:.2f}%)
- **Target 1**: ${card.tp1_price:.2f}
- **Target 2**: ${card.tp2_price:.2f}
- **Risk:Reward**: {card.risk_reward:.2f}:1
- **Key Support**: ${card.key_support:.2f}
- **Key Resistance**: ${card.key_resistance:.2f}

## Technical Indicators
- **Gap**: {snapshot.gap_pct:+.2f}%
- **Relative Volume**: {snapshot.relative_volume:.1f}x average
- **RSI(14)**: {rsi}
- **MACD Histogram**: {macd_hist}
- **EMA9**: {ema9} | **EMA20**: {ema20}
- **Bollinger Bands**: Lower={bb_lower} | Upper={bb_upper}
- **OBV**: {obv}

## Patterns Detected
{', '.join(card.patterns) if card.patterns else 'None'}

## Catalyst / News
- **Catalyst Score**: {catalyst_score:.1f}/100
- **Headlines**:
{headlines_text}

## Your Task
Rate this setup on a 1-10 confidence scale where:
  1-3 = Poor setup (conflicting signals, bad R:R, against trend)
  4-5 = Marginal (some positives but notable risks)
  6-7 = Good setup (signals align, reasonable R:R)
  8-10 = Excellent (strong confluence, catalyst-driven, clean levels)

Respond in **exactly** this JSON format:
```json
{{
  "confidence": <1-10>,
  "reasoning": "<2-3 sentence analysis>",
  "concerns": ["<concern 1>", "<concern 2>"],
  "suggestion": "<optional adjustment suggestion or empty string>"
}}
```"""

    def _call_llm(self, prompt: str) -> str:
        """Call the LLM API and return raw text response."""
        if self.provider == "openai":
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a professional day trading analyst. Respond only with the requested JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=300,
            )
            return response.choices[0].message.content or ""
        elif self.provider == "anthropic":
            response = self.client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text or ""
        return ""

    def _parse_response(self, raw: str) -> ValidationResult:
        """Parse the LLM JSON response into a ValidationResult."""
        # Extract JSON from possible markdown code fences
        text = raw.strip()
        if "```json" in text:
            text = text.split("```json", 1)[1]
        if "```" in text:
            text = text.split("```", 1)[0]
        text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"[AITradeValidator] Failed to parse LLM response: {raw[:200]}")
            return ValidationResult(
                confidence=5,
                reasoning=f"Parse error — raw response: {raw[:200]}",
                approved=True,
            )

        return ValidationResult(
            confidence=max(1, min(10, int(data.get("confidence", 5)))),
            reasoning=str(data.get("reasoning", "")),
            concerns=list(data.get("concerns", [])),
            suggestion=str(data.get("suggestion", "")),
        )
