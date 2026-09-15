"""
MDRAP Portfolio and Watchlist Tracker
Implements persistent watchlist manager and portfolio position/P&L tracker
with multi-dimensional performance attribution.
"""

import os
import sqlite3
import time
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Watchlist:
    name: str
    symbols: list[str] = field(default_factory=list)
    created_at: float = 0.0
    description: str = ""


@dataclass
class PortfolioPosition:
    symbol: str
    quantity: float
    avg_cost: float
    current_price: float = 0.0
    realized_pnl: float = 0.0
    sector: str = ""
    strategy: str = ""
    currency: str = "USD"
    venue: str = "XNAS"

    @property
    def unrealized_pnl(self) -> float:
        return (self.current_price - self.avg_cost) * self.quantity

    @property
    def market_value(self) -> float:
        return self.current_price * abs(self.quantity)

    @property
    def cost_basis(self) -> float:
        return self.avg_cost * abs(self.quantity)

    @property
    def return_pct(self) -> float:
        if self.avg_cost == 0:
            return 0.0
        return (self.current_price - self.avg_cost) / self.avg_cost * 100


@dataclass
class PerformanceSnapshot:
    timestamp: float
    total_equity: float
    cash: float
    market_value: float
    realized_pnl: float
    unrealized_pnl: float
    daily_pnl: float = 0.0


class WatchlistManager:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path
        self._watchlists: dict[str, Watchlist] = {}
        self._conn: sqlite3.Connection | None = None
        if self.db_path:
            if self.db_path != ":memory:":
                os.makedirs(
                    os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True
                )
            self._conn = sqlite3.connect(self.db_path)
            self._init_db()
            self._load_from_db()

    def _init_db(self):
        if not self._conn:
            return
        with self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS watchlists (
                    name TEXT PRIMARY KEY,
                    symbols TEXT,
                    created_at REAL,
                    description TEXT
                )
            """)

    def _load_from_db(self):
        if not self._conn:
            return
        cursor = self._conn.execute(
            "SELECT name, symbols, created_at, description FROM watchlists"
        )
        for row in cursor:
            name, symbols_json, created_at, desc = row
            symbols = json.loads(symbols_json) if symbols_json else []
            self._watchlists[name] = Watchlist(
                name=name, symbols=symbols, created_at=created_at, description=desc
            )

    def _save_to_db(self, wl: Watchlist):
        if not self._conn:
            return
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO watchlists (name, symbols, created_at, description)
                VALUES (?, ?, ?, ?)
            """,
                (wl.name, json.dumps(wl.symbols), wl.created_at, wl.description),
            )

    def _delete_from_db(self, name: str):
        if not self._conn:
            return
        with self._conn:
            self._conn.execute("DELETE FROM watchlists WHERE name = ?", (name,))

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def create(
        self, name: str, symbols: list[str] | None = None, description: str = ""
    ) -> Watchlist:
        if name in self._watchlists:
            wl = self._watchlists[name]
            if symbols is not None:
                for s in symbols:
                    if s not in wl.symbols:
                        wl.symbols.append(s)
            wl.description = description or wl.description
        else:
            wl = Watchlist(
                name=name,
                symbols=list(symbols) if symbols else [],
                created_at=time.time(),
                description=description,
            )
            self._watchlists[name] = wl
        self._save_to_db(wl)
        return wl

    def add_symbols(self, name: str, symbols: list[str]) -> Watchlist:
        wl = self._watchlists.get(name)
        if not wl:
            return self.create(name, symbols)
        for s in symbols:
            if s not in wl.symbols:
                wl.symbols.append(s)
        self._save_to_db(wl)
        return wl

    def remove_symbols(self, name: str, symbols: list[str]) -> Watchlist:
        wl = self._watchlists.get(name)
        if not wl:
            raise ValueError(f"Watchlist {name} not found")
        wl.symbols = [s for s in wl.symbols if s not in symbols]
        self._save_to_db(wl)
        return wl

    def delete(self, name: str) -> bool:
        if name in self._watchlists:
            del self._watchlists[name]
            self._delete_from_db(name)
            return True
        return False

    def get(self, name: str) -> Watchlist | None:
        return self._watchlists.get(name)

    def list_all(self) -> list[Watchlist]:
        return list(self._watchlists.values())

    def search(self, symbol: str) -> list[Any]:
        class _WatchlistSearchResult(str):
            @property
            def name(self) -> str:
                return str(self)

        return [
            _WatchlistSearchResult(name)
            for name, wl in self._watchlists.items()
            if symbol in wl.symbols
        ]


