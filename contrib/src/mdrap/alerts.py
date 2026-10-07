"""
Alerting & Notification System for MDRAP.

This module provides persistent, configurable alerts for market price levels,
spread thresholds, volume anomalies, quality events, and portfolio drawdown monitoring.
"""

import enum
import sqlite3
import time
import itertools
from dataclasses import dataclass
from typing import Callable

__stability__ = "beta"


class AlertType(str, enum.Enum):
    PRICE_ABOVE = "PRICE_ABOVE"
    PRICE_BELOW = "PRICE_BELOW"
    PRICE_CHANGE_PCT = "PRICE_CHANGE_PCT"
    SPREAD_ABOVE = "SPREAD_ABOVE"
    VOLUME_SPIKE = "VOLUME_SPIKE"
    QUALITY_DEGRADATION = "QUALITY_DEGRADATION"
    FEED_SILENCE = "FEED_SILENCE"
    DRAWDOWN = "DRAWDOWN"
    CUSTOM = "CUSTOM"


class AlertStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    TRIGGERED = "TRIGGERED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


@dataclass
class Alert:
    alert_id: int
    alert_type: AlertType
    symbol: str
    condition: str  # human-readable: 'AAPL price > 200.00'
    threshold: float
    status: AlertStatus = AlertStatus.ACTIVE
    created_at: float = 0.0
    triggered_at: float = 0.0
    triggered_value: float = 0.0
    repeat: bool = False  # if True, re-arms after trigger
    expiry: float = 0.0  # 0 = never expires
    message: str = ""  # triggered message

    # Anti-flapping and noise suppression controls (Hysteresis & Cooldown)
    hysteresis_ticks: int = 1  # Required consecutive tick breaches before firing
    hysteresis_margin_pct: float = (
        0.5  # Margin required to clear condition before re-arming
    )
    cooldown_s: float = 0.0  # Min seconds between re-notifications (0 = fire immediately on each re-arm)
    consecutive_breaches: int = 0  # Monotonic count of consecutive ticks meeting breach
    last_notified_at: float = 0.0
    re_armed: bool = True

    # Last-mile delivery tracking (preventing false negatives)
    delivery_status: str = "pending"  # "pending", "delivered", "failed"
    delivery_attempts: int = 0
    last_attempt_at: float = 0.0
    last_error: str | None = None


