"""
Corporate Actions Processor for MDRAP.

This module implements the corporate actions processor handling stock splits,
dividends, ticker changes, delistings, and spin-offs. This is critical for
ensuring historically adjusted data is available for accurate backtesting
and analysis.
"""

import enum
import itertools
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

__stability__ = "experimental"


class ActionType(str, enum.Enum):
    SPLIT = "SPLIT"
    REVERSE_SPLIT = "REVERSE_SPLIT"
    CASH_DIVIDEND = "CASH_DIVIDEND"
    TICKER_CHANGE = "TICKER_CHANGE"
    DELISTING = "DELISTING"
    SPINOFF = "SPINOFF"


@dataclass(slots=True)
class CorporateAction:
    action_id: int
    action_type: ActionType
    symbol: str
    effective_date: str  # 'YYYY-MM-DD'
    effective_timestamp: float  # epoch
    ratio: float = 1.0  # split ratio (e.g., 4.0 for 4:1 split)
    amount: float = 0.0  # dividend amount per share
    new_symbol: str = ""  # for TICKER_CHANGE
    description: str = ""


@dataclass(slots=True)
class AdjustmentFactor:
    symbol: str
    as_of_date: str
    cumulative_split_factor: float  # multiply raw price by this
    cumulative_dividend_factor: float  # multiply raw price by this for total return
    combined_factor: float  # split * dividend factor


def _date_to_timestamp(date_str: str) -> float:
    try:
        struct = time.strptime(date_str, "%Y-%m-%d")
        return time.mktime(struct)
    except ValueError:
        return 0.0


class SymbolHistoryList(list):
    def __contains__(self, item: Any) -> bool:
        if super().__contains__(item):
            return True
        return any(
            item in (d.get("old_symbol"), d.get("new_symbol"))
            for d in self
            if isinstance(d, dict)
        )


