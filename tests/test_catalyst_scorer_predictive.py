from tradingbot.research.news_aggregator import CatalystScorerV2, NewsAggregator
from datetime import datetime, timedelta

class DummyNewsItem:
    def __init__(self, symbol, headline, published_at, relevance_score):
        self.symbol = symbol
        self.headline = headline
        self.source = "test"
        self.published_at = published_at
        self.relevance_score = relevance_score

class DummyNewsAggregator:
    def __init__(self, news_map, max_age_hours=24):
        self.news_map = news_map
        self.max_age_hours = max_age_hours
    def fetch_news(self, symbols):
        return {s: self.news_map.get(s, []) for s in symbols}


def test_catalyst_scorer_negative_predictive_power():
    """Test that high catalyst scores do not correlate with negative news."""
    now = datetime.utcnow()
    # Simulate two symbols: one with positive news, one with negative news
    news_map = {
        "GOOD": [DummyNewsItem("GOOD", "FDA approval for new drug", now, 90)],
        "BAD": [DummyNewsItem("BAD", "SEC investigation, CEO resigns", now, 90)],
    }
    agg = DummyNewsAggregator(news_map)
    scorer = CatalystScorerV2(agg)
    scores = scorer.score_symbols(["GOOD", "BAD"])
    # Both get high scores, but BAD should not be as high as GOOD
    assert scores["GOOD"] > scores["BAD"], f"Expected GOOD > BAD, got {scores}"
    # BAD should not get a score above 60
    assert scores["BAD"] < 70, f"Negative news should not get high score: {scores['BAD']}"