class PortfolioTracker:
    def __init__(self, initial_cash: float = 100_000.0, db_path: str | None = None):
        self.db_path = db_path
        self._initial_cash = initial_cash
        self._cash = initial_cash
        self._positions: dict[str, PortfolioPosition] = {}
        self._snapshots: list[PerformanceSnapshot] = []
        self._conn: sqlite3.Connection | None = None
        if self.db_path:
            if self.db_path != ":memory:":
                os.makedirs(
                    os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True
                )
            self._conn = sqlite3.connect(self.db_path)
            self._init_db()
            self._load_from_db()

    def _init_db(self):
        if not self._conn:
            return
        with self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    symbol TEXT PRIMARY KEY,
                    quantity REAL,
                    avg_cost REAL,
                    realized_pnl REAL,
                    sector TEXT,
                    strategy TEXT
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    timestamp REAL,
                    total_equity REAL,
                    cash REAL,
                    market_value REAL,
                    realized_pnl REAL,
                    unrealized_pnl REAL,
                    daily_pnl REAL
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value REAL
                )
            """)
            # Initialize or retrieve cash
            cursor = self._conn.execute("SELECT value FROM config WHERE key = 'cash'")
            row = cursor.fetchone()
            if row:
                self._cash = row[0]
            else:
                self._conn.execute(
                    "INSERT INTO config (key, value) VALUES ('cash', ?)",
                    (self._initial_cash,),
                )

    def _load_from_db(self):
        if not self._conn:
            return
        cursor = self._conn.execute(
            "SELECT symbol, quantity, avg_cost, realized_pnl, sector, strategy FROM positions"
        )
        for row in cursor:
            try:
                from symbology import resolve_symbol

                sym_info = resolve_symbol(row[0])
                pos = PortfolioPosition(
                    symbol=row[0],
                    quantity=row[1],
                    avg_cost=row[2],
                    realized_pnl=row[3],
                    sector=row[4] or "",
                    strategy=row[5] or "",
                    currency=sym_info.currency,
                    venue=sym_info.venue_mic,
                )
            except Exception:
                pos = PortfolioPosition(
                    symbol=row[0],
                    quantity=row[1],
                    avg_cost=row[2],
                    realized_pnl=row[3],
                    sector=row[4] or "",
                    strategy=row[5] or "",
                )
            self._positions[pos.symbol] = pos

        cursor = self._conn.execute(
            "SELECT timestamp, total_equity, cash, market_value, realized_pnl, unrealized_pnl, daily_pnl FROM snapshots ORDER BY timestamp"
        )
        for row in cursor:
            snap = PerformanceSnapshot(*row)
            self._snapshots.append(snap)

    def _save_position_to_db(self, pos: PortfolioPosition):
        if not self._conn:
            return
        with self._conn:
            if pos.quantity == 0 and pos.realized_pnl == 0:
                self._conn.execute(
                    "DELETE FROM positions WHERE symbol = ?", (pos.symbol,)
                )
            else:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO positions (symbol, quantity, avg_cost, realized_pnl, sector, strategy)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (
                        pos.symbol,
                        pos.quantity,
                        pos.avg_cost,
                        pos.realized_pnl,
                        pos.sector,
                        pos.strategy,
                    ),
                )

    def _save_cash_to_db(self):
        if not self._conn:
            return
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO config (key, value) VALUES ('cash', ?)",
                (self._cash,),
            )

    def _save_snapshot_to_db(self, snap: PerformanceSnapshot):
        if not self._conn:
            return
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO snapshots (timestamp, total_equity, cash, market_value, realized_pnl, unrealized_pnl, daily_pnl)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    snap.timestamp,
                    snap.total_equity,
                    snap.cash,
                    snap.market_value,
                    snap.realized_pnl,
                    snap.unrealized_pnl,
                    snap.daily_pnl,
                ),
            )

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def add_trade(
        self,
        symbol: str,
        quantity: float,
        price: float,
        sector: str = "",
        strategy: str = "",
    ) -> PortfolioPosition:
        """Record a trade. Positive qty = buy, negative = sell.
        Updates avg_cost on buys, records realized P&L on sells."""
        if symbol not in self._positions:
            try:
                from symbology import resolve_symbol

                sym_info = resolve_symbol(symbol)
                pos = PortfolioPosition(
                    symbol=symbol,
                    quantity=0,
                    avg_cost=0,
                    sector=sector,
                    strategy=strategy,
                    currency=sym_info.currency,
                    venue=sym_info.venue_mic,
                )
            except Exception:
                pos = PortfolioPosition(
                    symbol=symbol,
                    quantity=0,
                    avg_cost=0,
                    sector=sector,
                    strategy=strategy,
                )
            self._positions[symbol] = pos
        else:
            pos = self._positions[symbol]
            if sector:
                pos.sector = sector
            if strategy:
                pos.strategy = strategy

        trade_value = quantity * price
        self._cash -= trade_value

        if pos.quantity * quantity > 0 or pos.quantity == 0:
            # increasing position
            total_cost = pos.avg_cost * pos.quantity + trade_value
            pos.quantity += quantity
            pos.avg_cost = total_cost / pos.quantity
        else:
            # decreasing position
            closing_qty = min(abs(pos.quantity), abs(quantity))
            direction = 1 if pos.quantity > 0 else -1
            realized = (price - pos.avg_cost) * closing_qty * direction
            pos.realized_pnl += realized

            pos.quantity += quantity
            if pos.quantity == 0:
                pos.avg_cost = 0.0
            elif pos.quantity * direction < 0:
                # position flipped
                pos.avg_cost = price

        pos.current_price = price
        self._save_position_to_db(pos)
        self._save_cash_to_db()
        return pos

    def update_price(self, symbol: str, price: float):
        """Update current market price for a position."""
        if symbol in self._positions:
            self._positions[symbol].current_price = price

    def update_prices(self, prices: dict[str, float]):
        """Bulk price update."""
        for symbol, price in prices.items():
            self.update_price(symbol, price)

    def get_position(self, symbol: str) -> PortfolioPosition | None:
        return self._positions.get(symbol)

    def all_positions(self) -> list[PortfolioPosition]:
        return list(self._positions.values())

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def market_value(self) -> float:
        return sum(pos.market_value for pos in self._positions.values())

    @property
    def total_equity(self) -> float:
        return self.cash + self.market_value

    def total_equity_in(self, target_currency: str = "USD") -> float:
        """Converts cash and all position market values into target currency using live FX matrix."""
        from fx import convert_currency

        total = convert_currency(self._cash, "USD", target_currency)
        for pos in self._positions.values():
            mv_converted = convert_currency(
                pos.market_value, pos.currency, target_currency
            )
            total += mv_converted
        return total

    @property
    def total_realized_pnl(self) -> float:
        return sum(pos.realized_pnl for pos in self._positions.values())

    @property
    def total_unrealized_pnl(self) -> float:
        return sum(pos.unrealized_pnl for pos in self._positions.values())

    def snapshot(self, timestamp: float | None = None) -> PerformanceSnapshot:
        """Take a point-in-time portfolio snapshot."""
        ts = timestamp or time.time()
        prev_equity = (
            self._snapshots[-1].total_equity if self._snapshots else self._initial_cash
        )
        equity = self.total_equity
        daily_pnl = equity - prev_equity

        snap = PerformanceSnapshot(
            timestamp=ts,
            total_equity=equity,
            cash=self.cash,
            market_value=self.market_value,
            realized_pnl=self.total_realized_pnl,
            unrealized_pnl=self.total_unrealized_pnl,
            daily_pnl=daily_pnl,
        )
        self._snapshots.append(snap)
        self._save_snapshot_to_db(snap)
        return snap

    def pnl_by_symbol(self) -> dict[str, dict]:
        """Return P&L breakdown per symbol."""
        res = {}
        for sym, pos in self._positions.items():
            res[sym] = {
                "realized_pnl": pos.realized_pnl,
                "unrealized_pnl": pos.unrealized_pnl,
                "total_pnl": pos.realized_pnl + pos.unrealized_pnl,
            }
        return res

    def pnl_by_sector(self) -> dict[str, dict]:
        """Return P&L breakdown per sector."""
        res: dict[str, dict] = {}
        for pos in self._positions.values():
            s = pos.sector or "Uncategorized"
            if s not in res:
                res[s] = {"realized_pnl": 0.0, "unrealized_pnl": 0.0, "total_pnl": 0.0}
            res[s]["realized_pnl"] += pos.realized_pnl
            res[s]["unrealized_pnl"] += pos.unrealized_pnl
            res[s]["total_pnl"] += pos.realized_pnl + pos.unrealized_pnl
        return res

    def pnl_by_strategy(self) -> dict[str, dict]:
        """Return P&L breakdown per strategy."""
        res: dict[str, dict] = {}
        for pos in self._positions.values():
            s = pos.strategy or "Uncategorized"
            if s not in res:
                res[s] = {"realized_pnl": 0.0, "unrealized_pnl": 0.0, "total_pnl": 0.0}
            res[s]["realized_pnl"] += pos.realized_pnl
            res[s]["unrealized_pnl"] += pos.unrealized_pnl
            res[s]["total_pnl"] += pos.realized_pnl + pos.unrealized_pnl
        return res

    def benchmark_comparison(
        self,
        benchmark_start_price_or_return: float,
        benchmark_current_price: float | None = None,
    ) -> dict:
        """Compare portfolio return vs benchmark (e.g. SPY)."""
        if self._initial_cash == 0:
            port_ret = 0.0
        else:
            port_ret = (
                (self.total_equity - self._initial_cash) / self._initial_cash * 100
            )

        if benchmark_current_price is None:
            bench_ret = benchmark_start_price_or_return
        else:
            if benchmark_start_price_or_return == 0:
                bench_ret = 0.0
            else:
                bench_ret = (
                    (benchmark_current_price - benchmark_start_price_or_return)
                    / benchmark_start_price_or_return
                    * 100
                )

        return {
            "portfolio_return_pct": port_ret,
            "benchmark_return_pct": bench_ret,
            "outperformance_pct": port_ret - bench_ret,
            "alpha": port_ret - bench_ret,
            "alpha_pct": port_ret - bench_ret,
        }

    def daily_pnl_series(self) -> list[PerformanceSnapshot]:
        """Return all snapshots for daily P&L tracking."""
        return self._snapshots

    def summary(self) -> dict:
        """Complete portfolio summary."""
        return {
            "cash": self.cash,
            "market_value": self.market_value,
            "total_equity": self.total_equity,
            "realized_pnl": self.total_realized_pnl,
            "unrealized_pnl": self.total_unrealized_pnl,
            "total_realized_pnl": self.total_realized_pnl,
            "total_unrealized_pnl": self.total_unrealized_pnl,
            "initial_cash": self._initial_cash,
            "total_return_pct": (self.total_equity - self._initial_cash)
            / self._initial_cash
            * 100
            if self._initial_cash
            else 0.0,
            "position_count": len(
                [p for p in self._positions.values() if p.quantity != 0]
            ),
        }
