from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class AISentimentAnalyzer:
    """
    AI-powered sentiment analysis for news headlines.

    Free providers:
      - "finbert"   : FinBERT (HuggingFace, runs locally, no API key needed)

    Paid providers (future use):
      - "openai"    : GPT-4o-mini (~$5/month)
      - "anthropic" : Claude Haiku (~$4/month)
    """

    def __init__(self, provider: str = "finbert", api_key: str | None = None) -> None:
        self.provider = provider.lower()
        self.enabled = False
        self.client = None

        if self.provider == "finbert":
            self._init_finbert()
        elif self.provider == "openai":
            self.api_key = api_key or os.getenv("OPENAI_API_KEY")
            self.enabled = bool(self.api_key)
            if self.enabled:
                try:
                    import openai
                    self.client = openai.OpenAI(api_key=self.api_key)
                except ImportError:
                    logger.warning("openai package not installed. Run: pip install openai")
                    self.enabled = False
        elif self.provider == "anthropic":
            self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
            self.enabled = bool(self.api_key)
            if self.enabled:
                try:
                    import anthropic
                    self.client = anthropic.Anthropic(api_key=self.api_key)
                except ImportError:
                    logger.warning("anthropic package not installed. Run: pip install anthropic")
                    self.enabled = False
        else:
            logger.warning(f"Unknown provider '{provider}'. Falling back to keyword analysis.")

    def _init_finbert(self) -> None:
        """Load FinBERT model (free, local, no API key needed)."""
        try:
            from transformers import pipeline
            logger.info("Loading FinBERT model (first run downloads ~500MB)...")
            self.client = pipeline(
                "text-classification",
                model="ProsusAI/finbert",
                return_all_scores=True,
            )
            self.enabled = True
            logger.info("FinBERT loaded successfully.")
        except ImportError:
            logger.warning(
                "FinBERT requires 'transformers' and 'torch'. "
                "Run: pip install transformers torch. Falling back to keywords."
            )
        except BaseException as e:
            logger.warning(f"FinBERT failed to load: {type(e).__name__}: {e}. Falling back to keywords.")

    def analyze_headlines_batch(
        self,
        headlines: list[dict[str, Any]]
    ) -> dict[str, dict[str, float | str]]:
        """
        Analyze sentiment for a batch of headlines.

        Args:
            headlines: List of dicts with 'symbol' and 'headline' keys

        Returns:
            Dict mapping symbol -> {sentiment_score: 0-100, reasoning: str, bullish: bool}
        """
        if not self.enabled:
            return self._fallback_keyword_analysis(headlines)

        try:
            if self.provider == "finbert":
                return self._analyze_with_finbert(headlines)
            else:
                return self._analyze_with_llm(headlines)
        except Exception as e:
            logger.error(f"AI sentiment analysis failed: {e}. Falling back to keywords.")
            return self._fallback_keyword_analysis(headlines)

    def _analyze_with_finbert(self, headlines: list[dict[str, Any]]) -> dict[str, dict[str, float | str]]:
        """Use FinBERT (free, local) to analyze sentiment."""
        results: dict[str, dict[str, float | str]] = {}
        # Aggregate by symbol (average across multiple headlines)
        symbol_scores: dict[str, list[float]] = {}

        for h in headlines:
            symbol = h["symbol"]
            headline = h["headline"][:512]  # FinBERT max token limit

            # Returns e.g. [{"label":"positive","score":0.9}, {"label":"negative",...}, ...]
            raw = self.client(headline)[0]
            score_map = {r["label"]: r["score"] for r in raw}

            positive = score_map.get("positive", 0.0)
            negative = score_map.get("negative", 0.0)
            neutral = score_map.get("neutral", 0.0)

            # Convert to 0-100 scale: 50=neutral, 100=max bullish, 0=max bearish
            sentiment_score = 50.0 + (positive - negative) * 50.0

            if symbol not in symbol_scores:
                symbol_scores[symbol] = []
            symbol_scores[symbol].append(sentiment_score)

        for h in headlines:
            symbol = h["symbol"]
            if symbol in symbol_scores and symbol not in results:
                avg_score = sum(symbol_scores[symbol]) / len(symbol_scores[symbol])
                results[symbol] = {
                    "sentiment_score": round(max(0.0, min(100.0, avg_score)), 2),
                    "reasoning": f"FinBERT analysis ({len(symbol_scores[symbol])} headline(s))",
                    "bullish": avg_score >= 60,
                    "ai_analyzed": True,
                }

        return results

    def _analyze_with_llm(self, headlines: list[dict[str, Any]]) -> dict[str, dict[str, float | str]]:
        """Use OpenAI or Anthropic API to analyze sentiment (paid, future use)."""
        batch_size = 10
        results: dict[str, dict[str, float | str]] = {}

        for i in range(0, len(headlines), batch_size):
            batch = headlines[i:i + batch_size]
            batch_text = "\n".join(
                f"{idx}. {h['symbol']}: {h['headline']}"
                for idx, h in enumerate(batch)
            )

            prompt = f"""Analyze the sentiment of these stock news headlines.
For each, respond: <number>|<score 0-100>|<reasoning 10-20 words>

Headlines:
{batch_text}"""

            if self.provider == "openai":
                response = self.client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=500,
                )
                analysis = response.choices[0].message.content
            else:  # anthropic
                response = self.client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=500,
                    messages=[{"role": "user", "content": prompt}],
                )
                analysis = response.content[0].text

            for line in (analysis or "").strip().split("\n"):
                if "|" not in line:
                    continue
                parts = line.split("|", 2)
                if len(parts) != 3:
                    continue
                try:
                    idx = int(parts[0].strip())
                    score = float(parts[1].strip())
                    reasoning = parts[2].strip()
                    if idx < len(batch):
                        symbol = batch[idx]["symbol"]
                        results[symbol] = {
                            "sentiment_score": max(0.0, min(100.0, score)),
                            "reasoning": reasoning,
                            "bullish": score >= 60,
                            "ai_analyzed": True,
                        }
                except (ValueError, IndexError):
                    continue

        # Fill any missing symbols with keyword fallback
        for h in headlines:
            if h["symbol"] not in results:
                fallback = self._fallback_keyword_analysis([h])
                results[h["symbol"]] = fallback.get(h["symbol"], {
                    "sentiment_score": 50.0,
                    "reasoning": "No analysis available",
                    "bullish": False,
                    "ai_analyzed": False,
                })

        return results

    def _fallback_keyword_analysis(self, headlines: list[dict[str, Any]]) -> dict[str, dict[str, float | str]]:
        """Simple keyword-based sentiment analysis fallback."""
        BULLISH = {
            "beat", "bullish", "breakout", "upgrade", "buy", "strong", "surge", "rally",
            "earnings beat", "guidance raise", "acquisition", "partnership",
            "FDA approval", "breakthrough", "record", "buyout", "merger",
        }
        BEARISH = {
            "miss", "bearish", "downgrade", "sell", "weak", "drop", "plunge", "warning",
            "guidance lower", "loss", "lawsuit", "recall", "bankruptcy",
        }

        results: dict[str, dict[str, float | str]] = {}
        for h in headlines:
            text = h["headline"].lower()
            bull = sum(1 for kw in BULLISH if kw in text)
            bear = sum(1 for kw in BEARISH if kw in text)
            total = bull + bear
            score = 50.0 if total == 0 else 50.0 + ((bull - bear) / total) * 50.0
            results[h["symbol"]] = {
                "sentiment_score": max(0.0, min(100.0, score)),
                "reasoning": f"Keywords: {bull} bullish, {bear} bearish",
                "bullish": score >= 60,
                "ai_analyzed": False,
            }
        return results



