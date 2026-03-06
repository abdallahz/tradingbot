from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


class ConfigLoader:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _load_yaml(self, file_name: str) -> dict[str, Any]:
        path = self.root / "config" / file_name
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        return data

    def scanner(self) -> dict[str, Any]:
        return self._load_yaml("scanner.yaml")

    def risk(self) -> dict[str, Any]:
        return self._load_yaml("risk.yaml")

    def indicators(self) -> dict[str, Any]:
        return self._load_yaml("indicators.yaml")

    def schedule(self) -> dict[str, Any]:
        return self._load_yaml("schedule.yaml")

    def broker(self) -> dict[str, Any]:
        config = self._load_yaml("broker.yaml")
        return self._apply_broker_env_overrides(config)

    def _apply_broker_env_overrides(self, config: dict[str, Any]) -> dict[str, Any]:
        alpaca = config.setdefault("alpaca", {})
        news = config.setdefault("news", {})

        self._set_if_present(alpaca, "api_key", "ALPACA_API_KEY")
        self._set_if_present(alpaca, "api_secret", "ALPACA_API_SECRET")
        self._set_if_present(alpaca, "paper", "ALPACA_PAPER", caster=self._to_bool)

        self._set_if_present(news, "sec_filings", "NEWS_SEC_FILINGS", caster=self._to_bool)
        self._set_if_present(news, "rss_feeds", "NEWS_RSS_FEEDS", caster=self._to_bool)
        self._set_if_present(news, "earnings_calendar", "NEWS_EARNINGS_CALENDAR", caster=self._to_bool)
        self._set_if_present(news, "press_releases", "NEWS_PRESS_RELEASES", caster=self._to_bool)
        self._set_if_present(news, "twitter_enabled", "NEWS_TWITTER_ENABLED", caster=self._to_bool)
        self._set_if_present(news, "reddit_enabled", "NEWS_REDDIT_ENABLED", caster=self._to_bool)
        self._set_if_present(news, "social_proxy_enabled", "NEWS_SOCIAL_PROXY_ENABLED", caster=self._to_bool)
        self._set_if_present(news, "twitter_bearer_token", "TWITTER_BEARER_TOKEN")
        self._set_if_present(news, "sec_user_agent", "SEC_USER_AGENT")
        self._set_if_present(news, "max_age_hours", "NEWS_MAX_AGE_HOURS", caster=int)

        return config

    def _set_if_present(
        self,
        section: dict[str, Any],
        key: str,
        env_name: str,
        caster: Any | None = None,
    ) -> None:
        raw = os.getenv(env_name)
        if raw is None:
            return

        value: Any = raw
        if caster is not None:
            value = caster(raw)
        section[key] = value

    def _to_bool(self, raw: str) -> bool:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
