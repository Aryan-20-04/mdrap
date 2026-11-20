"""MDRAP Data Quality Incident Management & Timeline Correlation.

Tracks, aggregates, and correlates data-quality anomalies into structured
incidents for operational reliability monitoring and deterministic replay.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import sqlite3
import threading
import time
from typing import Any, Optional

from .models import CanonicalEvent

__stability__ = "stable"


@dataclass(slots=True)
class DataQualityIncident:
    """Structured data-quality incident representing an anomaly episode."""

    incident_id: str
    provider: str
    instrument: str
    anomaly_type: str  # "SEQUENCE_GAP", "FEED_STALE", "CLOCK_SKEW", "CROSSED_BOOK", "DISCONNECTION"
    severity: str  # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    start_timestamp: float
    end_timestamp: Optional[float]
    event_ids: list[str] = field(default_factory=list)
    impact_summary: str = ""
    is_active: bool = True
    reproduced: bool = False
    reproduction_hash: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DataQualityIncident:
        return cls(**data)


class IncidentManager:
    """Manages active and historical data-quality incident lifecycles."""

    def __init__(self, db_path: Optional[str] = None, store: Any = None):
        self.store = store
        self.db_path = db_path or (
            getattr(store, "path", None) or getattr(store, "db_path", None)
            if store
            else None
        )
        self._lock = threading.Lock()
        self._active_incidents: dict[tuple[str, str, str], DataQualityIncident] = {}
        self._incident_history: list[DataQualityIncident] = []
        self._counter: int = 0
        if self.store or self.db_path:
            self._init_db()

    def _init_db(self) -> None:
        if self.store and hasattr(self.store, "conn"):
            with self.store._lock:
                self.store.conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS data_quality_incidents (
                        incident_id TEXT PRIMARY KEY,
                        provider TEXT NOT NULL,
                        instrument TEXT NOT NULL,
                        anomaly_type TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        start_timestamp REAL NOT NULL,
                        end_timestamp REAL,
                        event_ids TEXT NOT NULL,
                        impact_summary TEXT,
                        is_active INTEGER NOT NULL,
                        reproduced INTEGER NOT NULL,
                        reproduction_hash TEXT
                    )
                    """
                )
                self.store.conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_incident_prov_inst ON data_quality_incidents(provider, instrument)"
                )
                self.store.conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_incident_start ON data_quality_incidents(start_timestamp)"
                )
                self.store.conn.commit()
            return

        if not self.db_path:
            return
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            conn.execute("PRAGMA busy_timeout=5000;")
            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS data_quality_incidents (
                        incident_id TEXT PRIMARY KEY,
                        provider TEXT NOT NULL,
                        instrument TEXT NOT NULL,
                        anomaly_type TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        start_timestamp REAL NOT NULL,
                        end_timestamp REAL,
                        event_ids TEXT NOT NULL,
                        impact_summary TEXT,
                        is_active INTEGER NOT NULL,
                        reproduced INTEGER NOT NULL,
                        reproduction_hash TEXT
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_incident_prov_inst ON data_quality_incidents(provider, instrument)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_incident_start ON data_quality_incidents(start_timestamp)"
                )
        except Exception:
            pass
        finally:
            if conn:
                conn.close()

    def record_anomaly(
        self,
        event: CanonicalEvent | dict[str, Any],
        anomaly_type: str,
        impact_summary: str = "",
        severity: str = "MEDIUM",
    ) -> DataQualityIncident:
        """Record an anomaly and associate it with an active incident or create a new one."""
        with self._lock:
            src = (
                event.get("source")
                if isinstance(event, dict)
                else getattr(event, "source", "")
            )
            inst = (
                event.get("instrument_id")
                if isinstance(event, dict)
                else getattr(event, "instrument_id", "")
            )
            evt_id = (
                event.get("event_id")
                if isinstance(event, dict)
                else getattr(event, "event_id", "")
            )
            rx_ts = (
                event.get("receive_timestamp")
                if isinstance(event, dict)
                else getattr(event, "receive_timestamp", 0.0)
            )

            key = (src, inst, anomaly_type)
            now = rx_ts or time.time()

            incident = self._active_incidents.get(key)
            if incident and incident.is_active:
                # Extend existing active incident
                incident.end_timestamp = now
                if evt_id and evt_id not in incident.event_ids:
                    incident.event_ids.append(evt_id)
                if impact_summary and impact_summary not in incident.impact_summary:
                    incident.impact_summary += f"; {impact_summary}"
            else:
                # Open a new incident
                self._counter += 1
                incident_id = f"inc-{int(now)}-{self._counter:04d}"
                incident = DataQualityIncident(
                    incident_id=incident_id,
                    provider=src,
                    instrument=inst,
                    anomaly_type=anomaly_type,
                    severity=severity,
                    start_timestamp=now,
                    end_timestamp=now,
                    event_ids=[evt_id] if evt_id else [],
                    impact_summary=impact_summary
                    or f"Detected {anomaly_type} on {src} {inst}",
                    is_active=True,
                )
                self._active_incidents[key] = incident
                self._incident_history.append(incident)

            if self.db_path:
                self._persist_incident(incident)

            return incident

    def close_incident(self, provider: str, instrument: str, anomaly_type: str) -> None:
        """Close an active incident when feed conditions return to normal."""
        with self._lock:
            key = (provider, instrument, anomaly_type)
            inc = self._active_incidents.pop(key, None)
            if inc:
                inc.is_active = False
                inc.end_timestamp = time.time()
                if self.db_path:
                    self._persist_incident(inc)

    def _persist_incident(self, inc: DataQualityIncident) -> None:
        if self.store and hasattr(self.store, "conn"):
            with self.store._lock:
                self.store.conn.execute(
                    """
                    INSERT INTO data_quality_incidents (
                        incident_id, provider, instrument, anomaly_type, severity,
                        start_timestamp, end_timestamp, event_ids, impact_summary,
                        is_active, reproduced, reproduction_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(incident_id) DO UPDATE SET
                        end_timestamp=excluded.end_timestamp,
                        event_ids=excluded.event_ids,
                        impact_summary=excluded.impact_summary,
                        is_active=excluded.is_active,
                        reproduced=excluded.reproduced,
                        reproduction_hash=excluded.reproduction_hash
                    """,
                    (
                        inc.incident_id,
                        inc.provider,
                        inc.instrument,
                        inc.anomaly_type,
                        inc.severity,
                        inc.start_timestamp,
                        inc.end_timestamp,
                        json.dumps(inc.event_ids),
                        inc.impact_summary,
                        1 if inc.is_active else 0,
                        1 if inc.reproduced else 0,
                        inc.reproduction_hash,
                    ),
                )
                self.store.conn.commit()
            return

        if not self.db_path:
            return
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            conn.execute("PRAGMA busy_timeout=5000;")
            with conn:
                conn.execute(
                    """
                    INSERT INTO data_quality_incidents (
                        incident_id, provider, instrument, anomaly_type, severity,
                        start_timestamp, end_timestamp, event_ids, impact_summary,
                        is_active, reproduced, reproduction_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(incident_id) DO UPDATE SET
                        end_timestamp=excluded.end_timestamp,
                        event_ids=excluded.event_ids,
                        impact_summary=excluded.impact_summary,
                        is_active=excluded.is_active,
                        reproduced=excluded.reproduced,
                        reproduction_hash=excluded.reproduction_hash
                    """,
                    (
                        inc.incident_id,
                        inc.provider,
                        inc.instrument,
                        inc.anomaly_type,
                        inc.severity,
                        inc.start_timestamp,
                        inc.end_timestamp,
                        json.dumps(inc.event_ids),
                        inc.impact_summary,
                        1 if inc.is_active else 0,
                        1 if inc.reproduced else 0,
                        inc.reproduction_hash,
                    ),
                )
        except Exception as exc:
            import logging
            logging.getLogger("mdrap.incidents").error("[incidents] _persist_incident exception: %s", exc)
        finally:
            if conn:
                conn.close()

    def query_incidents(
        self,
        limit: int = 50,
        provider: Optional[str] = None,
        instrument: Optional[str] = None,
    ) -> list[DataQualityIncident]:
        """Query historical and active incidents."""
        with self._lock:
            if not self.db_path:
                matches = self._incident_history
                if provider:
                    matches = [
                        i for i in matches if i.provider.upper() == provider.upper()
                    ]
                if instrument:
                    matches = [
                        i for i in matches if i.instrument.upper() == instrument.upper()
                    ]
                return sorted(matches, key=lambda x: x.start_timestamp, reverse=True)[
                    :limit
                ]

        if self.store and hasattr(self.store, "conn"):
            with (self.store._read_lock if hasattr(self.store, "_read_lock") else self.store._lock):
                query = "SELECT * FROM data_quality_incidents"
                params = []
                clauses = []
                if provider:
                    clauses.append("provider = ?")
                    params.append(provider.upper())
                if instrument:
                    clauses.append("instrument = ?")
                    params.append(instrument.upper())
                if clauses:
                    query += " WHERE " + " AND ".join(clauses)
                query += " ORDER BY start_timestamp DESC LIMIT ?"
                params.append(limit)

                c = self.store.read_conn if hasattr(self.store, "read_conn") else self.store.conn
                c.row_factory = sqlite3.Row
                rows = c.execute(query, params).fetchall()
                results = []
                for r in rows:
                    results.append(
                        DataQualityIncident(
                            incident_id=r["incident_id"],
                            provider=r["provider"],
                            instrument=r["instrument"],
                            anomaly_type=r["anomaly_type"],
                            severity=r["severity"],
                            start_timestamp=r["start_timestamp"],
                            end_timestamp=r["end_timestamp"],
                            event_ids=json.loads(r["event_ids"]),
                            impact_summary=r["impact_summary"],
                            is_active=bool(r["is_active"]),
                            reproduced=bool(r["reproduced"]),
                            reproduction_hash=r["reproduction_hash"],
                        )
                    )
                return results

        # Query from SQLite if DB path provided
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.row_factory = sqlite3.Row
            query = "SELECT * FROM data_quality_incidents"
            params = []
            clauses = []
            if provider:
                clauses.append("provider = ?")
                params.append(provider.upper())
            if instrument:
                clauses.append("instrument = ?")
                params.append(instrument.upper())
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY start_timestamp DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            results = []
            for r in rows:
                results.append(
                    DataQualityIncident(
                        incident_id=r["incident_id"],
                        provider=r["provider"],
                        instrument=r["instrument"],
                        anomaly_type=r["anomaly_type"],
                        severity=r["severity"],
                        start_timestamp=r["start_timestamp"],
                        end_timestamp=r["end_timestamp"],
                        event_ids=json.loads(r["event_ids"]),
                        impact_summary=r["impact_summary"],
                        is_active=bool(r["is_active"]),
                        reproduced=bool(r["reproduced"]),
                        reproduction_hash=r["reproduction_hash"],
                    )
                )
            return results
        except Exception:
            return []
        finally:
            if conn:
                conn.close()

    def get_incident(self, incident_id: str) -> Optional[DataQualityIncident]:
        """Fetch a specific incident by its unique ID."""
        with self._lock:
            for inc in self._incident_history:
                if inc.incident_id == incident_id:
                    return inc

        if self.store and hasattr(self.store, "conn"):
            with (self.store._read_lock if hasattr(self.store, "_read_lock") else self.store._lock):
                c = self.store.read_conn if hasattr(self.store, "read_conn") else self.store.conn
                c.row_factory = sqlite3.Row
                r = c.execute(
                    "SELECT * FROM data_quality_incidents WHERE incident_id = ?",
                    (incident_id,),
                ).fetchone()
                if r:
                    return DataQualityIncident(
                        incident_id=r["incident_id"],
                        provider=r["provider"],
                        instrument=r["instrument"],
                        anomaly_type=r["anomaly_type"],
                        severity=r["severity"],
                        start_timestamp=r["start_timestamp"],
                        end_timestamp=r["end_timestamp"],
                        event_ids=json.loads(r["event_ids"]),
                        impact_summary=r["impact_summary"],
                        is_active=bool(r["is_active"]),
                        reproduced=bool(r["reproduced"]),
                        reproduction_hash=r["reproduction_hash"],
                    )

        if not self.db_path:
            return None

        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.row_factory = sqlite3.Row
            r = conn.execute(
                "SELECT * FROM data_quality_incidents WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
            if r:
                return DataQualityIncident(
                    incident_id=r["incident_id"],
                    provider=r["provider"],
                    instrument=r["instrument"],
                    anomaly_type=r["anomaly_type"],
                    severity=r["severity"],
                    start_timestamp=r["start_timestamp"],
                    end_timestamp=r["end_timestamp"],
                    event_ids=json.loads(r["event_ids"]),
                    impact_summary=r["impact_summary"],
                    is_active=bool(r["is_active"]),
                    reproduced=bool(r["reproduced"]),
                    reproduction_hash=r["reproduction_hash"],
                )
        except Exception as exc:
            import logging
            logging.getLogger("mdrap.incidents").error("[incidents] get_incident exception: %s", exc)
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
        return None
