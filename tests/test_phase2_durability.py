"""Phase 2 Explicit Contract Tests: Kill/Restart, Replay Determinism, Torn Writes, Bit Flips, Restart Dedup."""

import os
import struct
import zlib
import pytest

from mdrap.clock import FixedClock
from mdrap.engine import Engine
from mdrap.ingestlog import (
    IngestLog,
    IngestLogCorruptError,
    FRAME_MAGIC,
    FRAME_HEADER_FORMAT,
    FRAME_HEADER_SIZE,
    SEGMENT_HEADER_SIZE,
)
from mdrap.models import RawEvent
from mdrap.projection import SQLiteProjection


def test_phase2_replay_determinism(tmp_path):
    """Replay determinism: Replay the same log twice. Verify byte-identical canonical rows & identical IDs."""
    log_dir = str(tmp_path / "replay_log")
    log = IngestLog(log_dir=log_dir, fsync_policy="always")

    for i in range(25):
        raw = RawEvent(
            source="NASDAQ",
            payload={"instrument": "MSFT", "price": 400.0 + i, "sequence": i + 1, "exchange_ts": 1700000000.0 + i},
            receive_timestamp=1700000000.0 + i,
        )
        log.append(raw)
    log.close()

    # Pass 1
    db_pass1 = str(tmp_path / "pass1.db")
    proj1 = SQLiteProjection(db_path=db_pass1)
    engine1 = Engine()
    clock1 = FixedClock(1700000000.0)
    state1 = engine1.create_initial_state()

    log1 = IngestLog(log_dir=log_dir)
    batch1 = []
    for off, raw in log1.iter_from(0):
        state1, dec = engine1.step(state1, raw, clock1, offset=off)
        batch1.append(dec)
    proj1.apply(batch1, offset=24)

    # Pass 2
    db_pass2 = str(tmp_path / "pass2.db")
    proj2 = SQLiteProjection(db_path=db_pass2)
    engine2 = Engine()
    clock2 = FixedClock(1700000000.0)
    state2 = engine2.create_initial_state()

    log2 = IngestLog(log_dir=log_dir)
    batch2 = []
    for off, raw in log2.iter_from(0):
        state2, dec = engine2.step(state2, raw, clock2, offset=off)
        batch2.append(dec)
    proj2.apply(batch2, offset=24)

    # Verify identical decisions and canonical records
    assert len(batch1) == len(batch2) == 25
    for d1, d2 in zip(batch1, batch2):
        assert d1.event_id == d2.event_id
        assert d1.canonical_event.event_id == d2.canonical_event.event_id
        assert d1.canonical_event.price == d2.canonical_event.price

    # Verify identical SQLite rows
    rows1 = proj1._conn.execute("SELECT * FROM canonical_events ORDER BY exchange_timestamp ASC").fetchall()
    rows2 = proj2._conn.execute("SELECT * FROM canonical_events ORDER BY exchange_timestamp ASC").fetchall()
    assert rows1 == rows2

    proj1.close()
    proj2.close()
    log1.close()
    log2.close()


def test_phase2_torn_writes(tmp_path):
    """Torn writes: Inject incomplete final frames. Verify only the torn tail is removed."""
    log_dir = str(tmp_path / "torn_write_log")
    log = IngestLog(log_dir=log_dir, fsync_policy="always")

    for i in range(10):
        log.append(RawEvent(source="TEST", payload={"instrument": "TSLA", "price": 250.0 + i}))
    log.close()

    seg_path = log._list_segment_files()[0][1]
    valid_size = os.path.getsize(seg_path)

    # Append a partial frame header
    with open(seg_path, "ab") as f:
        f.write(b"\xaa\x55\x00\x00\x00\x01")  # 6 bytes only

    # Reopen
    recovered_log = IngestLog(log_dir=log_dir)
    assert os.path.getsize(seg_path) == valid_size
    events = list(recovered_log.iter_from(0))
    assert len(events) == 10
    recovered_log.close()


def test_phase2_bit_flips(tmp_path):
    """Bit flips: Corrupt CRC. Verify corruption is detected."""
    log_dir = str(tmp_path / "corrupt_crc_log")
    log = IngestLog(log_dir=log_dir, fsync_policy="always")

    for i in range(10):
        log.append(RawEvent(source="TEST", payload={"instrument": "META", "price": 500.0 + i}))
    log.close()

    seg_path = log._list_segment_files()[0][1]

    # Corrupt payload byte of record 3 in middle of log
    with open(seg_path, "r+b") as f:
        f.seek(SEGMENT_HEADER_SIZE + (FRAME_HEADER_SIZE + 20) * 2)
        b = f.read(1)
        f.seek(-1, os.SEEK_CUR)
        f.write(bytes([b[0] ^ 0xFF]))

    with pytest.raises(IngestLogCorruptError):
        IngestLog(log_dir=log_dir)


def test_phase2_restart_dedup(tmp_path):
    """Restart dedup: Crash after projection application but before checkpoint. Restart. Verify no duplicate rows."""
    db_path = str(tmp_path / "crash_dedup.db")
    proj = SQLiteProjection(db_path=db_path)

    engine = Engine()
    clock = FixedClock(1700000000.0)
    state = engine.create_initial_state()

    raw1 = RawEvent(source="SRC", payload={"instrument": "AMZN", "price": 170.0, "sequence": 1, "exchange_ts": 1700000000.0})
    state, dec1 = engine.step(state, raw1, clock, offset=0)

    # First apply: commits row, advances checkpoint to 0
    proj.apply([dec1], offset=0)
    assert proj.count_canonical() == 1
    assert proj.checkpoint() == 0

    raw2 = RawEvent(source="SRC", payload={"instrument": "AMZN", "price": 171.0, "sequence": 2, "exchange_ts": 1700000001.0})
    state, dec2 = engine.step(state, raw2, clock, offset=1)

    # Simulate crash before checkpoint: insert row directly into SQLite but without advancing projection_checkpoints
    cur = proj._conn.cursor()
    ev = dec2.canonical_event
    cur.execute(
        """
        INSERT OR IGNORE INTO canonical_events (
            event_id, instrument_id, event_type,
            exchange_timestamp, receive_timestamp, processing_timestamp,
            source, sequence_number, price, quantity,
            bid_price, ask_price, bid_size, ask_size,
            quality_status, reasons, raw_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            ev.event_id, ev.instrument_id, ev.event_type.value,
            ev.exchange_timestamp, ev.receive_timestamp, ev.processing_timestamp,
            ev.source, ev.sequence_number, ev.price, ev.quantity,
            ev.bid_price, ev.ask_price, ev.bid_size, ev.ask_size,
            ev.quality_status.value, "[]", str(ev.raw_id),
        ),
    )
    proj._conn.commit()

    # Checkpoint remains at 0!
    assert proj.checkpoint() == 0
    assert proj.count_canonical() == 2

    # Now recovery runs: replays from checkpoint + 1 = 1
    # Projection re-applies dec2!
    proj.apply([dec2], offset=1)

    # Crucial: count must still be exactly 2 (no duplicate row), checkpoint advanced to 1!
    assert proj.count_canonical() == 2
    assert proj.checkpoint() == 1
    proj.close()