class CorporateActionsEngine:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path
        self._conn = sqlite3.connect(self.db_path) if self.db_path else None
        self._action_id_gen = itertools.count(1)
        self._actions: list[CorporateAction] = []

        if self._conn:
            self._init_db()
            self._load_from_db()

    def _init_db(self) -> None:
        if not self._conn:
            return
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS corporate_actions (
                action_id INTEGER PRIMARY KEY,
                action_type TEXT,
                symbol TEXT,
                effective_date TEXT,
                effective_timestamp REAL,
                ratio REAL,
                amount REAL,
                new_symbol TEXT,
                description TEXT
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS ticker_history (
                old_symbol TEXT,
                new_symbol TEXT,
                change_date TEXT
            )
        """)
        self._conn.commit()

    def _load_from_db(self) -> None:
        if not self._conn:
            return
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT action_id, action_type, symbol, effective_date, effective_timestamp, ratio, amount, new_symbol, description FROM corporate_actions ORDER BY action_id"
        )
        rows = cursor.fetchall()
        max_id = 0
        for row in rows:
            action = CorporateAction(
                action_id=row[0],
                action_type=ActionType(row[1]),
                symbol=row[2],
                effective_date=row[3],
                effective_timestamp=row[4],
                ratio=row[5],
                amount=row[6],
                new_symbol=row[7] if row[7] is not None else "",
                description=row[8] if row[8] is not None else "",
            )
            self._actions.append(action)
            if row[0] > max_id:
                max_id = row[0]
        if max_id > 0:
            self._action_id_gen = itertools.count(max_id + 1)

    def _add_action(self, action: CorporateAction) -> None:
        self._actions.append(action)
        if self._conn:
            self._conn.execute(
                """
                INSERT INTO corporate_actions 
                (action_id, action_type, symbol, effective_date, effective_timestamp, ratio, amount, new_symbol, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    action.action_id,
                    action.action_type.value,
                    action.symbol,
                    action.effective_date,
                    action.effective_timestamp,
                    action.ratio,
                    action.amount,
                    action.new_symbol,
                    action.description,
                ),
            )
            if action.action_type == ActionType.TICKER_CHANGE:
                self._conn.execute(
                    """
                    INSERT INTO ticker_history (old_symbol, new_symbol, change_date)
                    VALUES (?, ?, ?)
                """,
                    (action.symbol, action.new_symbol, action.effective_date),
                )
            self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def add_split(
        self, symbol: str, effective_date: str, ratio: float, description: str = ""
    ) -> CorporateAction:
        """Record a stock split. ratio > 1 = forward split (e.g. 4:1 = 4.0), ratio < 1 = reverse split."""
        action_type = ActionType.SPLIT if ratio >= 1.0 else ActionType.REVERSE_SPLIT
        action = CorporateAction(
            action_id=next(self._action_id_gen),
            action_type=action_type,
            symbol=symbol,
            effective_date=effective_date,
            effective_timestamp=_date_to_timestamp(effective_date),
            ratio=ratio,
            description=description,
        )
        self._add_action(action)
        return action

    def add_dividend(
        self, symbol: str, effective_date: str, amount: float, description: str = ""
    ) -> CorporateAction:
        """Record a cash dividend with ex-date."""
        action = CorporateAction(
            action_id=next(self._action_id_gen),
            action_type=ActionType.CASH_DIVIDEND,
            symbol=symbol,
            effective_date=effective_date,
            effective_timestamp=_date_to_timestamp(effective_date),
            amount=amount,
            description=description,
        )
        self._add_action(action)
        return action

    def add_ticker_change(
        self,
        old_symbol: str,
        new_symbol: str,
        effective_date: str,
        description: str = "",
    ) -> CorporateAction:
        """Record a ticker symbol change (e.g. FB -> META)."""
        action = CorporateAction(
            action_id=next(self._action_id_gen),
            action_type=ActionType.TICKER_CHANGE,
            symbol=old_symbol,
            effective_date=effective_date,
            effective_timestamp=_date_to_timestamp(effective_date),
            new_symbol=new_symbol,
            description=description,
        )
        self._add_action(action)
        return action

    def add_delisting(
        self, symbol: str, effective_date: str, description: str = ""
    ) -> CorporateAction:
        """Record a delisting."""
        action = CorporateAction(
            action_id=next(self._action_id_gen),
            action_type=ActionType.DELISTING,
            symbol=symbol,
            effective_date=effective_date,
            effective_timestamp=_date_to_timestamp(effective_date),
            description=description,
        )
        self._add_action(action)
        return action

    def add_spinoff(
        self,
        parent_symbol: str,
        child_symbol: str,
        effective_date: str,
        ratio: float,
        description: str = "",
    ) -> CorporateAction:
        """Record a spin-off. ratio = shares of child per share of parent."""
        action = CorporateAction(
            action_id=next(self._action_id_gen),
            action_type=ActionType.SPINOFF,
            symbol=parent_symbol,
            effective_date=effective_date,
            effective_timestamp=_date_to_timestamp(effective_date),
            ratio=ratio,
            new_symbol=child_symbol,
            description=description,
        )
        self._add_action(action)
        return action

    def adjust_price(
        self, symbol: str, raw_price: float, as_of_date: str, adjustment: str = "split"
    ) -> float:
        """Adjust a historical price for splits and/or dividends.
        adjustment='split' for split-only, 'total' for split+dividend (total return), 'dividend' for dividend-only."""
        factor = self.adjustment_factor(symbol, as_of_date)
        if adjustment == "split":
            return raw_price / factor.cumulative_split_factor
        elif adjustment in ("total", "dividend"):
            total_divs = sum(
                a.amount
                for a in self.actions_for(symbol)
                if a.action_type == ActionType.CASH_DIVIDEND
                and a.effective_date > as_of_date
            )
            adj = (raw_price - total_divs) if total_divs > 0 else raw_price
            if adjustment == "total":
                return adj / factor.cumulative_split_factor
            return adj
        return raw_price

    def adjust_volume(self, symbol: str, raw_volume: float, as_of_date: str) -> float:
        """Adjust historical volume for splits (inverse of price adjustment)."""
        factor = self.adjustment_factor(symbol, as_of_date)
        return raw_volume * factor.cumulative_split_factor

    def adjustment_factor(self, symbol: str, as_of_date: str) -> AdjustmentFactor:
        """Get cumulative adjustment factors for a symbol at a given date."""
        split_factor = 1.0
        div_factor = 1.0

        for action in self.actions_for(symbol):
            if action.effective_date > as_of_date:
                if action.action_type in (ActionType.SPLIT, ActionType.REVERSE_SPLIT):
                    split_factor *= action.ratio
                elif action.action_type == ActionType.CASH_DIVIDEND:
                    div_factor *= 1.0 - (action.amount / 100.0)

        return AdjustmentFactor(
            symbol=symbol,
            as_of_date=as_of_date,
            cumulative_split_factor=split_factor,
            cumulative_dividend_factor=div_factor,
            combined_factor=split_factor * div_factor,
        )

    def resolve_symbol(self, symbol: str) -> str:
        """Resolve current symbol from historical symbol (e.g. FB -> META)."""
        current = symbol
        for action in self._actions:
            if (
                action.action_type == ActionType.TICKER_CHANGE
                and action.symbol == current
            ):
                current = action.new_symbol
        return current

    def symbol_history(self, current_symbol: str) -> list[dict]:
        """Return the complete symbol change history."""
        history = []
        target = current_symbol

        found = True
        while found:
            found = False
            for action in reversed(self._actions):
                if (
                    action.action_type == ActionType.TICKER_CHANGE
                    and action.new_symbol == target
                ):
                    history.append(
                        {
                            "old_symbol": action.symbol,
                            "new_symbol": action.new_symbol,
                            "change_date": action.effective_date,
                        }
                    )
                    target = action.symbol
                    found = True
                    break

        return SymbolHistoryList(history[::-1])

    def is_active(self, symbol: str) -> bool:
        """Check if a symbol is currently active (not delisted)."""
        for action in self.actions_for(symbol):
            if action.action_type == ActionType.DELISTING:
                return False
            if (
                action.action_type == ActionType.TICKER_CHANGE
                and action.symbol == symbol
            ):
                # The old symbol is no longer active
                return False
        return True

    def actions_for(self, symbol: str) -> list[CorporateAction]:
        """Get all corporate actions for a symbol, sorted by date."""
        symbol_actions = [
            a for a in self._actions if a.symbol == symbol or a.new_symbol == symbol
        ]
        return sorted(symbol_actions, key=lambda a: a.effective_timestamp)

    def pending_actions(self, as_of_date: str) -> list[CorporateAction]:
        """Get actions with effective_date > as_of_date."""
        return [a for a in self._actions if a.effective_date > as_of_date]

    def summary(self) -> dict:
        """Return summary: total actions by type, active symbols, delisted symbols."""
        counts: dict[str, int] = {}
        all_symbols = set()
        delisted_symbols = set()

        for action in self._actions:
            counts[action.action_type.value] = (
                counts.get(action.action_type.value, 0) + 1
            )
            all_symbols.add(action.symbol)
            if action.new_symbol:
                all_symbols.add(action.new_symbol)
            if action.action_type == ActionType.DELISTING:
                delisted_symbols.add(action.symbol)

        active = sum(1 for s in all_symbols if self.is_active(s))

        return {
            "total_actions": len(self._actions),
            "by_type": counts,
            "total_symbols": len(all_symbols),
            "active_symbols": active,
            "delisted_symbols": len(delisted_symbols),
        }


