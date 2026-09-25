"""
Tests for MDRAP Append-Only Memory-Mapped Binary Journal (.dbn / AOF).
"""
import os
import tempfile
import pytest

from journal import (
    BinaryJournal,
    BinaryJournalReader,
    JOURNAL_MAGIC,
    JOURNAL_VERSION,
    JOURNAL_HEADER_SIZE,
)
from shm import SLOT_SIZE


def test_journal_create_write_read():
    with tempfile.TemporaryDirectory() as tmpdir:
        journal_path = os.path.join(tmpdir, "events.dbn")

        with BinaryJournal(journal_path, initial_records=1024) as j:
            for i in range(1, 101):
                j.append_tick(
                    seq=i,
                    symbol="BTC/USD",
                    source="FEEDX",
                    price=50000.0 + i,
                    size=1.5,
                    bid=49999.0 + i,
                    ask=50001.0 + i,
                    bid_size=10.0,
                    ask_size=10.0,
                    status="VALID",
                    is_crossed=False,
                    exchange_ts=1000.0 + i,
                    ingest_ts=1000.0 + i + 0.001,
                    broadcast_ts=1000.0 + i + 0.002,
                    engine_us=12.5,
                )
            assert j.record_count == 100
            assert j.first_seq == 1
            assert j.last_seq == 100

        # Read back with BinaryJournalReader
        with BinaryJournalReader(journal_path) as reader:
            assert len(reader) == 100
            assert reader.first_seq == 1
            assert reader.last_seq == 100

            rec0 = reader.read_record(0)
            assert rec0["seq"] == 1
            assert rec0["sym"] == "BTC/USD"
            assert rec0["source"] == "FEEDX"
            assert rec0["price"] == pytest.approx(50001.0)
            assert rec0["status"] == "VALID"
            assert rec0["is_crossed"] is False

            rec99 = reader.read_record(99)
            assert rec99["seq"] == 100
            assert rec99["price"] == pytest.approx(50100.0)

            # Test iteration
            records = list(reader)
            assert len(records) == 100
            assert records[50]["seq"] == 51

            # Test scan_from_seq
            stream = list(reader.scan_from_seq(95))
            assert len(stream) == 6
            assert stream[0]["seq"] == 95
            assert stream[-1]["seq"] == 100


def test_journal_auto_expansion():
    with tempfile.TemporaryDirectory() as tmpdir:
        journal_path = os.path.join(tmpdir, "expand.dbn")

        # Initial records = 1024, write 2500 records to force dynamic mmap resize
        with BinaryJournal(journal_path, initial_records=1024) as j:
            for i in range(1, 2501):
                j.append_tick(
                    seq=i,
                    symbol="ETH/USD",
                    source="KRAKEN",
                    price=3000.0 + (i * 0.1),
                    size=2.0,
                    status="VALID",
                )
            assert j.record_count == 2500

        with BinaryJournalReader(journal_path) as reader:
            assert len(reader) == 2500
            rec = reader.read_record(2499)
            assert rec["seq"] == 2500
            assert rec["sym"] == "ETH/USD"


def test_journal_reopen_and_append():
    with tempfile.TemporaryDirectory() as tmpdir:
        journal_path = os.path.join(tmpdir, "reopen.dbn")

        with BinaryJournal(journal_path, initial_records=1024, epoch=12345) as j1:
            for i in range(1, 51):
                j1.append_tick(seq=i, symbol="SOL/USD", source="COINBASE", price=150.0, size=10.0)

        # Reopen journal and append more events
        with BinaryJournal(journal_path) as j2:
            assert j2.record_count == 50
            assert j2.epoch == 12345
            for i in range(51, 101):
                j2.append_tick(seq=i, symbol="SOL/USD", source="COINBASE", price=155.0, size=5.0)
            assert j2.record_count == 100

        with BinaryJournalReader(journal_path) as reader:
            assert len(reader) == 100
            assert reader.read_record(0)["seq"] == 1
            assert reader.read_record(99)["seq"] == 100


def test_journal_corrupt_file_handling():
    with tempfile.TemporaryDirectory() as tmpdir:
        journal_path = os.path.join(tmpdir, "corrupt.dbn")
        with open(journal_path, "wb") as f:
            f.write(b"BAD_MAGIC_HEADER_THAT_FAILS_VALIDATION")

        with pytest.raises(ValueError, match="Invalid journal magic|file smaller than header"):
            BinaryJournalReader(journal_path)