class AlertEngine:
    """
    Implements a persistent, configurable alerting engine for market price levels,
    spread thresholds, volume anomalies, and portfolio drawdown monitoring.
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path
        self._id_gen = itertools.count(1)
        self.alerts: list[Alert] = []
        self._reference_prices: dict[str, float] = {}
        self.conn = None
        # Delivery telemetry counters
        self.delivery_delivered_count: int = 0
        self.delivery_failed_count: int = 0
        self.delivery_backlog_count: int = 0
        if self.db_path:
            self.conn = sqlite3.connect(self.db_path)
            self._init_db()

    def _init_db(self):
        if not self.conn:
            return
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                alert_id INTEGER PRIMARY KEY,
                alert_type TEXT,
                symbol TEXT,
                condition TEXT,
                threshold REAL,
                status TEXT,
                created_at REAL,
                triggered_at REAL,
                triggered_value REAL,
                repeat INTEGER,
                expiry REAL,
                message TEXT,
                hysteresis_ticks INTEGER DEFAULT 1,
                hysteresis_margin_pct REAL DEFAULT 0.5,
                cooldown_s REAL DEFAULT 0.0,
                consecutive_breaches INTEGER DEFAULT 0,
                last_notified_at REAL DEFAULT 0.0,
                re_armed INTEGER DEFAULT 1,
                delivery_status TEXT DEFAULT 'pending',
                delivery_attempts INTEGER DEFAULT 0,
                last_attempt_at REAL DEFAULT 0.0,
                last_error TEXT DEFAULT NULL
            )
        """)
        # Safe migration if table previously existed with fewer columns
        existing_cols = {
            row[1] for row in cursor.execute("PRAGMA table_info(alerts)").fetchall()
        }
        migration_cols = [
            ("hysteresis_ticks", "INTEGER DEFAULT 1"),
            ("hysteresis_margin_pct", "REAL DEFAULT 0.5"),
            ("cooldown_s", "REAL DEFAULT 0.0"),
            ("consecutive_breaches", "INTEGER DEFAULT 0"),
            ("last_notified_at", "REAL DEFAULT 0.0"),
            ("re_armed", "INTEGER DEFAULT 1"),
            ("delivery_status", "TEXT DEFAULT 'pending'"),
            ("delivery_attempts", "INTEGER DEFAULT 0"),
            ("last_attempt_at", "REAL DEFAULT 0.0"),
            ("last_error", "TEXT DEFAULT NULL"),
        ]
        for col, ctype in migration_cols:
            if col not in existing_cols:
                try:
                    cursor.execute(f"ALTER TABLE alerts ADD COLUMN {col} {ctype};")
                except Exception:
                    pass
        self.conn.commit()

        cursor.execute("SELECT MAX(alert_id) FROM alerts")
        row = cursor.fetchone()
        max_id = row[0] if row and row[0] is not None else 0
        self._id_gen = itertools.count(max_id + 1)

        cursor.execute("""
            SELECT alert_id, alert_type, symbol, condition, threshold, status, 
                   created_at, triggered_at, triggered_value, repeat, expiry, message,
                   hysteresis_ticks, hysteresis_margin_pct, cooldown_s, consecutive_breaches,
                   last_notified_at, re_armed, delivery_status, delivery_attempts, last_attempt_at, last_error
            FROM alerts
        """)
        for row in cursor.fetchall():
            a = Alert(
                alert_id=row[0],
                alert_type=AlertType(row[1]),
                symbol=row[2],
                condition=row[3],
                threshold=row[4],
                status=AlertStatus(row[5]),
                created_at=row[6],
                triggered_at=row[7],
                triggered_value=row[8],
                repeat=bool(row[9]),
                expiry=row[10],
                message=row[11],
                hysteresis_ticks=row[12]
                if len(row) > 12 and row[12] is not None
                else 1,
                hysteresis_margin_pct=row[13]
                if len(row) > 13 and row[13] is not None
                else 0.5,
                cooldown_s=row[14] if len(row) > 14 and row[14] is not None else 0.0,
                consecutive_breaches=row[15]
                if len(row) > 15 and row[15] is not None
                else 0,
                last_notified_at=row[16]
                if len(row) > 16 and row[16] is not None
                else 0.0,
                re_armed=bool(row[17])
                if len(row) > 17 and row[17] is not None
                else True,
                delivery_status=row[18]
                if len(row) > 18 and row[18] is not None
                else "pending",
                delivery_attempts=row[19]
                if len(row) > 19 and row[19] is not None
                else 0,
                last_attempt_at=row[20]
                if len(row) > 20 and row[20] is not None
                else 0.0,
                last_error=row[21] if len(row) > 21 else None,
            )
            self.alerts.append(a)

    def _persist(self, alert: Alert):
        if not self.conn:
            return
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO alerts 
            (alert_id, alert_type, symbol, condition, threshold, status, created_at, 
             triggered_at, triggered_value, repeat, expiry, message,
             hysteresis_ticks, hysteresis_margin_pct, cooldown_s, consecutive_breaches,
             last_notified_at, re_armed, delivery_status, delivery_attempts, last_attempt_at, last_error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                alert.alert_id,
                alert.alert_type.value,
                alert.symbol,
                alert.condition,
                alert.threshold,
                alert.status.value,
                alert.created_at,
                alert.triggered_at,
                alert.triggered_value,
                int(alert.repeat),
                alert.expiry,
                alert.message,
                alert.hysteresis_ticks,
                alert.hysteresis_margin_pct,
                alert.cooldown_s,
                alert.consecutive_breaches,
                alert.last_notified_at,
                int(alert.re_armed),
                alert.delivery_status,
                alert.delivery_attempts,
                alert.last_attempt_at,
                alert.last_error,
            ),
        )
        self.conn.commit()

    def add_price_alert(
        self, symbol: str, direction: str, threshold: float, repeat: bool = False
    ) -> Alert:
        """Add a price alert. direction='above' or 'below'."""
        atype = (
            AlertType.PRICE_ABOVE
            if direction.lower() == "above"
            else AlertType.PRICE_BELOW
        )
        op = ">" if direction.lower() == "above" else "<"
        a = Alert(
            alert_id=next(self._id_gen),
            alert_type=atype,
            symbol=symbol,
            condition=f"{symbol} price {op} {threshold}",
            threshold=threshold,
            created_at=time.time(),
            repeat=repeat,
        )
        self.alerts.append(a)
        self._persist(a)
        return a

    def add_spread_alert(self, symbol: str, max_spread_bps: float) -> Alert:
        """Alert when bid-ask spread exceeds threshold."""
        a = Alert(
            alert_id=next(self._id_gen),
            alert_type=AlertType.SPREAD_ABOVE,
            symbol=symbol,
            condition=f"{symbol} spread > {max_spread_bps} bps",
            threshold=max_spread_bps,
            created_at=time.time(),
            repeat=True,
        )
        self.alerts.append(a)
        self._persist(a)
        return a

    def add_volume_alert(self, symbol: str, threshold_qty: float) -> Alert:
        """Alert when a single trade exceeds threshold quantity (whale alert)."""
        a = Alert(
            alert_id=next(self._id_gen),
            alert_type=AlertType.VOLUME_SPIKE,
            symbol=symbol,
            condition=f"{symbol} volume > {threshold_qty}",
            threshold=threshold_qty,
            created_at=time.time(),
            repeat=True,
        )
        self.alerts.append(a)
        self._persist(a)
        return a

    def add_change_alert(self, symbol: str, change_pct: float) -> Alert:
        """Alert when price moves more than change_pct from reference."""
        a = Alert(
            alert_id=next(self._id_gen),
            alert_type=AlertType.PRICE_CHANGE_PCT,
            symbol=symbol,
            condition=f"{symbol} price change > {change_pct}%",
            threshold=change_pct,
            created_at=time.time(),
            repeat=True,
        )
        self.alerts.append(a)
        self._persist(a)
        return a

    def add_drawdown_alert(self, max_drawdown_pct: float) -> Alert:
        """Alert when portfolio drawdown exceeds threshold."""
        a = Alert(
            alert_id=next(self._id_gen),
            alert_type=AlertType.DRAWDOWN,
            symbol="PORTFOLIO",
            condition=f"Drawdown > {max_drawdown_pct}%",
            threshold=max_drawdown_pct,
            created_at=time.time(),
            repeat=False,
        )
        self.alerts.append(a)
        self._persist(a)
        return a

    def add_custom_alert(
        self,
        symbol: str,
        condition: str,
        check_fn: Callable[[dict], bool] | None = None,
    ) -> Alert:
        """Add a custom alert with optional check function."""
        a = Alert(
            alert_id=next(self._id_gen),
            alert_type=AlertType.CUSTOM,
            symbol=symbol,
            condition=condition,
            threshold=0.0,
            created_at=time.time(),
            repeat=False,
        )
        if check_fn:
            setattr(a, "_check_fn", check_fn)
        self.alerts.append(a)
        self._persist(a)
        return a

    def evaluate_tick(
        self,
        symbol: str,
        price: float,
        volume: float = 0.0,
        bid: float = 0.0,
        ask: float = 0.0,
        timestamp: float = 0.0,
    ) -> list[Alert]:
        """Evaluate all active alerts against a new tick. Returns list of newly triggered alerts."""
        ts = timestamp or time.time()
        triggered = []

        if symbol not in self._reference_prices:
            self._reference_prices[symbol] = price

        ref_price = self._reference_prices[symbol]

        for a in self.alerts:
            if a.status != AlertStatus.ACTIVE:
                continue

            if a.expiry > 0 and ts > a.expiry:
                a.status = AlertStatus.EXPIRED
                self._persist(a)
                continue

            if a.symbol != symbol and a.symbol != "PORTFOLIO":
                continue

            is_triggered = False
            trigger_val = 0.0

            if a.alert_type == AlertType.PRICE_ABOVE:
                if price >= a.threshold:
                    is_triggered = True
                    trigger_val = price
            elif a.alert_type == AlertType.PRICE_BELOW:
                if price <= a.threshold:
                    is_triggered = True
                    trigger_val = price
            elif a.alert_type == AlertType.SPREAD_ABOVE:
                if bid > 0 and ask > 0:
                    mid = (ask + bid) / 2.0
                    spread_bps = ((ask - bid) / mid) * 10000.0
                    if spread_bps >= a.threshold:
                        is_triggered = True
                        trigger_val = spread_bps
            elif a.alert_type == AlertType.VOLUME_SPIKE:
                if volume >= a.threshold:
                    is_triggered = True
                    trigger_val = volume
            elif a.alert_type == AlertType.PRICE_CHANGE_PCT:
                if ref_price > 0:
                    change = abs((price - ref_price) / ref_price) * 100.0
                    if change >= a.threshold:
                        is_triggered = True
                        trigger_val = change
                        self._reference_prices[symbol] = price
            elif a.alert_type == AlertType.CUSTOM:
                check_fn = getattr(a, "_check_fn", None)
                if check_fn:
                    if check_fn(
                        {
                            "symbol": symbol,
                            "price": price,
                            "volume": volume,
                            "bid": bid,
                            "ask": ask,
                            "timestamp": ts,
                        }
                    ):
                        is_triggered = True
                        trigger_val = price

            if is_triggered:
                a.consecutive_breaches += 1
                is_cooldown_elapsed = a.cooldown_s > 0 and (
                    ts - a.last_notified_at >= a.cooldown_s
                )
                is_first_arm = (
                    a.re_armed and a.consecutive_breaches >= a.hysteresis_ticks
                )

                if is_first_arm or is_cooldown_elapsed:
                    a.triggered_at = ts
                    a.triggered_value = trigger_val
                    a.last_notified_at = ts
                    a.re_armed = False
                    a.delivery_status = "pending"
                    a.message = f"Alert Triggered: {a.condition} at {trigger_val:.4f}"
                    triggered.append(a)

                    if not a.repeat:
                        a.status = AlertStatus.TRIGGERED

                    self._persist(a)
            else:
                # Anti-flapping: check if metric has cleared the hysteresis margin before re-arming
                cleared = False
                margin = abs(a.threshold) * (a.hysteresis_margin_pct / 100.0)
                if a.alert_type == AlertType.PRICE_ABOVE:
                    cleared = price <= (a.threshold - margin)
                elif a.alert_type == AlertType.PRICE_BELOW:
                    cleared = price >= (a.threshold + margin)
                elif a.alert_type == AlertType.SPREAD_ABOVE:
                    if bid > 0 and ask > 0:
                        mid = (ask + bid) / 2.0
                        spread_bps = ((ask - bid) / mid) * 10000.0
                        cleared = spread_bps <= (a.threshold - margin)
                elif a.alert_type == AlertType.VOLUME_SPIKE:
                    cleared = volume < a.threshold
                else:
                    cleared = True

                if cleared and (not a.re_armed or a.consecutive_breaches > 0):
                    a.consecutive_breaches = 0
                    a.re_armed = True
                    self._persist(a)

        return triggered

    def list_alerts(
        self, status: AlertStatus | None = None, symbol: str | None = None
    ) -> list[Alert]:
        """List alerts with optional filters."""
        res = self.alerts
        if status:
            res = [a for a in res if a.status == status]
        if symbol:
            res = [a for a in res if a.symbol == symbol]
        return res

    def cancel_alert(self, alert_id: int) -> bool:
        """Cancel an alert by ID."""
        for a in self.alerts:
            if a.alert_id == alert_id:
                if a.status == AlertStatus.ACTIVE:
                    a.status = AlertStatus.CANCELLED
                    self._persist(a)
                    return True
        return False

    def clear_triggered(self) -> int:
        """Clear all triggered alerts. Returns count cleared."""
        count = 0
        for a in self.alerts:
            if a.status == AlertStatus.TRIGGERED:
                a.status = AlertStatus.CANCELLED
                self._persist(a)
                count += 1
        return count

    def active_count(self) -> int:
        return sum(1 for a in self.alerts if a.status == AlertStatus.ACTIVE)

    def triggered_count(self) -> int:
        return sum(1 for a in self.alerts if a.status == AlertStatus.TRIGGERED)

    def history(self, limit: int = 50) -> list[Alert]:
        """Return triggered alert history sorted by triggered_at descending."""
        triggered = [a for a in self.alerts if a.triggered_at > 0]
        triggered.sort(key=lambda x: x.triggered_at, reverse=True)
        return triggered[:limit]

    def summary(self) -> dict:
        """Return dict with counts by status and type."""
        res = {
            "active": self.active_count(),
            "triggered": self.triggered_count(),
            "cancelled": sum(
                1 for a in self.alerts if a.status == AlertStatus.CANCELLED
            ),
            "expired": sum(1 for a in self.alerts if a.status == AlertStatus.EXPIRED),
            "total": len(self.alerts),
            "by_status": {},
            "by_type": {},
        }
        for a in self.alerts:
            res["by_status"][a.status.value] = (
                res["by_status"].get(a.status.value, 0) + 1
            )
            res["by_type"][a.alert_type.value] = (
                res["by_type"].get(a.alert_type.value, 0) + 1
            )
        return res

    def query_pending_deliveries(self, limit: int = 50) -> list[Alert]:
        """Fetch triggered alerts that are pending external delivery."""
        pending = [
            a
            for a in self.alerts
            if a.delivery_status == "pending" and a.triggered_at > 0
        ]
        self.delivery_backlog_count = len(pending)
        return pending[:limit]

    def mark_delivery_status(
        self,
        alert_id: int,
        status: str,
        attempts: int,
        last_attempt_at: float,
        error: str | None = None,
    ) -> None:
        """Update delivery telemetry and status for an alert."""
        for a in self.alerts:
            if a.alert_id == alert_id:
                a.delivery_status = status
                a.delivery_attempts = attempts
                a.last_attempt_at = last_attempt_at
                a.last_error = error
                if status == "delivered":
                    self.delivery_delivered_count += 1
                elif status == "failed":
                    self.delivery_failed_count += 1
                self._persist(a)
                break
        self.delivery_backlog_count = sum(
            1
            for a in self.alerts
            if a.delivery_status == "pending" and a.triggered_at > 0
        )
