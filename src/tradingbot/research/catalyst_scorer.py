from __future__ import annotations

from tradingbot.models import SymbolSnapshot


class CatalystScorer:
    def __init__(self, min_catalyst_score: float = 60.0) -> None:
        self.min_catalyst_score = min_catalyst_score

    def filter(self, snapshots: list[SymbolSnapshot]) -> list[SymbolSnapshot]:
        return [item for item in snapshots if item.catalyst_score >= self.min_catalyst_score]
