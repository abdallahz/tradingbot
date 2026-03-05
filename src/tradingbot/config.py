from __future__ import annotations

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
        return self._load_yaml("broker.yaml")
