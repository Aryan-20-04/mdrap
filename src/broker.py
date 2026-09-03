"""
Streaming Message Broker Abstraction for MDRAP V2.

Provides an abstract EventBroker interface and implementations:
- QueueBroker: Pure Python, zero-dependency, bounded thread-safe in-memory
  streaming bus with queue depth tracking and backpressure signaling.
- KafkaBroker: Adapter for Apache Kafka / Redpanda when an external broker is configured.
"""
from __future__ import annotations

import queue
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class EventBroker(ABC):
    """Abstract message broker for decoupled streaming pipelines."""

    @abstractmethod
    def publish(self, topic: str, item: Any, timeout: Optional[float] = None) -> bool:
        """Publish a single item to a topic. Returns True if accepted, False if dropped/timeout."""
        pass

    @abstractmethod
    def poll(self, topic: str, timeout: Optional[float] = None) -> Optional[Any]:
        """Poll a single item from a topic."""
        pass

    @abstractmethod
    def poll_batch(self, topic: str, max_items: int, timeout: Optional[float] = None) -> List[Any]:
        """Poll up to max_items from a topic."""
        pass

    @abstractmethod
    def depth(self, topic: str) -> int:
        """Current backlog / queue depth for a topic."""
        pass

    @abstractmethod
    def close(self):
        """Cleanly close all channels and release resources."""
        pass


class QueueBroker(EventBroker):
    """
    High-performance in-memory streaming broker.
    
    Features:
    - Bounded topic queues with configurable capacities.
    - Backpressure monitoring via high-watermark thresholds.
    - Low-overhead batch polling.
    """

    def __init__(self, default_capacity: int = 20_000, high_watermark_pct: float = 0.85):
        self.default_capacity = default_capacity
        self.high_watermark_pct = high_watermark_pct
        self.high_watermark = int(default_capacity * high_watermark_pct)
        self._queues: Dict[str, queue.Queue] = {}
        self._max_depths: Dict[str, int] = {}
        self._published_counts: Dict[str, int] = {}
        self._consumed_counts: Dict[str, int] = {}
        self._stalls: int = 0

    def _get_queue(self, topic: str) -> queue.Queue:
        if topic not in self._queues:
            self._queues[topic] = queue.Queue(maxsize=self.default_capacity)
            self._max_depths[topic] = 0
            self._published_counts[topic] = 0
            self._consumed_counts[topic] = 0
        return self._queues[topic]

    def publish(self, topic: str, item: Any, timeout: Optional[float] = None) -> bool:
        q = self._get_queue(topic)
        cur_size = q.qsize()
        if cur_size > self._max_depths[topic]:
            self._max_depths[topic] = cur_size

        try:
            q.put(item, block=True, timeout=timeout)
            self._published_counts[topic] += 1
            return True
        except queue.Full:
            self._stalls += 1
            return False

    def is_backpressure_active(self, topic: str) -> bool:
        """Returns True if the topic backlog exceeds the high watermark."""
        q = self._get_queue(topic)
        return q.qsize() >= self.high_watermark

    def poll(self, topic: str, timeout: Optional[float] = None) -> Optional[Any]:
        q = self._get_queue(topic)
        try:
            item = q.get(block=True, timeout=timeout)
            q.task_done()
            self._consumed_counts[topic] += 1
            return item
        except queue.Empty:
            return None

    def poll_batch(self, topic: str, max_items: int, timeout: Optional[float] = None) -> List[Any]:
        items: List[Any] = []
        first = self.poll(topic, timeout=timeout)
        if first is None:
            return items
        items.append(first)

        q = self._get_queue(topic)
        while len(items) < max_items:
            try:
                item = q.get_nowait()
                q.task_done()
                self._consumed_counts[topic] += 1
                items.append(item)
            except queue.Empty:
                break
        return items

    def depth(self, topic: str) -> int:
        q = self._get_queue(topic)
        return q.qsize()

    def stats(self) -> Dict[str, Any]:
        return {
            topic: {
                "depth": q.qsize(),
                "max_depth": self._max_depths[topic],
                "published": self._published_counts[topic],
                "consumed": self._consumed_counts[topic],
            }
            for topic, q in self._queues.items()
        }

    def close(self):
        self._queues.clear()


class KafkaBroker(EventBroker):
    """
    Pluggable broker adapter for Apache Kafka / Redpanda clusters.
    Requires external confluent_kafka or kafka-python dependency.
    """

    def __init__(self, bootstrap_servers: str = "localhost:9092"):
        self.bootstrap_servers = bootstrap_servers
        try:
            from kafka import KafkaConsumer, KafkaProducer  # type: ignore
            self._producer = KafkaProducer(bootstrap_servers=bootstrap_servers)
            self._consumer_cls = KafkaConsumer
        except ImportError:
            raise ImportError(
                "kafka-python is required to use KafkaBroker. "
                "Install it with: pip install kafka-python"
            )

    def publish(self, topic: str, item: Any, timeout: Optional[float] = None) -> bool:
        import json
        payload = json.dumps(item, default=str).encode("utf-8")
        future = self._producer.send(topic, payload)
        if timeout:
            future.get(timeout=timeout)
        return True

    def poll(self, topic: str, timeout: Optional[float] = None) -> Optional[Any]:
        # Implementation for external broker consumer
        raise NotImplementedError("KafkaBroker polling is configured per consumer group worker.")

    def poll_batch(self, topic: str, max_items: int, timeout: Optional[float] = None) -> List[Any]:
        raise NotImplementedError("KafkaBroker polling is configured per consumer group worker.")

    def depth(self, topic: str) -> int:
        return 0

    def close(self):
        if hasattr(self, "_producer"):
            self._producer.close()
