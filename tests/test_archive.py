import sys
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from archive import RawArchive, replay
from models import RawEvent


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


def test_write_and_replay_roundtrip(temp_dir):
    events = []
    for i in range(100):
        source = "FEEDA" if i % 2 == 0 else "FEEDB"
        events.append(
            RawEvent(
                source=source,
                payload={"instrument": "AAPL", "price": 150.0, "type": "TRADE"},
                receive_timestamp=time.time(),
                raw_id=f"evt-{i}",
            )
        )

    with RawArchive(temp_dir, buffer_size=50) as archive:
        for ev in events:
            archive.write(ev)

    replayed = list(replay(temp_dir))
    assert len(replayed) == 100

    replayed_ids = {ev.raw_id for ev in replayed}
    original_ids = {ev.raw_id for ev in events}
    assert replayed_ids == original_ids


def test_date_partitioning(temp_dir):
    ts1 = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    ts2 = datetime(2026, 1, 16, 12, 0, 0, tzinfo=timezone.utc).timestamp()

    ev1 = RawEvent(source="FEEDA", payload={"id": 1}, receive_timestamp=ts1, raw_id="1")
    ev2 = RawEvent(source="FEEDA", payload={"id": 2}, receive_timestamp=ts2, raw_id="2")

    with RawArchive(temp_dir) as archive:
        archive.write(ev1)
        archive.write(ev2)

    dates = os.listdir(temp_dir)
    assert "2026-01-15" in dates
    assert "2026-01-16" in dates
    assert len(dates) == 2


def test_source_partitioning(temp_dir):
    ts = time.time()
    with RawArchive(temp_dir) as archive:
        archive.write(
            RawEvent(source="FEEDA", payload={}, receive_timestamp=ts, raw_id="1")
        )
        archive.write(
            RawEvent(source="FEEDB", payload={}, receive_timestamp=ts, raw_id="2")
        )
        archive.write(
            RawEvent(source="FEEDC", payload={}, receive_timestamp=ts, raw_id="3")
        )

    date_dir = os.listdir(temp_dir)[0]
    full_date_dir = os.path.join(temp_dir, date_dir)
    files = os.listdir(full_date_dir)
    assert "FEEDA.jsonl" in files
    assert "FEEDB.jsonl" in files
    assert "FEEDC.jsonl" in files
    assert len(files) >= 3


def test_buffered_flush_on_close(temp_dir):
    events = [
        RawEvent(
            source="FEEDA", payload={}, receive_timestamp=time.time(), raw_id=str(i)
        )
        for i in range(10)
    ]

    with RawArchive(temp_dir, buffer_size=50) as archive:
        for ev in events:
            archive.write(ev)

    replayed = list(replay(temp_dir))
    assert len(replayed) == 10


def test_replay_filter_by_date(temp_dir):
    ts1 = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    ts2 = datetime(2026, 1, 16, 12, 0, 0, tzinfo=timezone.utc).timestamp()

    with RawArchive(temp_dir) as archive:
        archive.write(
            RawEvent(source="FEEDA", payload={}, receive_timestamp=ts1, raw_id="1")
        )
        archive.write(
            RawEvent(source="FEEDA", payload={}, receive_timestamp=ts2, raw_id="2")
        )

    replayed = list(replay(temp_dir, date="2026-01-15"))
    assert len(replayed) == 1
    assert replayed[0].raw_id == "1"


def test_replay_filter_by_source(temp_dir):
    ts = time.time()
    with RawArchive(temp_dir) as archive:
        archive.write(
            RawEvent(source="FEEDA", payload={}, receive_timestamp=ts, raw_id="1")
        )
        archive.write(
            RawEvent(source="FEEDB", payload={}, receive_timestamp=ts, raw_id="2")
        )
        archive.write(
            RawEvent(source="FEEDC", payload={}, receive_timestamp=ts, raw_id="3")
        )

    replayed = list(replay(temp_dir, source="FEEDA"))
    assert len(replayed) == 1
    assert replayed[0].raw_id == "1"
    assert replayed[0].source == "FEEDA"


def test_stats(temp_dir):
    ts = time.time()
    with RawArchive(temp_dir) as archive:
        archive.write(
            RawEvent(source="FEEDA", payload={}, receive_timestamp=ts, raw_id="1")
        )
        archive.write(
            RawEvent(source="FEEDB", payload={}, receive_timestamp=ts, raw_id="2")
        )
        archive.write(
            RawEvent(source="FEEDA", payload={}, receive_timestamp=ts, raw_id="3")
        )

        stats = archive.stats()
        assert stats is not None
