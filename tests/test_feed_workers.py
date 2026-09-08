import os
import sys
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from feed_workers import BaseFeedWorker, MultiFeedManager, SimulatorFeedWorker
from models import RawEvent


class DummyWorker(BaseFeedWorker):
    def __init__(self, name: str, count: int = 10, delay_s: float = 0.01):
        super().__init__(name=name, queue_capacity=1024)
        self.count = count
        self.delay_s = delay_s

    def run(self) -> None:
        self._is_connected = True
        for i in range(self.count):
            if self._stop_event.is_set():
                break
            raw = RawEvent(
                source=self.worker_name,
                payload={"instrument": "TEST", "price": 100.0 + i, "event_type": "TRADE"},
                receive_timestamp=time.time(),
                raw_id=f"{self.worker_name}-{i}",
            )
            self.emit(raw)
            if self.delay_s > 0:
                time.sleep(self.delay_s)


def test_feed_worker_lifecycle():
    w = DummyWorker(name="W1", count=5, delay_s=0.01)
    w.start()
    time.sleep(0.08)

    st = w.stats()
    assert st["name"] == "W1"
    assert st["total_ingested"] >= 4
    assert w.out_queue.size() >= 4

    item = w.out_queue.poll()
    assert item is not None
    assert item.source == "W1"

    w.stop()
    assert not w.is_alive()


def test_multi_feed_manager_multiplexing():
    mgr = MultiFeedManager()
    w1 = DummyWorker(name="BINANCE", count=10, delay_s=0.005)
    w2 = DummyWorker(name="COINBASE", count=10, delay_s=0.005)
    mgr.add_worker(w1)
    mgr.add_worker(w2)

    mgr.start_all()
    time.sleep(0.1)

    collected = []
    while True:
        ev = mgr.poll_next()
        if ev is None:
            break
        collected.append(ev)

    assert len(collected) == 20
    sources = {ev.source for ev in collected}
    assert "BINANCE" in sources
    assert "COINBASE" in sources

    mgr.stop_all()


def test_multi_feed_batch_drain():
    mgr = MultiFeedManager()
    w1 = DummyWorker(name="A", count=15, delay_s=0.001)
    w2 = DummyWorker(name="B", count=15, delay_s=0.001)
    mgr.add_worker(w1)
    mgr.add_worker(w2)

    mgr.start_all()
    time.sleep(0.08)

    batch = []
    drained = mgr.drain_batch(batch, max_items=25)
    assert drained > 0
    assert len(batch) == drained

    mgr.stop_all()


def test_simulator_feed_worker():
    sim_worker = SimulatorFeedWorker(name="SIM-TEST", target_eps=5000.0, total_events=100)
    sim_worker.start()
    time.sleep(0.05)

    ev = sim_worker.out_queue.poll()
    assert ev is not None
    assert ev.payload["event_type"] in ("TRADE", "QUOTE")

    sim_worker.stop()