class AISentimentAnalyzer:
    """
    AI-powered sentiment analysis for news headlines using OpenAI/Anthropic APIs.
    Falls back to keyword-based analysis if API unavailable or disabled.
    """

    def __init__(self, provider: str = "openai", api_key: str | None = None) -> None:
        """
        Args:
            provider: "openai" or "anthropic" (Claude)
            api_key: API key for the provider (optional, reads from env if not provided)
        """
        self.provider = provider.lower()
        self.api_key = api_key or os.getenv(f"{provider.upper()}_API_KEY")
        self.enabled = bool(self.api_key)
        
        if self.enabled:
            if self.provider == "openai":
                try:
                    import openai
                    self.client = openai.OpenAI(api_key=self.api_key)
                except ImportError:
                    logger.warning("openai package not installed. Run: pip install openai")
                    self.enabled = False
            elif self.provider == "anthropic":
                try:
                    import anthropic
                    self.client = anthropic.Anthropic(api_key=self.api_key)
                except ImportError:
                    logger.warning("anthropic package not installed. Run: pip install anthropic")
                    self.enabled = False
            else:
                logger.warning(f"Unknown AI provider: {provider}. Falling back to keyword analysis.")
                self.enabled = False

    def analyze_headlines_batch(
        self, 
        headlines: list[dict[str, Any]]
    ) -> dict[str, dict[str, float | str]]:
        """
        Analyze sentiment for a batch of headlines.
        
        Args:
            headlines: List of dicts with 'symbol' and 'headline' keys
            
        Returns:
            Dict mapping symbol -> {sentiment_score: 0-100, reasoning: str, bullish: bool}
        """
        if not self.enabled:
            return self._fallback_keyword_analysis(headlines)
        
        try:
            return self._analyze_with_ai(headlines)
        except Exception as e:
            logger.error(f"AI sentiment analysis failed: {e}. Falling back to keywords.")
            return self._fallback_keyword_analysis(headlines)

    def _analyze_with_ai(self, headlines: list[dict[str, Any]]) -> dict[str, dict[str, float | str]]:
        """Use AI API to analyze sentiment (batched for cost efficiency)."""
        # Batch headlines into groups of 10 to minimize API calls
        batch_size = 10
        results: dict[str, dict[str, float | str]] = {}
        
        for i in range(0, len(headlines), batch_size):
            batch = headlines[i:i + batch_size]
            batch_text = "\n".join([
                f"{idx}. {h['symbol']}: {h['headline']}"
                for idx, h in enumerate(batch)
            ])
            
            prompt = f"""Analyze the sentiment of these stock news headlines. For each headline, provide:
1. Sentiment score (0-100, where 0=extremely bearish, 50=neutral, 100=extremely bullish)
2. Brief reasoning (10-20 words)

Headlines:
{batch_text}

Respond in this exact format for each headline:
<number>|<score>|<reasoning>

Example:
0|75|Strong earnings beat with guidance raise indicates positive momentum
1|30|Downgrade by analyst due to margin compression concerns"""

            if self.provider == "openai":
                response = self.client.chat.completions.create(
                    model="gpt-4o-mini",  # Cost-effective model
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=500
                )
                analysis = response.choices[0].message.content
            elif self.provider == "anthropic":
                response = self.client.messages.create(
                    model="claude-3-haiku-20240307",  # Cost-effective model
                    max_tokens=500,
                    messages=[{"role": "user", "content": prompt}]
                )
                analysis = response.content[0].text
            else:
                return self._fallback_keyword_analysis(headlines)
            
            # Parse AI response
            for line in analysis.strip().split("\n"):
                if "|" not in line:
                    continue
                parts = line.split("|", 2)
                if len(parts) != 3:
                    continue
                try:
                    idx = int(parts[0].strip())
                    score = float(parts[1].strip())
                    reasoning = parts[2].strip()
                    
                    if idx >= len(batch):
                        continue
                    
                    symbol = batch[idx]["symbol"]
                    results[symbol] = {
                        "sentiment_score": max(0.0, min(100.0, score)),
                        "reasoning": reasoning,
                        "bullish": score >= 60,
                        "ai_analyzed": True
                    }
                except (ValueError, IndexError):
                    continue
        
        # Fill in any missing symbols with fallback
        for h in headlines:
            if h["symbol"] not in results:
                fallback = self._fallback_keyword_analysis([h])
                results[h["symbol"]] = fallback.get(h["symbol"], {
                    "sentiment_score": 50.0,
                    "reasoning": "No AI analysis available",
                    "bullish": False,
                    "ai_analyzed": False
                })
        
        return results

    def _fallback_keyword_analysis(self, headlines: list[dict[str, Any]]) -> dict[str, dict[str, float | str]]:
        """Simple keyword-based sentiment analysis (original method)."""
        BULLISH_KEYWORDS = {
            "beat", "bullish", "breakout", "upgrade", "buy", "strong", "surge", "rally",
            "earnings beat", "guidance raise", "acquisition", "partnership",
            "FDA approval", "breakthrough", "record", "buyout", "merger"
        }
        BEARISH_KEYWORDS = {
            "miss", "bearish", "downgrade", "sell", "weak", "drop", "plunge", "warning",
            "guidance lower", "loss", "lawsuit", "recall", "bankruptcy"
        }
        
        results: dict[str, dict[str, float | str]] = {}
        
        for h in headlines:
            headline_lower = h["headline"].lower()
            bullish_count = sum(1 for kw in BULLISH_KEYWORDS if kw in headline_lower)
            bearish_count = sum(1 for kw in BEARISH_KEYWORDS if kw in headline_lower)
            
            # Score based on keyword balance
            total = bullish_count + bearish_count
            if total == 0:
                score = 50.0
                reasoning = "Neutral - no sentiment keywords detected"
            else:
                sentiment_delta = (bullish_count - bearish_count) / total
                score = 50.0 + (sentiment_delta * 50.0)
                reasoning = f"Keyword analysis: {bullish_count} bullish, {bearish_count} bearish"
            
            results[h["symbol"]] = {
                "sentiment_score": max(0.0, min(100.0, score)),
                "reasoning": reasoning,
                "bullish": score >= 60,
                "ai_analyzed": False
            }
        
        return results
