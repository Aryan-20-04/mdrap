# MDRAP Consolidation — Agent 5: Kafka, PCAP & High-Performance Networking Audit

## 1. Executive Summary

Agent 5 conducted an architectural audit of three high-performance transport and delivery subsystems:
1. **`src/mdrap/kafka_sink.py` (Kafka / Redpanda Output Sink)**
2. **`src/mdrap/pcap.py` (Libpcap 2.4 / MoldUDP64 Multicast Packet Replay)**
3. **`src/mdrap/af_xdp.py` (Linux AF_XDP & Solarflare EF_VI Kernel Bypass)**

A critical architectural mandate of this audit is: **Do not conflate distinct responsibilities**.
- SQLite WAL provides local single-node persistence, crash safety, and local replay.
- Kafka provides multi-node distributed streaming distribution to downstream analytics clusters.
- IngestLog WAL replay provides canonical event replay.
- PCAP provides raw L2/L3/L4 wire packet replay for protocol-level fuzzing and parser validation.

---

## 2. Detailed Subsystem Analysis

### A. Kafka / Redpanda Output Sink (`kafka_sink.py`)
- **Location**: `src/mdrap/kafka_sink.py` (467 lines)
- **Role**:
  - Implements `DurableKafkaSink`, which runs as a decoupled background worker tailing the local SQLite WAL store.
  - Checkpointed cursor persistence: Resumes from last acknowledged offset upon restart.
  - Multi-topic routing: Valid events -> `mdrap.canonical.v1`; Quarantined events -> `mdrap.quarantine.v1`.
  - At-least-once delivery with partition key = `instrument_id`.
  - Zero hard dependencies: Abstract `KafkaProducerClient` protocol with deterministic `InMemoryKafkaProducer` for local testing, offline verification, and fallback.
- **Architectural Distinction vs SQLite WAL**:
  - SQLite WAL is local, synchronous/group-committed, and crash-resilient for the local node.
  - `kafka_sink.py` does NOT replace SQLite WAL; it feeds downstream external consumers.
- **Verdict: MOVE TO OPTIONAL COMPONENT**:
  - Retain `kafka_sink.py` in `mdrap.kafka_sink` as an optional enterprise streaming bridge.
  - Do not add mandatory broker dependencies to the core package.
  - Verified by `tests/test_next_pieces_guarantees.py` and `tests/test_phase19_operability.py`.

### B. PCAP / MoldUDP64 / ITCH Packet Engine (`pcap.py`)
- **Location**: `src/mdrap/pcap.py` (284 lines)
- **Role**:
  - Authentic, pure-Python standard Libpcap 2.4 file reader and writer (`PcapReader`, `PcapWriter`).
  - Zero-copy binary struct unpacking for Ethernet II, IPv4, UDP, and MoldUDP64 multicast protocols.
  - Paced packet replay with microsecond timestamp alignment (`PcapPacer`).
  - Corrupted frame generation for adversarial network chaos testing (truncated frames, packet drops, sequence gaps).
- **Architectural Distinction vs IngestLog WAL Replay**:
  - IngestLog WAL replay reconstructs `CanonicalEvent` structures from normalized disk logs.
  - PCAP replay feeds raw bytes into network socket parsers (e.g. ITCH, SBE) to test wire-level resilience before normalization occurs.
- **Verdict: RETAIN AS OPTIONAL DIAGNOSTIC MODULE**:
  - Keep `src/mdrap/pcap.py` as a diagnostic network test and fuzzing harness.
  - It does not run in the production hot tick path, but enables institutional network packet validation.
  - Verified by `tests/test_phase21_feed_recovery_and_pcap.py` and `tests/test_adversarial_fuzzing.py`.

### C. AF_XDP & EF_VI Kernel Bypass (`af_xdp.py`)
- **Location**: `src/mdrap/af_xdp.py` (227 lines)
- **Role**:
  - Linux `AF_XDP` and Solarflare `EF_VI` constant declarations.
  - Lacks native C bindings, ioctl hooks, or Linux UMEM kernel memory map integration.
  - Only contains an in-memory loopback mock used in a single mock test.
  - Completely non-functional on Windows, macOS, and bare Linux without root XDP drivers.
- **Zero Engine Usage**:
  - Zero modules in `src/mdrap/` import `af_xdp`.
  - Only `tests/test_af_xdp.py` tested the loopback mock.
- **Verdict: DELETE**:
  - Speculative kernel-bypass placeholders must not be retained in a socket-based sidecar.
  - Real low-latency IPC is already achieved through seqlock POSIX/Windows Shared Memory (`src/mdrap/shm.py`, <1µs latency).
