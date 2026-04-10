"""
Data client abstraction layer.

Provides a Protocol that both AlpacaClient and IBKRClient implement,
plus a factory function that reads broker.yaml to instantiate the
correct one.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from tradingbot.models import SymbolSnapshot

logger = logging.getLogger(__name__)


@runtime_checkable
class DataClient(Protocol):
    """Minimal interface that session_runner expects from a data provider."""

    _CORE_WATCHLIST: list[str]

    def get_tradable_universe(self) -> list[str]: ...

    def get_premarket_snapshots(self, universe: list[str]) -> list[SymbolSnapshot]: ...

    def get_screener_symbols(self) -> list[str]:
        """Return today's dynamically-discovered movers."""
        ...


def create_data_client(broker_config: dict[str, Any]) -> DataClient:
    """Factory: build the right data client based on ``provider`` key.

    ``provider`` is read from broker.yaml (or the ``DATA_PROVIDER``
    env var).  Defaults to ``"alpaca"`` for backward compatibility.

    Returns an **already-connected** client for IBKR, or a ready-to-use
    AlpacaClient (which connects lazily per request).
    """
    import os
    provider = os.getenv("DATA_PROVIDER", broker_config.get("provider", "alpaca")).lower()

    if provider == "ibkr":
        from tradingbot.data.ibkr_client import IBKRClient

        ibkr_cfg = broker_config.get("ibkr", {})
        client = IBKRClient(
            host=ibkr_cfg.get("host", "127.0.0.1"),
            port=int(ibkr_cfg.get("port", 4002)),
            client_id=int(ibkr_cfg.get("client_id", 1)),
            timeout=float(ibkr_cfg.get("timeout", 30)),
            readonly=ibkr_cfg.get("readonly", False),
        )
        client.connect()
        logger.info("Data provider: IBKR (IB Gateway)")
        return client  # type: ignore[return-value]

    # Default: Alpaca
    from tradingbot.data.alpaca_client import AlpacaClient

    alpaca_cfg = broker_config.get("alpaca", {})
    client = AlpacaClient(
        api_key=alpaca_cfg["api_key"],
        api_secret=alpaca_cfg["api_secret"],
        paper=alpaca_cfg.get("paper", True),
        data_feed=alpaca_cfg.get("data_feed", "iex"),
    )
    logger.info("Data provider: Alpaca")
    return client  # type: ignore[return-value]
