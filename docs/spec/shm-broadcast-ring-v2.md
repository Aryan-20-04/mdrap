# MDRAP Shared Memory Broadcast Ring Specification (v2)

- **Status**: FROZEN (Milestone M1 Contract)
- **Document Version**: 2.0.0
- **Primary Use Case**: Sub-microsecond inter-process communication between `mdrapd` native core and zero-copy consumers (trading bots, risk engines, persistence loggers, Python control plane).

---

## 1. Architectural Architecture

The MDRAP Shared Memory Broadcast Ring is a **lock-free, single-producer multiple-consumer (SPMC)** circular ring buffer backed by OS memory mapping (`/dev/shm` on POSIX, named page-file sections on Windows).

### 1.1 Cache-Line Partitioning
To prevent cache-line bouncing and false sharing:
- **Cache Line 0 (0–63B)**: Read-mostly static metadata (Magic, Version, Slot Size, Slot Count, Epoch ID).
- **Cache Line 1 (64–127B)**: Single-producer writer hot state (`head_seq`, `heartbeat_ns`).
- **Cache Line 2+ (128B+)**: Multi-consumer cursor tracking, telemetry, and per-slot circular payload arenas.

```
+-------------------------------------------------------------------------+
| Cache Line 0 (Read-Mostly Header): Magic, Ver, SlotSz, SlotCnt, EpochID  |
+-------------------------------------------------------------------------+
| Cache Line 1 (Writer State): Head Seq (uint64), Heartbeat (uint64 ns)   |
+-------------------------------------------------------------------------+
| Cache Line 2..N: Telemetry & Overrun Metrics Page                       |
+-------------------------------------------------------------------------+
| Ring Slot 0 (128B): [commit_start_seq | CanonicalRecordV1 (64B) | commit_end_seq]
| Ring Slot 1 (128B): [commit_start_seq | CanonicalRecordV1 (64B) | commit_end_seq]
| ...
| Ring Slot N-1 (128B): ...
+-------------------------------------------------------------------------+
```

---

## 2. Header Layout (64-Byte Cache Line 0 & Line 1)

### 2.1 Cache Line 0: Read-Mostly Header Struct

| Offset | Size (B) | Type | Field Name | Description |
|:---:|:---:|:---:|---|---|
| `0x00` | 8 | `uint64_t` | `magic` | Magic constant: `0x4D4452415053484D` (`MDRAPSHM`) |
| `0x08` | 4 | `uint32_t` | `version` | Protocol version (current: `2`) |
| `0x0C` | 4 | `uint32_t` | `slot_size` | Slot stride in bytes (must be multiple of 64; default `128`) |
| `0x10` | 4 | `uint32_t` | `slot_count` | Ring capacity (must be power of 2; e.g. `65536`) |
| `0x14` | 4 | `uint32_t` | `reserved0` | Padding to maintain 64-bit alignment |
| `0x18` | 8 | `uint64_t` | `epoch_id` | Random 64-bit incarnation ID generated on writer start |
| `0x20` | 32 | `uint8_t[32]`| `reserved_cl0` | Cache line padding to fill 64 bytes |

### 2.2 Cache Line 1: Writer Active State Struct

| Offset | Size (B) | Type | Field Name | Description |
|:---:|:---:|:---:|---|---|
| `0x40` | 8 | `uint64_t` | `head_seq` | Committed head sequence (written with atomic release-store) |
| `0x48` | 8 | `int64_t` | `heartbeat_ns` | Monotonic nanoseconds of publisher liveness |
| `0x50` | 48 | `uint8_t[48]`| `reserved_cl1` | Cache line padding to isolate write invalidations |

---

## 3. Two-Phase Commit Protocol (Per-Slot Verification)

Each ring slot is 128 bytes ($2 \times 64\text{B}$ cache lines) structured as:
```
Offset 0x00: commit_start_seq (uint64_t)
Offset 0x08: Canonical Hot Record v1 (64 bytes)
Offset 0x48: Checksum / reserved (uint64_t)
Offset 0x78: commit_end_seq (uint64_t)
```

