# MDRAP v5 Memory Model: Zero-Allocation Layout & Cache Architecture

## 1. Executive Summary

This document specifies the memory architecture for MDRAP v5.0.

In low-latency systems, dynamic memory management (`malloc`, `free`, and CPython's `pymalloc`) is the primary contributor to latency jitter:
1. Heap fragmentation causes non-deterministic allocation latency ($50\ \text{ns}$ to $> 10\ \mu\text{s}$).
2. Garbage collection sweeps cause stop-the-world pauses.
3. Unaligned memory structures straddle 64-byte L1 cache-line boundaries, triggering dual-line bus locks.
4. Pointer-based object graphs cause frequent L2/L3 cache misses.

MDRAP v5 establishes a **Zero-Allocation Steady-State Memory Contract**:
- All operational memory buffers are pre-allocated at process initialization.
- Ticks are processed in contiguous, 128-byte cache-aligned memory blocks (`NativeSlot`).
- Wire payloads are decoded using zero-copy structural overlays.
- Zero heap allocations (`malloc`, `free`, `new`, PyObject creation) occur during live market data processing.

---

## 2. Memory Alignment & Cache-Line Architecture

Modern x86_64 processors (including the Intel Golden Cove architecture in the Core i7-1255U) utilize **64-byte cache lines**.

When an internal data structure spans across two cache lines, a single memory access requires two separate cache fetches and may trigger split-lock bus transactions if updated atomically.

### 2.1 Two-Cache-Line `NativeSlot` Layout (Exactly 128 Bytes)

`NativeSlot` is engineered to occupy exactly two 64-byte cache lines, organized by field access locality:

```
Cache Line 0 (Offset 0x00 - 0x3F, 64 Bytes): High-Frequency Hotpath
+---------------------------------------------------------------------------------------+
|  0x00: slot_id (8B)        |  0x08: feed_seq (8B)       |  0x10: source_ts_ns (8B)    |
+---------------------------------------------------------------------------------------+
|  0x18: local_ts_ns (8B)    |  0x20: price_scaled (8B)   |  0x28: bid_scaled (8B)      |
+---------------------------------------------------------------------------------------+
|  0x30: ask_scaled (8B)     |  0x38: size (4B) | ch (2B) | side (1B) | status (1B)     |
+---------------------------------------------------------------------------------------+

Cache Line 1 (Offset 0x40 - 0x7F, 64 Bytes): Quality Metadata & Routing
+---------------------------------------------------------------------------------------+
|  0x40: anomaly_mask (4B)   |  0x44: dedup_fp (4B)       |  0x48: symbol (16B)         |
+---------------------------------------------------------------------------------------+
|  0x58: canonical_id (8B)   |  0x60: payload_len (4B)    |  0x64: crc32c (4B)          |
+---------------------------------------------------------------------------------------+
|  0x68: explicit_padding (24B)                                                         |
+---------------------------------------------------------------------------------------+
```

### 2.2 Compilation Verification
```c
_Alignas(64) typedef struct { ... } NativeSlot;
_Static_assert(sizeof(NativeSlot) == 128, "NativeSlot must be exactly 128 bytes");
_Static_assert(offsetof(NativeSlot, anomaly_mask) == 64, "Line 2 must start at offset 64");
```

---

## 3. Wire Overlay Zero-Copy Parsing

Instead of reading socket data into an intermediate buffer and parsing fields into Python dataclasses, MDRAP v5 overlays structured C types directly on top of network socket buffers.

### NASDAQ MoldUDP64 / ITCH 5.0 Example:
```c
// Direct wire overlay without heap allocation
void process_wire_packet(const uint8_t* wire_buf, size_t len) {
    const moldudp64_header_t* mold_hdr = (const moldudp64_header_t*)wire_buf;
    const uint8_t* msg_ptr = wire_buf + sizeof(moldudp64_header_t);

    uint16_t msg_count = ntohs(mold_hdr->message_count);
    uint64_t expected_seq = ring->next_expected_seq;

    for (uint16_t i = 0; i < msg_count; ++i) {
        uint16_t msg_len = ntohs(*(const uint16_t*)msg_ptr);
        const itch_trade_msg_t* itch = (const itch_trade_msg_t*)(msg_ptr + 2);

        // Populate pre-allocated slot
        uint64_t slot_idx = ring->head++ & ring->mask;
        NativeSlot* slot = &ring->slots[slot_idx];

        slot->feed_sequence = expected_seq++;
        slot->price_scaled  = (int64_t)(ntohl(itch->price) * 10000); // 4 decimals -> 8
        slot->size          = ntohl(itch->shares);
        memcpy(slot->symbol, itch->stock, 8);

        // Advance wire pointer
        msg_ptr += 2 + msg_len;
    }
}
```

---

## 4. In-Flight Memory Budgets & Footprint

To ensure the entire hotpath fits comfortably inside the CPU's shared 12 MB L3 cache, buffer capacities are bounded and power-of-two sized:

| Memory Structure | Slot Count | Element Size | Total Allocation | Cache Residency |
|:---|:---:|:---:|:---:|:---|
| **Ingress Hot Ring** | 65,536 | 128 bytes | **8.38 MB** | Fits in L3 cache (12 MB) |
| **SPSC Projection Ring** | 262,144 | 128 bytes | **33.55 MB** | Main RAM (Drained by E-Core 4) |
| **SHM Distribution Ring** | 65,536 | 128 bytes | **8.38 MB** | Shared Memory Page Pool (`/dev/shm`) |
| **Robin-Hood Dedup Table** | 131,072 | 16 bytes | **2.09 MB** | Fits in L3 cache (2 MB footprint) |
| **Channel State Table** | 256 | 64 bytes | **0.016 MB** | Fits in L1 D-Cache (32 KB per core) |
| **Total Steady-State Footprint** | -- | -- | **$\mathbf{52.42\ \text{MB}}$** | Bounded & Pre-Allocated |

---

## 5. False Sharing Prevention & Memory Alignment Rules

1. **Producer-Consumer Pointers Separated by 64 Bytes**:
   - In all SPSC queues, the producer index (`head`) and consumer index (`tail`) are explicitly separated by 64 bytes of padding. This prevents the producer core from invalidating the consumer core's cache line during high-frequency enqueue operations.
2. **Page-Aligned Shared Memory**:
   - The shared memory mapped region (`shm_open` or `CreateFileMapping`) is aligned to the OS 4 KB (or 2 MB huge page) boundary via `VirtualAlloc` / `mmap(MAP_SHARED)`.
3. **Zero Dynamic Allocation Verification**:
   - In automated release tests, `malloc` and `free` symbols are intercepted via dynamic linker wrappers (`LD_PRELOAD` or MSVC CRT hooks) to assert that zero allocation calls are made during steady-state processing of 1,000,000 events.
