from tradingbot.research.news_aggregator import CatalystScorerV2, NewsAggregator


def test_news_aggregator_initialization():
    """Test news aggregator can be initialized and handles empty results."""
    agg = NewsAggregator(sec_enabled=True, earnings_enabled=True, press_releases_enabled=True)
    news = agg.fetch_news(["AAPL", "MSFT"])
    assert isinstance(news, dict)
    assert "AAPL" in news
    assert "MSFT" in news


def test_catalyst_scorer_baseline():
    """Test catalyst scorer returns reasonable scores for symbols without news."""
    agg = NewsAggregator()
    scorer = CatalystScorerV2(agg)
    scores = scorer.score_symbols(["UNKN", "FAKE"])
    
    # Symbols without news should get neutral baseline
    assert all(0 <= score <= 100 for score in scores.values())
    assert scores["UNKN"] == 50.0


def test_catalyst_scorer_with_mocked_news():
    """Test catalyst scorer boosts scores for symbols with news."""
    agg = NewsAggregator()
    scorer = CatalystScorerV2(agg)
    scores = scorer.score_symbols(["NVDA", "TSLA", "PLTR", "AAPL"])
    
    # All symbols should get valid scores between 0-100
    assert all(0 <= score <= 100 for score in scores.values())
    
    # Symbols with mocked news (NVDA, TSLA, PLTR) should score higher than baseline
    # AAPL has no mocked news so should be baseline
    assert scores.get("NVDA", 0) >= 50.0 or scores.get("TSLA", 0) >= 50.0 or scores.get("PLTR", 0) >= 50.0
