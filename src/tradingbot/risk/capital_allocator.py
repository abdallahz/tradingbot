"""
capital_allocator.py — Position sizing and trade slot management.

Manages:
- Buying power queries from IBKR account
- Max concurrent positions (3 default)
- Morning entry limits (2 default, reserve 1 for midday)
- Risk per trade (0.5% of account default)
- Streak-based size scaling (from RiskManager)
- PDT counter (3 day trades per 5 rolling business days if under $25K)
- Max single position cap (40% of account)

Works in three modes:
- alert_only: no capital checks (current behavior, always passes)
- paper: real checks against paper account
- live: real checks against live account
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass
class PositionSlot:
    """Tracks a single open position."""
    symbol: str
    entry_price: float
    quantity: int
    entry_time: str
    session: Literal["morning", "midday", "close"]
    order_ids: list[int] = field(default_factory=list)  # parent + OCA children


@dataclass
class PDTRecord:
    """Tracks a single day trade for PDT counting."""
    symbol: str
    trade_date: date
    buy_time: str
    sell_time: str


class CapitalAllocator:
    """Manages position sizing, slot allocation, and PDT protection.

    Designed to work with IBKR but mode-independent — in alert_only mode,
    all checks pass so the existing alert system is unaffected.
    """

    def __init__(
        self,
        mode: Literal["alert_only", "paper", "live"] = "alert_only",
        max_concurrent_positions: int = 3,
        max_morning_entries: int = 2,
        reserve_midday_slots: int = 1,
        risk_per_trade_pct: float = 0.5,
        max_single_position_pct: float = 40.0,
        max_notional_per_trade: float = 10_000.0,
        pdt_protection: bool = True,
        pdt_threshold: float = 25_000.0,
    ) -> None:
        self.mode = mode
        self.max_concurrent_positions = max_concurrent_positions
        self.max_morning_entries = max_morning_entries
        self.reserve_midday_slots = reserve_midday_slots
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_single_position_pct = max_single_position_pct
        self.max_notional_per_trade = max_notional_per_trade
        self.pdt_protection = pdt_protection
        self.pdt_threshold = pdt_threshold

        # In-memory state
        self._open_positions: dict[str, PositionSlot] = {}
        self._morning_entries_today: int = 0
        self._day_trades: list[PDTRecord] = []
        self._account_value: float = 0.0
        self._buying_power: float = 0.0
        self._cash_balance: float = 0.0

    # ── Account state ──────────────────────────────────────────────────

    def update_account(
        self,
        net_liquidation: float,
        buying_power: float,
        cash_balance: float,
    ) -> None:
        """Update cached account values from IBKR."""
        self._account_value = net_liquidation
        self._buying_power = buying_power
        self._cash_balance = cash_balance
        logger.debug(
            f"Account updated: NLV=${net_liquidation:,.0f}, "
            f"BP=${buying_power:,.0f}, cash=${cash_balance:,.0f}"
        )

    @property
    def account_value(self) -> float:
        return self._account_value

    @property
    def buying_power(self) -> float:
        return self._buying_power

    @property
    def open_position_count(self) -> int:
        return len(self._open_positions)

    @property
    def open_symbols(self) -> list[str]:
        return list(self._open_positions.keys())

    # ── Slot management ────────────────────────────────────────────────

    def has_slot(self, session: Literal["morning", "midday", "close"] = "morning") -> bool:
        """Check if there's an available position slot.

        Morning: uses max_morning_entries (2), reserves slots for midday.
        Midday/Close: uses all remaining slots up to max_concurrent_positions.
        """
        if self.mode == "alert_only":
            return True

        total_open = len(self._open_positions)
        if total_open >= self.max_concurrent_positions:
            return False

        if session == "morning":
            if self._morning_entries_today >= self.max_morning_entries:
                logger.info(
                    f"Morning entry limit reached ({self._morning_entries_today}/"
                    f"{self.max_morning_entries})"
                )
                return False
            # Reserve slots for midday
            available = self.max_concurrent_positions - total_open
            if available <= self.reserve_midday_slots and self._morning_entries_today > 0:
                logger.info("Reserving remaining slot(s) for midday")
                return False

        return True

    def can_afford(self, entry_price: float, quantity: int) -> bool:
        """Check if the account has enough buying power for the trade."""
        if self.mode == "alert_only":
            return True

        notional = entry_price * quantity
        if notional > self._buying_power:
            logger.info(
                f"Insufficient buying power: need ${notional:,.0f}, "
                f"have ${self._buying_power:,.0f}"
            )
            return False
        return True

    # ── Position sizing ────────────────────────────────────────────────

    def calculate_position_size(
        self,
        entry_price: float,
        stop_price: float,
        streak_multiplier: float = 1.0,
    ) -> int:
        """Calculate number of shares based on risk-per-trade and stop distance.

        Formula: shares = (account × risk% × streak_mult) / (entry - stop)

        Caps:
        - Max 40% of account in a single position
        - Max $10K notional (or 50% of account) per trade
        - Minimum 1 share
        """
        if self.mode == "alert_only":
            return 0  # no execution in alert mode

        if entry_price <= 0 or stop_price <= 0 or entry_price <= stop_price:
            logger.warning(
                f"Invalid prices for sizing: entry={entry_price}, stop={stop_price}"
            )
            return 0

        risk_per_share = entry_price - stop_price
        if risk_per_share <= 0:
            return 0

        # Dollar risk budget
        risk_budget = self._account_value * (self.risk_per_trade_pct / 100) * streak_multiplier
        shares_by_risk = int(risk_budget / risk_per_share)

        # Cap by max single position (40% of account)
        max_notional_by_pct = self._account_value * (self.max_single_position_pct / 100)

        # Cap by absolute max notional ($10K or 50% of account)
        max_notional_abs = min(
            self.max_notional_per_trade,
            self._account_value * 0.5,
        )

        max_notional = min(max_notional_by_pct, max_notional_abs)
        shares_by_notional = int(max_notional / entry_price) if entry_price > 0 else 0

        # Cap by buying power
        shares_by_bp = int(self._buying_power / entry_price) if entry_price > 0 else 0

        shares = min(shares_by_risk, shares_by_notional, shares_by_bp)
        shares = max(shares, 0)  # never negative

        if shares == 0 and self._account_value > 0:
            logger.info(
                f"Position size = 0 shares: risk_budget=${risk_budget:.2f}, "
                f"risk_per_share=${risk_per_share:.2f}"
            )

        return shares

    # ── PDT protection ─────────────────────────────────────────────────

    def pdt_ok(self) -> bool:
        """Check if a new day trade is allowed under PDT rules.

        PDT rule: under $25K equity, max 3 day trades in 5 rolling business days.
        Returns True if trade is allowed, False if it would violate PDT.
        """
        if self.mode == "alert_only":
            return True

        if not self.pdt_protection:
            return True

        # PDT only applies if account < $25K
        if self._account_value >= self.pdt_threshold:
            return True

        # Count day trades in last 5 business days
        cutoff = date.today() - timedelta(days=7)  # 7 calendar days ≈ 5 business
        recent = [dt for dt in self._day_trades if dt.trade_date >= cutoff]

        if len(recent) >= 3:
            logger.warning(
                f"PDT limit: {len(recent)} day trades in rolling 5-day window"
            )
            return False

        return True

    def record_day_trade(self, symbol: str, buy_time: str, sell_time: str) -> None:
        """Record a completed day trade (bought and sold same day)."""
        self._day_trades.append(
            PDTRecord(
                symbol=symbol,
                trade_date=date.today(),
                buy_time=buy_time,
                sell_time=sell_time,
            )
        )
        # Prune old records (> 7 calendar days)
        cutoff = date.today() - timedelta(days=7)
        self._day_trades = [dt for dt in self._day_trades if dt.trade_date >= cutoff]

    @property
    def pdt_trades_remaining(self) -> int:
        """How many day trades are available in the current 5-day window."""
        if not self.pdt_protection or self._account_value >= self.pdt_threshold:
            return 999  # effectively unlimited

        cutoff = date.today() - timedelta(days=7)
        recent = [dt for dt in self._day_trades if dt.trade_date >= cutoff]
        return max(0, 3 - len(recent))

    # ── Position tracking ──────────────────────────────────────────────

    def open_position(
        self,
        symbol: str,
        entry_price: float,
        quantity: int,
        entry_time: str,
        session: Literal["morning", "midday", "close"],
        order_ids: list[int] | None = None,
    ) -> None:
        """Record a new open position."""
        self._open_positions[symbol] = PositionSlot(
            symbol=symbol,
            entry_price=entry_price,
            quantity=quantity,
            entry_time=entry_time,
            session=session,
            order_ids=order_ids or [],
        )
        if session == "morning":
            self._morning_entries_today += 1
        logger.info(
            f"Opened position: {symbol} {quantity}@${entry_price:.2f} ({session})"
        )

    def close_position(self, symbol: str) -> PositionSlot | None:
        """Remove a closed position. Returns the slot if found."""
        slot = self._open_positions.pop(symbol, None)
        if slot:
            logger.info(f"Closed position: {symbol}")
        return slot

    def get_position(self, symbol: str) -> PositionSlot | None:
        """Get details of an open position."""
        return self._open_positions.get(symbol)

    def reset_daily(self) -> None:
        """Reset daily counters (call at start of each trading day)."""
        self._morning_entries_today = 0
        logger.info("Daily counters reset")

    # ── Pre-trade gate (all checks combined) ───────────────────────────

    def pre_trade_check(
        self,
        entry_price: float,
        stop_price: float,
        session: Literal["morning", "midday", "close"],
        streak_multiplier: float = 1.0,
    ) -> tuple[bool, int, str]:
        """Run all capital/slot/PDT checks before placing an order.

        Returns:
            (allowed: bool, shares: int, reason: str)

        If allowed is False, reason explains why. Shares is 0 if not allowed.
        """
        if self.mode == "alert_only":
            return True, 0, "alert_only mode"

        # PDT check
        if not self.pdt_ok():
            return False, 0, f"PDT limit reached ({3 - self.pdt_trades_remaining}/3 used)"

        # Slot check
        if not self.has_slot(session):
            return False, 0, f"No slot available ({self.open_position_count}/{self.max_concurrent_positions})"

        # Position sizing
        shares = self.calculate_position_size(entry_price, stop_price, streak_multiplier)
        if shares <= 0:
            return False, 0, "Position size = 0 (insufficient capital or risk budget)"

        # Affordability check
        if not self.can_afford(entry_price, shares):
            return False, 0, f"Insufficient buying power for {shares} shares @ ${entry_price:.2f}"

        return True, shares, "ok"