### 3.1 Publisher Write Sequence (Release Consistency)
1. Determine target slot index: `slot_idx = target_seq & (slot_count - 1)`.
2. Compute memory offset: `slot_offset = HEADER_SIZE + (slot_idx * slot_size)`.
3. Write `commit_start_seq = target_seq`.
4. Copy `CanonicalRecordV1` into slot payload area (`slot_offset + 8`).
5. Execute memory barrier (`atomic_thread_fence(memory_order_release)` or `_mm_sfence()`).
6. Write `commit_end_seq = target_seq`.
7. Atomic store `head_seq = target_seq + 1` (`memory_order_release`).

### 3.2 Consumer Zero-Copy Read Sequence (Acquire Consistency)
1. Read `head_seq` (`memory_order_acquire`).
2. If `reader_seq >= head_seq`: nothing to read (idle / yield).
3. If `head_seq - reader_seq > slot_count`:
   - **Overrun Detected!** Publisher has lapped the reader.
   - Record `overrun_count++`.
   - Resync `reader_seq = head_seq - 1` (jump to latest committed tick).
4. Read `commit_start_seq` at `slot_offset`.
5. Read `commit_end_seq` at `slot_offset + 0x78`.
6. Verify two-phase consistency:
   $$\text{commit\_start\_seq} == \text{commit\_end\_seq} == \text{reader\_seq}$$
   If mismatched, slot is actively being overwritten by the writer; drop or retry.
7. Read and consume 64-byte payload.
8. Advance `reader_seq++`.

---

## 4. Operating System Semantics & Lifecycle

### 4.1 Linux / POSIX (`/dev/shm`)
- Segments are created via `shm_open(O_CREAT | O_RDWR, 0666)` and sized via `ftruncate()`.
- **Unlink Behavior**: Calling `shm_unlink()` removes the directory entry from `/dev/shm`, but existing memory mappings in reader processes remain valid.
- **Ghost Epoch Prevention (P0-2)**:
  - To detect daemon restarts, readers MUST probe the named segment via `shm_open` during validation checks.
  - If `shm_open` fails or returns a different `epoch_id` than the mapped buffer, the reader immediately detects publisher recreation and triggers a clean remap.

### 4.2 Windows (`Global\` / `Local\`)
- Created via `CreateFileMappingW(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, size, name)`.
- Windows automatically tears down named kernel section objects when the last open handle is closed.
- If the publisher terminates abruptly, readers catch `ERROR_FILE_NOT_FOUND` on reconnection.

---

## 5. Reference Python Reader Implementation

```python
import struct
from multiprocessing.shared_memory import SharedMemory

class SHMReferenceReaderV2:
    def __init__(self, name: str = "mdrap_feed"):
        self.shm = SharedMemory(name=name, create=False)
        # Unpack Cache Line 0
        magic, ver, self.slot_sz, self.slot_cnt, _, self.epoch_id = struct.unpack_from(
            "<QIIIIQ", self.shm.buf, 0
        )
        assert magic == 0x4D4452415053484D, "Invalid SHM magic"
        assert ver == 2, "Unsupported SHM version"
        self.mask = self.slot_cnt - 1
        self.cursor = self.read_head()

    def read_head(self) -> int:
        return struct.unpack_from("<Q", self.shm.buf, 64)[0]

    def poll_next(self) -> bytes | None:
        head = self.read_head()
        if self.cursor >= head:
            return None
        # Check overrun
        if head - self.cursor > self.slot_cnt:
            self.cursor = head - 1  # Fast-forward
        
        offset = 128 + (self.cursor & self.mask) * self.slot_sz
        start_seq = struct.unpack_from("<Q", self.shm.buf, offset)[0]
        end_seq = struct.unpack_from("<Q", self.shm.buf, offset + 120)[0]
        if start_seq != self.cursor or end_seq != self.cursor:
            return None # Torn read, being modified
        
        payload = bytes(self.shm.buf[offset + 8 : offset + 72])
        self.cursor += 1
        return payload
```
