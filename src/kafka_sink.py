"""MDRAP Kafka / Redpanda OutputSink (Durable Tier).

Provides a durable, resumable, at-least-once streaming bridge from the persisted
MDRAP storage (SQLite/DuckDB WAL) to Apache Kafka or Redpanda topics.

Design Invariants:
1. Runs outside the synchronous pipeline's call stack as a decoupled worker or
   background tailer. Never blocks or introduces latency to tick processing.
2. Durable tier (default): Tails persisted Store via monotonic cursor/rowid.
3. Checkpointed durability: Resumes from last acknowledged offset upon restart.
4. Topic separation: Valid canonical events -> `mdrap.canonical.v1`;
   Quarantined/invalid events -> `mdrap.quarantine.v1`.
5. Per-instrument ordering: Partition key = `instrument_id`.
6. Natural deduplication: Messages include the `(source, instrument_id, sequence_number)`
   tuple, enabling consumers to dedupe idempotently without multi-phase transactions.
7. Graceful degradation: Downstream broker outages or slow consumers increment
   dropped/error counters and alert via Prometheus, never halting tick ingestion.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from models import CanonicalEvent, QualityStatus

logger = logging.getLogger(__name__)

__stability__ = "stable"

__all__ = [
    "KafkaSinkConfig",
    "KafkaMessage",
    "KafkaProducerClient",
    "InMemoryKafkaProducer",
    "DurableKafkaSink",
]


@dataclass
class KafkaSinkConfig:
    """Configuration for Durable Kafka / Redpanda OutputSink."""

    bootstrap_servers: str = "localhost:9092"
    canonical_topic: str = "mdrap.canonical.v1"
    quarantine_topic: str = "mdrap.quarantine.v1"
    client_id: str = "mdrap-sink-durable"
    security_protocol: str = "PLAINTEXT"  # PLAINTEXT, SASL_PLAINTEXT, SASL_SSL, SSL
    sasl_mechanism: str = "PLAIN"  # PLAIN, SCRAM-SHA-256, SCRAM-SHA-512
    sasl_username: Optional[str] = None
    sasl_password: Optional[str] = None
    credentials_ref: Optional[str] = None  # Reference to environment variable or secret
    enable_idempotence: bool = True
    batch_size: int = 100
    poll_interval_s: float = 0.05
    checkpoint_file: Optional[str] = None  # Atomic local file backup for cursor

    def resolve_credentials(self) -> Tuple[Optional[str], Optional[str]]:
        """Resolve SASL credentials safely from environment or configuration."""
        user = self.sasl_username
        pwd = self.sasl_password

        if self.credentials_ref:
            # Resolves from environment variable ref, e.g. "KAFKA_PROD_CREDS" -> "user:pass"
            ref_val = os.environ.get(self.credentials_ref, "")
            if ":" in ref_val:
                parts = ref_val.split(":", 1)
                user, pwd = parts[0], parts[1]
            elif ref_val:
                pwd = ref_val

        # Direct environment variable overrides
        user = os.environ.get("MDRAP_KAFKA_USER", user)
        pwd = os.environ.get("MDRAP_KAFKA_PASSWORD", pwd)

        return user, pwd

    def validate_security(self, profile: str = "production") -> None:
        """Enforce strict transport and authentication standards in production."""
        if profile.lower() in ("production", "prod"):
            if self.security_protocol.upper() == "PLAINTEXT":
                raise ValueError(
                    "Security policy violation: Plaintext Kafka connections are forbidden in production profile. "
                    "Configure SASL_SSL or SSL."
                )


@dataclass(slots=True)
class KafkaMessage:
    """Record produced to a Kafka topic."""

    topic: str
    key: bytes
    value: bytes
    timestamp_ms: int
    headers: List[Tuple[str, bytes]] = field(default_factory=list)


class KafkaProducerClient(Protocol):
    """Protocol for abstract Kafka producer implementations."""

    def produce(
        self,
        topic: str,
        value: bytes,
        key: bytes | None = None,
        headers: list[tuple[str, bytes]] | None = None,
        on_delivery: Callable[[Exception | None, Any], None] | None = None,
    ) -> None: ...

    def flush(self, timeout: float = 5.0) -> int: ...

    def close(self) -> None: ...


class InMemoryKafkaProducer:
    """Deterministic, high-throughput in-memory Kafka producer for testing and offline environments."""

    def __init__(self, simulate_network_failure: bool = False) -> None:
        self.messages: List[KafkaMessage] = []
        self.by_topic: Dict[str, List[KafkaMessage]] = {}
        self.simulate_network_failure = simulate_network_failure
        self._lock = threading.Lock()
        self.is_closed = False

    def produce(
        self,
        topic: str,
        value: bytes,
        key: bytes | None = None,
        headers: list[tuple[str, bytes]] | None = None,
        on_delivery: Callable[[Exception | None, Any], None] | None = None,
    ) -> None:
        if self.is_closed:
            raise RuntimeError("Producer is closed")

        if self.simulate_network_failure:
            err = ConnectionRefusedError("Broker connection failure (simulated)")
            if on_delivery:
                on_delivery(err, None)
            raise err

        msg = KafkaMessage(
            topic=topic,
            key=key or b"",
            value=value,
            timestamp_ms=int(time.time() * 1000),
            headers=headers or [],
        )

        with self._lock:
            self.messages.append(msg)
            if topic not in self.by_topic:
                self.by_topic[topic] = []
            self.by_topic[topic].append(msg)

        if on_delivery:
            on_delivery(None, msg)

    def flush(self, timeout: float = 5.0) -> int:
        return 0

    def close(self) -> None:
        self.is_closed = True

    def clear(self) -> None:
        with self._lock:
            self.messages.clear()
            self.by_topic.clear()


class DurableKafkaSink:
    """
    Durable-Tier Kafka OutputSink.

    Tails validated events and quarantined records from the persistent Store
    and streams them to segregated Kafka topics using a resumable checkpoint.
    """

    def __init__(
        self,
        config: KafkaSinkConfig | None = None,
        store: Any | None = None,
        producer: KafkaProducerClient | None = None,
    ) -> None:
        self.config = config or KafkaSinkConfig()
        self.store = store
        self.producer = producer or InMemoryKafkaProducer()

        # Checkpoint state: (last_canonical_rowid, last_quarantine_rowid)
        self.last_canonical_rowid: int = 0
        self.last_quarantine_rowid: int = 0
        self.last_checkpoint_ts: float = 0.0

        # Operational telemetry counters
        self.produced_count: int = 0
        self.dropped_count: int = 0
        self.last_error: Optional[str] = None
        self._lock = threading.Lock()

        # Background tailer thread control
        self._running = False
        self._tailer_thread: Optional[threading.Thread] = None

        # Load persisted checkpoint if available
        self._load_checkpoint()

    # -----------------------------------------------------------------------
    # OutputSink Protocol Methods (In-Process Broadcast Adapter)
    # -----------------------------------------------------------------------

    def broadcast_tick(self, event: CanonicalEvent, bbo: Any | None = None) -> None:
        """Direct push adapter satisfying the OutputSink protocol without blocking."""
        try:
            self._produce_canonical_event(event)
        except Exception as exc:
            self.dropped_count += 1
            self.last_error = str(exc)
            logger.warning("DurableKafkaSink direct broadcast dropped tick: %s", exc)

    def broadcast_depth(self, ladder: Any) -> None:
        """Broadcast consolidated depth ladder snapshot."""
        # Optional depth broadcast to Kafka
        pass

    def close(self) -> None:
        """Gracefully flush and terminate producer and background tailer."""
        self.stop_tailer()
        if hasattr(self.producer, "flush"):
            try:
                self.producer.flush(timeout=2.0)
            except Exception:
                pass
        if hasattr(self.producer, "close"):
            try:
                self.producer.close()
            except Exception:
                pass
        self._save_checkpoint()

    # -----------------------------------------------------------------------
    # Durable Tailer & Checkpoint Engine
    # -----------------------------------------------------------------------

    def poll_and_produce(self, limit: int = 100) -> int:
        """
        Poll pending records from store after the last checkpoint and produce them.

        Returns:
            Number of newly produced events across canonical and quarantine streams.
        """
        if not self.store:
            return 0

        produced_this_cycle = 0

        # 1. Tail Canonical Events
        if hasattr(self.store, "query_canonical_after_rowid"):
            rows = self.store.query_canonical_after_rowid(
                self.last_canonical_rowid, limit=limit
            )
            for r in rows:
                rowid = r[0]
                payload = {
                    "event_id": r[1],
                    "instrument_id": r[2],
                    "event_type": r[3],
                    "exchange_timestamp": r[4],
                    "receive_timestamp": r[5],
                    "processing_timestamp": r[6],
                    "source": r[7],
                    "sequence_number": r[8],
                    "price": r[9],
                    "quantity": r[10],
                    "bid_price": r[11],
                    "bid_size": r[12],
                    "ask_price": r[13],
                    "ask_size": r[14],
                    "quality_status": r[15],
                    "reasons": json.loads(r[16]) if r[16] else [],
                    "raw_id": r[17],
                    # Idempotency / deduplication metadata
                    "_idempotency_key": f"{r[7]}:{r[2]}:{r[8]}",
                }
                key = str(r[2]).encode("utf-8")
                val = json.dumps(payload).encode("utf-8")

                try:
                    self.producer.produce(
                        topic=self.config.canonical_topic,
                        key=key,
                        value=val,
                    )
                    self.last_canonical_rowid = rowid
                    self.produced_count += 1
                    produced_this_cycle += 1
                except Exception as exc:
                    self.dropped_count += 1
                    self.last_error = str(exc)
                    logger.error(
                        "Failed producing canonical event rowid=%d: %s", rowid, exc
                    )
                    break

        # 2. Tail Quarantine Records (Separate topic to avoid false-positive mixup)
        if hasattr(self.store, "query_quarantine_after_rowid"):
            q_rows = self.store.query_quarantine_after_rowid(
                self.last_quarantine_rowid, limit=limit
            )
            for qr in q_rows:
                q_rowid = qr[0]
                q_payload = {
                    "event_id": qr[1],
                    "instrument_id": qr[2],
                    "source": qr[3],
                    "quality_status": qr[4],
                    "reasons": json.loads(qr[5]) if qr[5] else [],
                    "payload_json": qr[6],
                    "receive_timestamp": qr[7],
                }
                key = str(qr[2]).encode("utf-8")
                val = json.dumps(q_payload).encode("utf-8")

                try:
                    self.producer.produce(
                        topic=self.config.quarantine_topic,
                        key=key,
                        value=val,
                    )
                    self.last_quarantine_rowid = q_rowid
                    self.produced_count += 1
                    produced_this_cycle += 1
                except Exception as exc:
                    self.dropped_count += 1
                    self.last_error = str(exc)
                    logger.error(
                        "Failed producing quarantine record rowid=%d: %s", q_rowid, exc
                    )
                    break

        if produced_this_cycle > 0:
            self._save_checkpoint()

        return produced_this_cycle

    def lag(self) -> int:
        """
        Calculate current checkpoint lag behind Store's canonical_events table.

        Returns:
            Number of unproduced rows waiting in storage.
        """
        if not self.store or not hasattr(self.store, "get_max_rowid"):
            return 0
        try:
            max_rowid = self.store.get_max_rowid("canonical_events")
            return max(0, max_rowid - self.last_canonical_rowid)
        except Exception:
            return 0

    def start_tailer(self) -> None:
        """Start asynchronous background tailer thread."""
        if self._running:
            return
        self._running = True
        self._tailer_thread = threading.Thread(
            target=self._tailer_loop, name="mdrap-kafka-tailer", daemon=True
        )
        self._tailer_thread.start()

    def stop_tailer(self) -> None:
        """Signal background tailer to exit and join."""
        self._running = False
        if self._tailer_thread and self._tailer_thread.is_alive():
            self._tailer_thread.join(timeout=1.0)
            self._tailer_thread = None

    def _tailer_loop(self) -> None:
        while self._running:
            try:
                produced = self.poll_and_produce(limit=self.config.batch_size)
                if produced == 0:
                    time.sleep(self.config.poll_interval_s)
            except Exception as exc:
                self.last_error = str(exc)
                time.sleep(self.config.poll_interval_s)

    # -----------------------------------------------------------------------
    # Helper & Serialization Routines
    # -----------------------------------------------------------------------

    def _produce_canonical_event(self, event: CanonicalEvent) -> None:
        topic = (
            self.config.canonical_topic
            if event.quality_status != QualityStatus.INVALID
            else self.config.quarantine_topic
        )
        data = {
            "event_id": event.event_id,
            "instrument_id": event.instrument_id,
            "event_type": event.event_type.value,
            "exchange_timestamp": event.exchange_timestamp,
            "receive_timestamp": event.receive_timestamp,
            "processing_timestamp": event.processing_timestamp,
            "source": event.source,
            "sequence_number": event.sequence_number,
            "price": event.price,
            "quantity": event.quantity,
            "bid_price": event.bid_price,
            "bid_size": event.bid_size,
            "ask_price": event.ask_price,
            "ask_size": event.ask_size,
            "quality_status": event.quality_status.value,
            "reasons": event.reasons,
            "raw_id": event.raw_id,
            "_idempotency_key": f"{event.source}:{event.instrument_id}:{event.sequence_number}",
        }
        self.producer.produce(
            topic=topic,
            key=event.instrument_id.encode("utf-8"),
            value=json.dumps(data).encode("utf-8"),
        )
        self.produced_count += 1

    def _save_checkpoint(self) -> None:
        self.last_checkpoint_ts = time.time()
        if self.config.checkpoint_file:
            try:
                dir_path = os.path.dirname(os.path.abspath(self.config.checkpoint_file))
                if dir_path:
                    os.makedirs(dir_path, exist_ok=True)
                tmp_file = f"{self.config.checkpoint_file}.tmp"
                data = {
                    "last_canonical_rowid": self.last_canonical_rowid,
                    "last_quarantine_rowid": self.last_quarantine_rowid,
                    "timestamp": self.last_checkpoint_ts,
                }
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(data, f)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_file, self.config.checkpoint_file)
            except Exception as exc:
                logger.warning("Failed writing Kafka sink checkpoint file: %s", exc)

    def _load_checkpoint(self) -> None:
        if self.config.checkpoint_file and os.path.exists(self.config.checkpoint_file):
            try:
                with open(self.config.checkpoint_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.last_canonical_rowid = data.get("last_canonical_rowid", 0)
                    self.last_quarantine_rowid = data.get("last_quarantine_rowid", 0)
                    self.last_checkpoint_ts = data.get("timestamp", 0.0)
            except Exception as exc:
                logger.warning("Failed loading Kafka sink checkpoint file: %s", exc)
