from pathlib import Path

from tradingbot.config import ConfigLoader


def test_broker_env_overrides(monkeypatch):
    root = Path.cwd()

    monkeypatch.setenv("ALPACA_API_KEY", "env_key")
    monkeypatch.setenv("ALPACA_API_SECRET", "env_secret")
    monkeypatch.setenv("ALPACA_PAPER", "false")
    monkeypatch.setenv("NEWS_SOCIAL_PROXY_ENABLED", "true")
    monkeypatch.setenv("NEWS_MAX_AGE_HOURS", "48")

    broker = ConfigLoader(root).broker()

    assert broker["alpaca"]["api_key"] == "env_key"
    assert broker["alpaca"]["api_secret"] == "env_secret"
    assert broker["alpaca"]["paper"] is False
    assert broker["news"]["social_proxy_enabled"] is True
    assert broker["news"]["max_age_hours"] == 48


def test_env_bool_truthy_values(monkeypatch):
    root = Path.cwd()
    monkeypatch.setenv("NEWS_RSS_FEEDS", "YES")

    broker = ConfigLoader(root).broker()
    assert broker["news"]["rss_feeds"] is True

    monkeypatch.delenv("NEWS_RSS_FEEDS", raising=False)
    broker_without_override = ConfigLoader(root).broker()
    assert "rss_feeds" in broker_without_override["news"]