WELL_KNOWN_ACTIONS = [
    # Apple 4:1 split (2020-08-31)
    ("AAPL", "SPLIT", "2020-08-31", 4.0, "Apple 4-for-1 stock split"),
    # Tesla 3:1 split (2022-08-25)
    ("TSLA", "SPLIT", "2022-08-25", 3.0, "Tesla 3-for-1 stock split"),
    # Google 20:1 split (2022-07-18)
    ("GOOGL", "SPLIT", "2022-07-18", 20.0, "Alphabet 20-for-1 stock split"),
    # Amazon 20:1 split (2022-06-06)
    ("AMZN", "SPLIT", "2022-06-06", 20.0, "Amazon 20-for-1 stock split"),
    # Nvidia 10:1 split (2024-06-10)
    ("NVDA", "SPLIT", "2024-06-10", 10.0, "NVIDIA 10-for-1 stock split"),
    # Facebook -> Meta ticker change (2021-10-28)
    (
        "FB",
        "TICKER_CHANGE",
        "2021-10-28",
        0,
        "Facebook rebranded to Meta Platforms",
        "META",
    ),
    # Twitter delisting (2022-10-28)
    ("TWTR", "DELISTING", "2022-10-28", 0, "Twitter acquired by Elon Musk, delisted"),
]


def load_well_known(engine: CorporateActionsEngine) -> None:
    for action in WELL_KNOWN_ACTIONS:
        symbol, type_str, eff_date, ratio_or_amount, desc = (
            action[0],
            action[1],
            action[2],
            action[3],
            action[4],
        )

        if type_str == "SPLIT":
            engine.add_split(symbol, eff_date, ratio_or_amount, desc)
        elif type_str == "TICKER_CHANGE":
            new_symbol = action[5]
            engine.add_ticker_change(symbol, new_symbol, eff_date, desc)
        elif type_str == "DELISTING":
            engine.add_delisting(symbol, eff_date, desc)
        elif type_str == "CASH_DIVIDEND":
            engine.add_dividend(symbol, eff_date, ratio_or_amount, desc)
        elif type_str == "SPINOFF":
            new_symbol = action[5]
            engine.add_spinoff(symbol, new_symbol, eff_date, ratio_or_amount, desc)
