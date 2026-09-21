/*
 * ============================================================================
 *  MDRAP fastpath.c -- HARDENING PATCH SET  (drop-in replacement block)
 * ============================================================================
 *
 *  WHAT THIS FILE IS
 *    A complete, compile-tested replacement for the region of src/fastpath.c
 *    that runs from the top of the file (the "MDRAP Native C Hot-Path ..."
 *    comment) through the end of `fastpath_shm_read_slot()` -- i.e. everything
 *    BEFORE the "Simple Binary Encoding (SBE)" section.  The SBE section needs
 *    only the four small edits listed in SBE_HUNKS.txt (they cannot be a
 *    whole-function replacement because upstream lines ~1000-1611 were not
 *    available for review).
 *
 *  COMPATIBILITY
 *    * Every existing exported symbol keeps its name and signature.
 *    * FastEvent / FastResult keep size and field offsets (checked by
 *      _Static_assert).  `present_mask` lives in what used to be 4 bytes of
 *      implicit padding after `event_type`; an old ctypes wrapper that does not
 *      know the field simply leaves it 0 (= legacy "NaN means absent").
 *    * FastEngine is opaque to Python, so its layout is free to change.
 *    * New exports are additive (see the "NEW EXPORTS" list below).
 *
 *  BEFORE APPLYING: grep the untouched tail of fastpath.c (lines ~1000-1611)
 *  for any use of the fields/macros removed here:
 *      last_seq  last_ts  price_stats  dedup_occupied  dedup_keys
 *      FastRollingStats  compute_dedup_key  fnv1a_64
 *  `check_and_insert_dedup()` is kept as a compatibility shim.
 *
 *  PATCH INDEX
 *    C-0  portability + atomics shim, ABI static asserts
 *    C-1  exact sequence de-duplication (64-bit sliding bitmap per slot),
 *         two-generation sliding-window table for un-sequenced events,
 *         seeded word-at-a-time hash (replaces byte-wise FNV-1a),
 *         per-source epochs so a new exchange session can be started cleanly
 *    C-2  lazy slots + pooled rings:  ~277 MB eager  ->  ~25 MB virtual,
 *         RSS proportional to *active* (source, instrument) pairs
 *    C-3  price baseline: only clean events fold in, warm-up minimum sample
 *         count, sigma floor for tick-quantised prices, regime-shift re-seed,
 *         drift-free replace-update Welford, no sqrt / modulo / division
 *    C-4  presence mask, event-type + required-field validation, finite
 *         timestamps, size validation, optional negative prices
 *    C-5  watermark poisoning guards (future timestamp, huge sequence jump)
 *    C-6  replay: range clamp (fixes UINT64_MAX infinite loop), correct
 *         min/max bookkeeping, truncation flag in binary frames
 *    C-7  SHM: seqlock ordering fixed in legacy path; new v3 layout with
 *         aligned header, UNCOMMITTED marker, acquire/release atomics,
 *         length validation, presence + truncation flags
 *    C-8  thread safety: engine spin-lock on exported entry points,
 *         race-free default-engine creation
 *
 *  NEW EXPORTS (all additive)
 *    fastpath_engine_abi_version, fastpath_engine_configure,
 *    fastpath_engine_set_hash_seed, fastpath_engine_source_reset,
 *    fastpath_engine_evaluate_unlocked, fastpath_engine_eval_fast2,
 *    fastpath_engine_counters,
 *    fastpath_shm_init_v3, fastpath_shm_write_tick_v3,
 *    fastpath_shm_read_slot_v3, fastpath_shm_head_v3, fastpath_shm_epoch_v3
 *
 *  NEW REASON BIT (add to src/rules.def, then re-run tools/gen_reasons.py so
 *  Python and C stay in sync; pick the next free bit < 32 -- 24 is only a
 *  placeholder):
 *      RULE_DEF(TS_IMPLAUSIBLE, 24, "exchange timestamp implausibly ahead of receive time")
 * ============================================================================
 */
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define MAX_SOURCES 32
#define MAX_INSTRUMENTS 8192
#define INSTRUMENT_SHIFT 13
#define TOTAL_SLOTS (MAX_SOURCES * MAX_INSTRUMENTS)
#define MAX_WINDOW 128

/* --- C-1: dedup geometry (was: one 262,144-slot table that never expired) --- */
#define DEDUP_GEN_SLOTS 131072u                       /* 2^17 keys per generation   */
#define DEDUP_GEN_MASK  (DEDUP_GEN_SLOTS - 1u)
#define DEDUP_GEN_MAX   (DEDUP_GEN_SLOTS / 2u)        /* rotate at 50% load         */
#define DEDUP_MAX_PROBE 32
#define DEDUP_CACHE_SIZE (2u * DEDUP_GEN_SLOTS)       /* kept for source compat     */
#define DEDUP_MASK      (DEDUP_GEN_SLOTS - 1u)        /* kept for source compat     */

/* --- C-2: pooled price rings --- */
#define RING_CHUNK_RINGS 256u
#define RING_CHUNK_MAX   (TOTAL_SLOTS / RING_CHUNK_RINGS)

/* --- C-3: price corridor constants --- */
#define MIN_SAMPLES_FOR_VARIANCE 3
#define WARMUP_PRICE_DEV_RATIO 0.10

#define FASTPATH_ABI_VERSION 5

// Quality Status
#define STATUS_VALID 0
#define STATUS_SUSPICIOUS 1
#define STATUS_INVALID 2

// Reason Bitmasks (Single source of truth: rules.def)
#define REASON_NONE 0ULL
#define RULE_DEF(name, bit, desc) REASON_##name = (1ULL << (bit)),
enum ReasonBits {
    REASON_BIT_NONE = 0,
#include "rules.def"
};
#undef RULE_DEF
#define CORE_REASON_MASK 0xFFFFFFFFULL
#define USER_REASON_MASK 0xFFFFFFFF00000000ULL

/* ============================================================================
 * C-0  portability + atomics shim
 * ==========================================================================*/
#if defined(_MSC_VER) && !defined(__clang__)
  #include <intrin.h>
  #if !defined(_M_X64) && !defined(_M_AMD64)
    #error "fastpath: MSVC build supports x86-64 only (x86 TSO memory model is assumed)"
  #endif
  #define MD_FENCE_RELEASE()      _ReadWriteBarrier()
  #define MD_FENCE_ACQUIRE()      _ReadWriteBarrier()
  #define MD_STORE_REL_U64(p, v)  do { _ReadWriteBarrier(); *(volatile uint64_t *)(p) = (uint64_t)(v); } while (0)
  #define MD_STORE_RLX_U64(p, v)  (*(volatile uint64_t *)(p) = (uint64_t)(v))
  #define MD_LOAD_ACQ_U64(p)      (_ReadWriteBarrier(), *(volatile const uint64_t *)(p))
  #define MD_LOAD_RLX_U64(p)      (*(volatile const uint64_t *)(p))
  #define MD_LOCK(l)              do { while (_InterlockedExchange((volatile long *)(l), 1)) { _mm_pause(); } } while (0)
  #define MD_UNLOCK(l)            do { _ReadWriteBarrier(); *(volatile long *)(l) = 0; } while (0)
#else
  #define MD_FENCE_RELEASE()      __atomic_thread_fence(__ATOMIC_RELEASE)
  #define MD_FENCE_ACQUIRE()      __atomic_thread_fence(__ATOMIC_ACQUIRE)
  #define MD_STORE_REL_U64(p, v)  __atomic_store_n((uint64_t *)(p), (uint64_t)(v), __ATOMIC_RELEASE)
  #define MD_STORE_RLX_U64(p, v)  __atomic_store_n((uint64_t *)(p), (uint64_t)(v), __ATOMIC_RELAXED)
  #define MD_LOAD_ACQ_U64(p)      __atomic_load_n((const uint64_t *)(p), __ATOMIC_ACQUIRE)
  #define MD_LOAD_RLX_U64(p)      __atomic_load_n((const uint64_t *)(p), __ATOMIC_RELAXED)
  #if defined(__x86_64__) || defined(__i386__)
    #define MD_CPU_RELAX()        __builtin_ia32_pause()
  #elif defined(__aarch64__)
    #define MD_CPU_RELAX()        __asm__ __volatile__("yield" ::: "memory")
  #else
    #define MD_CPU_RELAX()        __asm__ __volatile__("" ::: "memory")
  #endif
  #define MD_LOCK(l)              do { while (__atomic_exchange_n((l), 1, __ATOMIC_ACQUIRE)) { MD_CPU_RELAX(); } } while (0)
  #define MD_UNLOCK(l)            __atomic_store_n((l), 0, __ATOMIC_RELEASE)
#endif

/* --- C-4: presence mask (occupies the 4 bytes of former implicit padding) --- */
#define FE_MASK_VALID 0x80000000u   /* set by wrapper => bits below are authoritative */
#define FE_HAS_PRICE  0x01u
#define FE_HAS_QTY    0x02u
#define FE_HAS_BID    0x04u
#define FE_HAS_ASK    0x08u
#define FE_HAS_BID_SZ 0x10u
#define FE_HAS_ASK_SZ 0x20u

#pragma pack(push, 8)
typedef struct {
    int32_t source_id;
    int32_t instrument_id;
    int32_t event_type;      // 0 = TRADE, 1 = QUOTE
    uint32_t present_mask;   // NEW (was padding). 0 => legacy semantics: NAN means "absent"
    double exchange_ts;
    double receive_ts;
    int64_t sequence_num;    // -1 if not present
    double price;            // NAN if None (legacy mode)
    double quantity;
    double bid_price;
    double ask_price;
    double bid_size;
    double ask_size;
} FastEvent;

typedef struct {
    int32_t status;
    uint32_t _reserved;      // Explicit 4-byte padding for 8-byte boundary
    uint64_t reason_mask;
} FastResult;
#pragma pack(pop)

_Static_assert(sizeof(FastEvent) == 88, "FastEvent ABI size changed");
_Static_assert(offsetof(FastEvent, exchange_ts) == 16, "FastEvent ABI offset changed");
_Static_assert(offsetof(FastEvent, ask_size) == 80, "FastEvent ABI offset changed");
_Static_assert(sizeof(FastResult) == 16, "FastResult ABI size changed");

/* --- C-2: one 96-byte slot per (source, instrument); zero-filled == "untouched" --- */
#define SLOT_INIT    0x1u
#define SLOT_HAS_SEQ 0x2u
#define SLOT_HAS_TS  0x4u
#define SLOT_NO_RING 0x8u
typedef struct {
    int64_t  last_seq;       /* valid iff SLOT_HAS_SEQ                                  */
    uint64_t cand_plus1;     /* C-5: pending resync candidate (+1; 0 = none)            */
    uint64_t seen;           /* C-1: bit i set => (last_seq - i) already seen           */
    double   last_ts;        /* valid iff SLOT_HAS_TS                                   */
    double   mean;
    double   m2;
    double   anom_last;
    double  *ring;           /* lazily taken from the engine ring pool                  */
    uint32_t epoch;          /* source epoch this slot state belongs to                 */
    uint32_t flags;
    int32_t  n;
    int32_t  head;
    int32_t  anom_count;
    uint32_t fold_count;
    uint64_t _pad;
} FastSlot;
_Static_assert(sizeof(FastSlot) == 96, "FastSlot layout");

// ============================================================================
// Phase F: High-Performance Native C Circular Replay Buffer (Spec §18)
// ============================================================================
#define REPLAY_RING_SIZE 65536 // Power of 2 (2^16 slots)
#define REPLAY_RING_MASK (REPLAY_RING_SIZE - 1)

#pragma pack(push, 1)
typedef struct {
    uint64_t seq;
    char symbol[16];
    char source[16];
    char event_type[8]; // "TRADE", "QUOTE", "DEPTH"
    double price;
    double size;
    double bid;
    double ask;
    double bid_size;
    double ask_size;
    uint8_t status;     // 1=VALID, 2=SUSPICIOUS, 3=INVALID
    uint8_t is_crossed; // 1 or 0
    double exchange_ts;
    double ingest_ts;
    double broadcast_ts;
    double engine_us;
    uint8_t is_valid;
} FastReplayRecord;

// Exact 92-byte MDRAP-BIN V1 TICK Frame matching protocol.py
typedef struct {
    char magic[2];       // 'M', 'D'
    uint8_t msg_type;    // 1 (MSG_TYPE_TICK)
    uint8_t payload_len; // 88
    uint64_t seq;
    uint8_t status;
    uint8_t is_crossed;
    char pad[2];         // pad[0]: bit0 = symbol truncated, bit1 = source truncated  (C-6)
    float engine_us;
    double exchange_ts;
    double ingest_ts;
    double broadcast_ts;
    double price;
    double size;
    double bid;
    double ask;
    char symbol[8];
    char source[8];
} FastBinTickFrame;
#pragma pack(pop)

typedef struct FastEngine {
    /* --- upstream configuration --- */
    double staleness_threshold_s;
    double price_anomaly_stddev;
    int32_t price_window;
    /* --- new tunables (set with fastpath_engine_configure) --- */
    int32_t price_min_samples;     /* default 20  : no sigma test before this many samples   */
    int32_t price_reseed_after;    /* default 8   : consecutive consistent anomalies => new regime (0 = off) */
    double  price_sigma_floor_rel; /* default 2e-4: sigma >= 2 bp of |mean| (tick-quantised prices) */
    double  price_reseed_band_rel; /* default 0.01: "consistent" = within 1% of previous anomaly */
    double  max_future_skew_s;     /* default 1.0 : exchange_ts may lead receive_ts by this much */
    int64_t seq_jump_limit;        /* default 1<<24 */
    int32_t unseq_dup_status;      /* default STATUS_SUSPICIOUS (un-sequenced "duplicates" are ambiguous) */
    int32_t allow_negative;        /* default 0   : 1 => negative price/bid/ask allowed (WTI 2020, spreads) */
    uint64_t hash_seed;
    volatile int32_t lock;
    uint32_t dedup_active;
    uint32_t dedup_count;
    uint32_t ring_chunk_count;
    uint32_t ring_in_chunk;
    uint64_t stat_ring_oom;
    uint64_t stat_dedup_rotations;
    uint32_t source_epoch[MAX_SOURCES];
    FastSlot *slots;               /* TOTAL_SLOTS zero-filled slots (lazy pages) */
    double inv_n[MAX_WINDOW + 1];
    double *ring_chunk[RING_CHUNK_MAX];
    uint64_t dedup_keys[2][DEDUP_GEN_SLOTS];   /* 0 == empty */
    FastReplayRecord replay_ring[REPLAY_RING_SIZE];
    uint64_t replay_min_seq;
    uint64_t replay_max_seq;
    uint64_t replay_total_recorded;
} FastEngine;

static inline uint32_t get_slot(int32_t s_id, int32_t i_id) {
    return (uint32_t)((s_id << INSTRUMENT_SHIFT) | i_id);
}

/* ============================================================================
 * C-1  hashing + two-generation sliding-window table
 * ==========================================================================*/
static inline uint64_t mix64(uint64_t x) {           /* splitmix64 finaliser */
    x ^= x >> 30; x *= 0xbf58476d1ce4e5b9ULL;
    x ^= x >> 27; x *= 0x94d049bb133111ebULL;
    x ^= x >> 31;
    return x;
}
/* one multiply per 64-bit word (was: one multiply per BYTE, serial dependency) */
static inline uint64_t hcomb(uint64_t h, uint64_t v) {
    h = (h ^ v) * 0x9E3779B97F4A7C15ULL;
    return h ^ (h >> 29);
}
static inline uint64_t dbits(double d) {             /* canonical bit pattern: -0 == +0, one NaN */
    uint64_t u;
    if (d != d) return 0x7ff8000000000000ULL;
    memcpy(&u, &d, sizeof u);
    if ((u << 1) == 0) return 0;
    return u;
}
static inline uint64_t hbytes(uint64_t h, const void *p, size_t n) {
    const uint8_t *b = (const uint8_t *)p;
    while (n >= 8) { uint64_t v; memcpy(&v, b, 8); h = hcomb(h, v); b += 8; n -= 8; }
    if (n) { uint64_t v = 0; memcpy(&v, b, n); h = hcomb(h, v); }
    return h;
}

/* Key for events WITHOUT an exchange sequence number: exact field equality
 * (full double bit patterns, not microsecond-rounded), scoped by source epoch. */
static inline uint64_t unseq_dedup_key(const FastEngine *eng, const FastEvent *ev, uint32_t epoch) {
    uint64_t h = eng->hash_seed;
    h = hcomb(h, ((uint64_t)(uint32_t)ev->source_id << 32) | (uint32_t)ev->instrument_id);
    h = hcomb(h, ((uint64_t)epoch << 32) | (uint32_t)ev->event_type);
    h = hcomb(h, dbits(ev->exchange_ts));
    if (ev->event_type == 1) {
        h = hcomb(h, dbits(ev->bid_price)); h = hcomb(h, dbits(ev->ask_price));
        h = hcomb(h, dbits(ev->bid_size));  h = hcomb(h, dbits(ev->ask_size));
    } else {
        h = hcomb(h, dbits(ev->price));     h = hcomb(h, dbits(ev->quantity));
    }
    return mix64(h);
}

/* Returns 1 if `key` was seen within the last ~[65k, 131k] insertions, else inserts and returns 0.
 * Two open-addressed generations: lookups probe both, inserts go to the active one; when it
 * reaches 50% load the OLDER generation is wiped and becomes the new active one.  This is a true
 * sliding window (bounded memory, bounded probe length, no permanent entries). */
static inline int dedup_check_insert(FastEngine *eng, uint64_t key) {
    if (key == 0) key = 1;                           /* 0 marks an empty slot */
    uint64_t *act = eng->dedup_keys[eng->dedup_active];
    uint64_t *old = eng->dedup_keys[eng->dedup_active ^ 1u];
    const uint32_t idx = (uint32_t)key & DEDUP_GEN_MASK;
    int hit = 0;
    for (int i = 0; i < DEDUP_MAX_PROBE; ++i) {
        uint64_t k = old[(idx + (uint32_t)i) & DEDUP_GEN_MASK];
        if (k == key) { hit = 1; break; }
        if (k == 0) break;
    }
    uint32_t free_slot = DEDUP_GEN_SLOTS;            /* sentinel: none found */
    for (int i = 0; i < DEDUP_MAX_PROBE; ++i) {
        uint32_t s = (idx + (uint32_t)i) & DEDUP_GEN_MASK;
        uint64_t k = act[s];
        if (k == key) return 1;                      /* already in the active generation */
        if (k == 0) { free_slot = s; break; }
    }
    /* new key, or a key that only lives in the older generation: (re)insert into the active one
     * so a key that keeps recurring never ages out of the window */
    act[free_slot == DEDUP_GEN_SLOTS ? idx : free_slot] = key;
    if (++eng->dedup_count >= DEDUP_GEN_MAX) {
        eng->dedup_active ^= 1u;
        memset(eng->dedup_keys[eng->dedup_active], 0, sizeof eng->dedup_keys[0]);
        eng->dedup_count = 0;
        eng->stat_dedup_rotations++;
    }
    return hit;
}
/* compatibility shim for any remaining upstream call sites */
static inline int check_and_insert_dedup(FastEngine *eng, uint64_t key) { return dedup_check_insert(eng, key); }

static inline void mark(FastResult *res, int32_t status, uint64_t reason) {
    if (status > res->status) {
        res->status = status;
    }
    // Strict enforcement: native C engine never sets bits >= 32 (reserved for user rules in Python)
    res->reason_mask |= (reason & CORE_REASON_MASK);
}

// Exported C Functions
#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif

/* ============================================================================
 * C-2  slots + pooled rings
 * ==========================================================================*/
static double *ring_alloc(FastEngine *e) {
    if (e->ring_chunk_count == 0 || e->ring_in_chunk >= RING_CHUNK_RINGS) {
        if (e->ring_chunk_count >= RING_CHUNK_MAX) return NULL;
        double *c = (double *)malloc(sizeof(double) * (size_t)RING_CHUNK_RINGS * (size_t)e->price_window);
        if (!c) return NULL;
        e->ring_chunk[e->ring_chunk_count++] = c;
        e->ring_in_chunk = 0;
    }
    double *base = e->ring_chunk[e->ring_chunk_count - 1];
    return base + (size_t)(e->ring_in_chunk++) * (size_t)e->price_window;
}
static inline double *slot_ring(FastEngine *e, FastSlot *sl) {
    if (sl->ring) return sl->ring;
    if (sl->flags & SLOT_NO_RING) return NULL;
    sl->ring = ring_alloc(e);
    if (!sl->ring) { sl->flags |= SLOT_NO_RING; e->stat_ring_oom++; }
    return sl->ring;
}
/* Lazily (re)initialise a slot when it is first touched or its source epoch moved on. */
static inline FastSlot *slot_touch(FastEngine *e, uint32_t s_id, uint32_t i_id) {
    FastSlot *sl = &e->slots[get_slot((int32_t)s_id, (int32_t)i_id)];
    const uint32_t ep = e->source_epoch[s_id];
    if (!(sl->flags & SLOT_INIT) || sl->epoch != ep) {
        double *ring = sl->ring;                     /* keep the pooled ring across resets */
        memset(sl, 0, sizeof *sl);
        sl->ring = ring;
        sl->epoch = ep;
        sl->flags = SLOT_INIT;
    }
    return sl;
}

/* ============================================================================
 * C-3  drift-free rolling baseline
 * ==========================================================================*/
static inline void recompute_stats(FastSlot *sl, const double *ring) {
    const int n = sl->n;
    if (n <= 0) { sl->mean = 0.0; sl->m2 = 0.0; return; }
    double s = 0.0;
    for (int i = 0; i < n; ++i) s += ring[i];
    const double mean = s / (double)n;
    double m2 = 0.0;
    for (int i = 0; i < n; ++i) { const double d = ring[i] - mean; m2 += d * d; }
    sl->mean = mean; sl->m2 = m2;
}
static inline void fold_price(const FastEngine *e, FastSlot *sl, double *ring, double x) {
    const int W = e->price_window;
    if (sl->n >= W) {
        /* sliding window: single-step "replace" update (no add-then-remove cancellation) */
        const double old = ring[sl->head];
        const double d = x - old;
        const double nm = sl->mean + d * e->inv_n[W];
        sl->m2 += d * ((x - nm) + (old - sl->mean));
        sl->mean = nm;
        ring[sl->head] = x;
        if (++sl->head >= W) sl->head = 0;           /* no integer modulo */
        if (sl->m2 < 0.0) sl->m2 = 0.0;
    } else {
        ring[sl->n] = x;                             /* while filling, head == n */
        sl->n++;
        const double delta = x - sl->mean;
        sl->mean += delta * e->inv_n[sl->n];
        sl->m2 += delta * (x - sl->mean);
        sl->head = (sl->n >= W) ? 0 : sl->n;
    }
    if (++sl->fold_count >= (uint32_t)W * 64u) {     /* bound floating-point drift */
        sl->fold_count = 0;
        recompute_stats(sl, ring);
    }
}

/* ============================================================================
 * C-1 / C-5  exact sequence tracking with poisoning guards
 * ==========================================================================*/
static inline void seq_check(const FastEngine *e, FastSlot *sl, int64_t seq, FastResult *res) {
    if (!(sl->flags & SLOT_HAS_SEQ)) {
        sl->last_seq = seq; sl->seen = 1ULL; sl->flags |= SLOT_HAS_SEQ;
        return;
    }
    if (seq > sl->last_seq) {
        const int64_t jump = seq - sl->last_seq;     /* both >= 0 -> cannot overflow */
        if (jump > e->seq_jump_limit) {
            /* implausible jump: flag it, but adopt the new level only if the NEXT event
             * confirms it (one bogus 2^62 sequence must not disable gap detection forever) */
            mark(res, STATUS_SUSPICIOUS, REASON_SEQUENCE_GAP);
            const int64_t cand = (int64_t)(sl->cand_plus1 - 1u);
            if (sl->cand_plus1 != 0u && seq > cand && (seq - cand) <= e->seq_jump_limit) {
                sl->last_seq = seq; sl->seen = 1ULL; sl->cand_plus1 = 0u;
            } else {
                sl->cand_plus1 = (uint64_t)seq + 1u;
            }
            return;
        }
        if (jump > 1) mark(res, STATUS_SUSPICIOUS, REASON_SEQUENCE_GAP);
        sl->seen = (jump < 64) ? ((sl->seen << jump) | 1ULL) : 1ULL;
        sl->last_seq = seq;
        sl->cand_plus1 = 0u;
        return;
    }
    const uint64_t d = (uint64_t)(sl->last_seq - seq);
    if (d < 64u) {
        if ((sl->seen >> d) & 1ULL) {
            mark(res, STATUS_INVALID, REASON_DUPLICATE);            /* exact duplicate */
        } else {
            sl->seen |= (1ULL << d);                                /* late but new */
            mark(res, STATUS_SUSPICIOUS, REASON_OUT_OF_ORDER);
        }
    } else {
        mark(res, STATUS_SUSPICIOUS, REASON_OUT_OF_ORDER);          /* older than window: cannot tell dup from new session */
    }
}

/* ============================================================================
 * Engine lifecycle
 * ==========================================================================*/
EXPORT int32_t fastpath_engine_abi_version(void) { return FASTPATH_ABI_VERSION; }

EXPORT FastEngine *fastpath_engine_create(double staleness, double stddev, int32_t window) {
    FastEngine *eng = (FastEngine *)calloc(1, sizeof(FastEngine));
    if (!eng) return NULL;
    eng->staleness_threshold_s = staleness > 0.0 ? staleness : 0.05;
    eng->price_anomaly_stddev = stddev > 0.0 ? stddev : 6.0;
    eng->price_window = window > MAX_WINDOW ? MAX_WINDOW : (window > 0 ? window : 50);
    if (eng->price_window < 2) eng->price_window = 2;

    eng->price_min_samples = eng->price_window < 20 ? eng->price_window : 20;
    eng->price_reseed_after = 8;
    eng->price_sigma_floor_rel = 2e-4;
    eng->price_reseed_band_rel = 0.01;
    eng->max_future_skew_s = 1.0;
    eng->seq_jump_limit = (int64_t)1 << 24;
    eng->unseq_dup_status = STATUS_SUSPICIOUS;
    eng->allow_negative = 0;
    eng->hash_seed = mix64((uint64_t)(uintptr_t)eng ^ ((uint64_t)time(NULL) * 0x9E3779B97F4A7C15ULL) ^ (uint64_t)clock());
    for (int i = 1; i <= MAX_WINDOW; ++i) eng->inv_n[i] = 1.0 / (double)i;

    eng->slots = (FastSlot *)calloc((size_t)TOTAL_SLOTS, sizeof(FastSlot));   /* lazy zero pages */
    if (!eng->slots) { free(eng); return NULL; }
    return eng;
}

EXPORT void fastpath_engine_destroy(FastEngine *eng) {
    if (!eng) return;
    for (uint32_t i = 0; i < eng->ring_chunk_count; ++i) free(eng->ring_chunk[i]);
    free(eng->slots);
    free(eng);
}

EXPORT void fastpath_engine_reset(FastEngine *eng) {
    if (!eng) return;
    MD_LOCK(&eng->lock);
    for (int s = 0; s < MAX_SOURCES; ++s) eng->source_epoch[s]++;   /* O(1): slots reinitialise lazily */
    memset(eng->dedup_keys, 0, sizeof(eng->dedup_keys));
    eng->dedup_active = 0; eng->dedup_count = 0;
    memset(eng->replay_ring, 0, sizeof(eng->replay_ring));
    eng->replay_min_seq = 0;
    eng->replay_max_seq = 0;
    eng->replay_total_recorded = 0;
    MD_UNLOCK(&eng->lock);
}

/* Start a fresh exchange session for one source (sequence numbers restarted, daily reset, ...). */
EXPORT void fastpath_engine_source_reset(FastEngine *eng, int32_t source_id) {
    if (!eng || source_id < 0 || source_id >= MAX_SOURCES) return;
    MD_LOCK(&eng->lock);
    eng->source_epoch[source_id]++;
    MD_UNLOCK(&eng->lock);
}

EXPORT void fastpath_engine_set_hash_seed(FastEngine *eng, uint64_t seed) {
    if (!eng) return;
    MD_LOCK(&eng->lock); eng->hash_seed = mix64(seed); MD_UNLOCK(&eng->lock);
}

/* Any argument <0 (or NaN for doubles) leaves that setting unchanged. */
EXPORT void fastpath_engine_configure(FastEngine *eng,
    int32_t price_min_samples, int32_t price_reseed_after,
    double price_sigma_floor_rel, double price_reseed_band_rel,
    double max_future_skew_s, int64_t seq_jump_limit,
    int32_t unseq_dup_status, int32_t allow_negative)
{
    if (!eng) return;
    MD_LOCK(&eng->lock);
    if (price_min_samples >= 0) eng->price_min_samples = price_min_samples > eng->price_window ? eng->price_window : price_min_samples;
    if (price_reseed_after >= 0) eng->price_reseed_after = price_reseed_after;
    if (price_sigma_floor_rel >= 0.0) eng->price_sigma_floor_rel = price_sigma_floor_rel;
    if (price_reseed_band_rel >= 0.0) eng->price_reseed_band_rel = price_reseed_band_rel;
    if (max_future_skew_s >= 0.0) eng->max_future_skew_s = max_future_skew_s;
    if (seq_jump_limit > 0) eng->seq_jump_limit = seq_jump_limit;
    if (unseq_dup_status == STATUS_SUSPICIOUS || unseq_dup_status == STATUS_INVALID) eng->unseq_dup_status = unseq_dup_status;
    if (allow_negative == 0 || allow_negative == 1) eng->allow_negative = allow_negative;
    MD_UNLOCK(&eng->lock);
}

EXPORT void fastpath_engine_counters(const FastEngine *eng, uint64_t *out /* [3]: ring_oom, dedup_rotations, ring_chunks */) {
    if (!eng || !out) return;
    out[0] = eng->stat_ring_oom; out[1] = eng->stat_dedup_rotations; out[2] = eng->ring_chunk_count;
}

/* ============================================================================
 * C-3/C-4/C-5  evaluation
 * ==========================================================================*/
static inline int fld_present(uint32_t pm, int use_mask, uint32_t bit, double v) {
    return use_mask ? ((pm & bit) != 0u) : !isnan(v);
}
static inline int num_bad(double v, int allow_neg) {
    return !isfinite(v) || (!allow_neg && v < 0.0);
}

static void evaluate_nolock(FastEngine *eng, const FastEvent *ev, FastResult *res) {
    res->status = STATUS_VALID;
    res->_reserved = 0;                              /* was left uninitialised */
    res->reason_mask = REASON_NONE;
    if (!eng || !ev || !eng->slots) { mark(res, STATUS_INVALID, REASON_SCHEMA_VIOLATION); return; }

    const int32_t s_id = ev->source_id;
    const int32_t i_id = ev->instrument_id;
    if (s_id < 0 || s_id >= MAX_SOURCES || i_id < 0 || i_id >= MAX_INSTRUMENTS) {
        mark(res, STATUS_INVALID, REASON_SCHEMA_VIOLATION);
        return;
    }
    if (ev->event_type != 0 && ev->event_type != 1) {          /* C-4: event_type was never validated */
        mark(res, STATUS_INVALID, REASON_SCHEMA_VIOLATION);
        return;
    }

    FastSlot *sl = slot_touch(eng, (uint32_t)s_id, (uint32_t)i_id);

    const uint32_t pm = ev->present_mask;
    const int use_mask = (pm & FE_MASK_VALID) != 0u;
    const int has_price = fld_present(pm, use_mask, FE_HAS_PRICE,  ev->price);
    const int has_qty   = fld_present(pm, use_mask, FE_HAS_QTY,    ev->quantity);
    const int has_bid   = fld_present(pm, use_mask, FE_HAS_BID,    ev->bid_price);
    const int has_ask   = fld_present(pm, use_mask, FE_HAS_ASK,    ev->ask_price);
    const int has_bsz   = fld_present(pm, use_mask, FE_HAS_BID_SZ, ev->bid_size);
    const int has_asz   = fld_present(pm, use_mask, FE_HAS_ASK_SZ, ev->ask_size);
    const int allow_neg = eng->allow_negative;

    /* --- C-4: structural validation ------------------------------------------------ */
    int bad = 0;
    if (ev->event_type == 0) { if (!has_price) bad = 1; }              /* TRADE needs a price   */
    else                     { if (!has_bid && !has_ask) bad = 1; }    /* QUOTE needs a side    */
    if ((has_price && num_bad(ev->price,     allow_neg)) ||
        (has_bid   && num_bad(ev->bid_price, allow_neg)) ||
        (has_ask   && num_bad(ev->ask_price, allow_neg)) ||
        (has_qty   && num_bad(ev->quantity,  0))         ||
        (has_bsz   && num_bad(ev->bid_size,  0))         ||
        (has_asz   && num_bad(ev->ask_size,  0)))
        bad = 1;
    const int ex_fin = isfinite(ev->exchange_ts) != 0;
    const int rc_fin = isfinite(ev->receive_ts) != 0;
    if (!ex_fin || !rc_fin) bad = 1;                                   /* NaN/inf timestamps    */
    if (bad) mark(res, STATUS_INVALID, REASON_SCHEMA_VIOLATION);

    /* --- C-1: sequence / duplicate detection ---------------------------------------- */
    if (ev->sequence_num >= 0) {
        seq_check(eng, sl, ev->sequence_num, res);
    } else if (ex_fin) {
        if (dedup_check_insert(eng, unseq_dedup_key(eng, ev, sl->epoch)))
            mark(res, eng->unseq_dup_status, REASON_DUPLICATE);
    }

    /* --- C-5: timestamp watermark, never advanced by implausible values --------------- */
    if (ex_fin && rc_fin) {
        const int plausible = ev->exchange_ts <= ev->receive_ts + eng->max_future_skew_s;
#ifndef FASTPATH_NO_TS_REASON      /* define only if you have NOT added TS_IMPLAUSIBLE to rules.def */
        if (!plausible) mark(res, STATUS_SUSPICIOUS, REASON_TS_IMPLAUSIBLE);
#else
        if (!plausible) mark(res, STATUS_SUSPICIOUS, REASON_OUT_OF_ORDER);
#endif
        if (!(sl->flags & SLOT_HAS_TS)) {
            if (plausible) { sl->last_ts = ev->exchange_ts; sl->flags |= SLOT_HAS_TS; }
        } else if (ev->exchange_ts < sl->last_ts) {
            mark(res, STATUS_SUSPICIOUS, REASON_OUT_OF_ORDER);
        } else if (plausible) {
            sl->last_ts = ev->exchange_ts;
        }
        if (ev->receive_ts - ev->exchange_ts > eng->staleness_threshold_s)
            mark(res, STATUS_SUSPICIOUS, REASON_STALE);
    }

    /* --- crossed quote -------------------------------------------------------------- */
    if (!bad && has_bid && has_ask && ev->bid_price > ev->ask_price)
        mark(res, STATUS_INVALID, REASON_CROSSED_QUOTE);

    /* --- C-3: price sanity, LAST so that only clean events touch the baseline -------- */
    if (res->status != STATUS_INVALID && has_price) {
        double *ring = slot_ring(eng, sl);
        if (ring) {
            const double px = ev->price;
            int anomaly = 0;
            double sd2 = 0.0;
            if (sl->n >= MIN_SAMPLES_FOR_VARIANCE) {
                const double mean = sl->mean;
                double var = sl->m2 * eng->inv_n[sl->n];
                if (var < 0.0) var = 0.0;
                const double fl = eng->price_sigma_floor_rel * fabs(mean);
                sd2 = var > fl * fl ? var : fl * fl;                  /* sigma floor */
                const double dev = px - mean;
                if (sl->n >= eng->price_min_samples)
                    anomaly = (dev * dev) > (eng->price_anomaly_stddev * eng->price_anomaly_stddev * sd2);  /* no sqrt */
                else
                    anomaly = fabs(mean) > 0.0 && fabs(dev) > WARMUP_PRICE_DEV_RATIO * fabs(mean);   /* warm-up: coarse jump test only */
            }
            if (anomaly) {
                mark(res, STATUS_SUSPICIOUS, REASON_PRICE_ANOMALY);
                /* regime-shift handling: K consecutive mutually consistent anomalies => accept the new level */
                double band = eng->price_anomaly_stddev * sqrt(sd2);
                const double rel = eng->price_reseed_band_rel * fabs(px);
                if (rel > band) band = rel;
                if (sl->anom_count > 0 && fabs(px - sl->anom_last) <= band) sl->anom_count++;
                else sl->anom_count = 1;
                sl->anom_last = px;
                if (eng->price_reseed_after > 0 && sl->anom_count >= eng->price_reseed_after) {
                    sl->n = 0; sl->head = 0; sl->mean = 0.0; sl->m2 = 0.0; sl->fold_count = 0; sl->anom_count = 0;
                    fold_price(eng, sl, ring, px);
                }
            } else {
                sl->anom_count = 0;
                fold_price(eng, sl, ring, px);
            }
        }
    }
}

EXPORT void fastpath_engine_evaluate_unlocked(FastEngine *eng, const FastEvent *ev, FastResult *res) {
    if (!res) return;
    evaluate_nolock(eng, ev, res);
}

EXPORT void fastpath_engine_evaluate(FastEngine *eng, const FastEvent *ev, FastResult *res) {
    if (!res) return;
    if (!eng) { evaluate_nolock(NULL, ev, res); return; }
    MD_LOCK(&eng->lock);
    evaluate_nolock(eng, ev, res);
    MD_UNLOCK(&eng->lock);
}

EXPORT void fastpath_engine_evaluate_batch(FastEngine *eng, const FastEvent *events, FastResult *results, int32_t count) {
    if (!events || !results || count <= 0) return;
    if (!eng) { for (int32_t i = 0; i < count; ++i) evaluate_nolock(NULL, &events[i], &results[i]); return; }
    MD_LOCK(&eng->lock);                             /* one lock per batch: ~free */
    for (int32_t i = 0; i < count; ++i) evaluate_nolock(eng, &events[i], &results[i]);
    MD_UNLOCK(&eng->lock);
}

EXPORT uint64_t fastpath_engine_eval_fast2(
    FastEngine *eng,
    int32_t source_id, int32_t instrument_id, int32_t event_type,
    double exchange_ts, double receive_ts, int64_t sequence_num,
    double price, double quantity, double bid_price, double ask_price,
    double bid_size, double ask_size, uint32_t present_mask
) {
    FastEvent ev;
    memset(&ev, 0, sizeof ev);
    ev.source_id = source_id; ev.instrument_id = instrument_id; ev.event_type = event_type;
    ev.present_mask = present_mask;
    ev.exchange_ts = exchange_ts; ev.receive_ts = receive_ts; ev.sequence_num = sequence_num;
    ev.price = price; ev.quantity = quantity; ev.bid_price = bid_price; ev.ask_price = ask_price;
    ev.bid_size = bid_size; ev.ask_size = ask_size;
    FastResult res;
    fastpath_engine_evaluate(eng, &ev, &res);
    return (((uint64_t)res.status) << 32) | ((uint64_t)res.reason_mask);
}

EXPORT uint64_t fastpath_engine_eval_fast(
    FastEngine *eng,
    int32_t source_id, int32_t instrument_id, int32_t event_type,
    double exchange_ts, double receive_ts, int64_t sequence_num,
    double price, double quantity, double bid_price, double ask_price,
    double bid_size, double ask_size
) {
    return fastpath_engine_eval_fast2(eng, source_id, instrument_id, event_type, exchange_ts, receive_ts,
        sequence_num, price, quantity, bid_price, ask_price, bid_size, ask_size, 0u /* legacy NaN semantics */);
}

EXPORT uint64_t fastpath_engine_noop(
    FastEngine *eng,
    int32_t source_id, int32_t instrument_id, int32_t event_type,
    double exchange_ts, double receive_ts, int64_t sequence_num,
    double price, double quantity, double bid_price, double ask_price,
    double bid_size, double ask_size
) {
    (void)eng; (void)source_id; (void)instrument_id; (void)event_type;
    (void)exchange_ts; (void)receive_ts; (void)sequence_num;
    (void)price; (void)quantity; (void)bid_price; (void)ask_price;
    (void)bid_size; (void)ask_size;
    return 0;
}

/* ============================================================================
 * C-6  replay ring
 * ==========================================================================*/
static inline size_t md_strnlen(const char *s, size_t max) { size_t n = 0; while (n < max && s[n]) ++n; return n; }

EXPORT void fastpath_engine_replay_record(
    FastEngine *eng, uint64_t seq, const char *symbol, const char *source, const char *event_type,
    double price, double size, double bid, double ask, double bid_size, double ask_size,
    uint8_t status, uint8_t is_crossed, double exchange_ts, double ingest_ts, double broadcast_ts, double engine_us
) {
    if (!eng) return;
    uint32_t slot = (uint32_t)(seq & REPLAY_RING_MASK);
    FastReplayRecord *rec = &eng->replay_ring[slot];
    rec->seq = seq;
    strncpy(rec->symbol, symbol ? symbol : "", sizeof(rec->symbol) - 1);
    rec->symbol[sizeof(rec->symbol) - 1] = '\0';
    strncpy(rec->source, source ? source : "", sizeof(rec->source) - 1);
    rec->source[sizeof(rec->source) - 1] = '\0';
    strncpy(rec->event_type, event_type ? event_type : "TICK", sizeof(rec->event_type) - 1);
    rec->event_type[sizeof(rec->event_type) - 1] = '\0';
    rec->price = price; rec->size = size; rec->bid = bid; rec->ask = ask;
    rec->bid_size = bid_size; rec->ask_size = ask_size;
    rec->status = status; rec->is_crossed = is_crossed;
    rec->exchange_ts = exchange_ts; rec->ingest_ts = ingest_ts; rec->broadcast_ts = broadcast_ts;
    rec->engine_us = engine_us;
    rec->is_valid = 1;

    /* window bookkeeping: derive min from max so an out-of-order older seq can never widen it */
    if (eng->replay_total_recorded == 0 || seq > eng->replay_max_seq) eng->replay_max_seq = seq;
    if (eng->replay_max_seq >= REPLAY_RING_SIZE) eng->replay_min_seq = eng->replay_max_seq - REPLAY_RING_SIZE + 1;
    else if (eng->replay_total_recorded == 0 || seq < eng->replay_min_seq) eng->replay_min_seq = seq;
    eng->replay_total_recorded++;
}

/* Clamp [from,to] to what the ring can actually hold; returns 0 when nothing can match.
 * (upstream looped `for (s = from; s <= to; ++s)`: to_seq == UINT64_MAX never terminates.) */
static inline int replay_clamp(const FastEngine *eng, uint64_t *from, uint64_t *to) {
    if (eng->replay_total_recorded == 0) return 0;
    if (*from < eng->replay_min_seq) *from = eng->replay_min_seq;
    if (*to > eng->replay_max_seq) *to = eng->replay_max_seq;
    if (*to < *from) return 0;
    if (*to - *from >= (uint64_t)REPLAY_RING_SIZE) *to = *from + (uint64_t)REPLAY_RING_SIZE - 1u;
    return 1;
}

EXPORT int32_t fastpath_engine_replay_slice(
    FastEngine *eng, uint64_t from_seq, uint64_t to_seq, const char *symbol,
    FastReplayRecord *out_records, int32_t max_out
) {
    if (!eng || to_seq < from_seq || max_out <= 0 || !out_records) return 0;
    if (!replay_clamp(eng, &from_seq, &to_seq)) return 0;
    int32_t count = 0;
    int filter_sym = (symbol != NULL && symbol[0] != '\0' && strcmp(symbol, "ALL") != 0);
    for (uint64_t s = from_seq; ; ++s) {
        if (count >= max_out) break;
        uint32_t slot = (uint32_t)(s & REPLAY_RING_MASK);
        FastReplayRecord *rec = &eng->replay_ring[slot];
        if (rec->is_valid && rec->seq == s) {
            if (!filter_sym || strcmp(rec->symbol, symbol) == 0) {
                out_records[count] = *rec;
                count++;
            }
        }
        if (s == to_seq) break;                      /* terminates even when to_seq == UINT64_MAX */
    }
    return count;
}

EXPORT int32_t fastpath_engine_replay_binary_slice(
    FastEngine *eng, uint64_t from_seq, uint64_t to_seq, const char *symbol,
    uint8_t *out_bytes, int32_t max_bytes
) {
    int32_t frame_len = (int32_t)sizeof(FastBinTickFrame); // 92 bytes
    if (!eng || to_seq < from_seq || max_bytes < frame_len || !out_bytes) return 0;
    if (!replay_clamp(eng, &from_seq, &to_seq)) return 0;
    int32_t frames_written = 0;
    int32_t offset = 0;
    int filter_sym = (symbol != NULL && symbol[0] != '\0' && strcmp(symbol, "ALL") != 0);
    for (uint64_t s = from_seq; ; ++s) {
        if (offset + frame_len > max_bytes) break;
        uint32_t slot = (uint32_t)(s & REPLAY_RING_MASK);
        FastReplayRecord *rec = &eng->replay_ring[slot];
        if (rec->is_valid && rec->seq == s) {
            if (!filter_sym || strcmp(rec->symbol, symbol) == 0) {
                FastBinTickFrame *frame = (FastBinTickFrame *)(out_bytes + offset);
                frame->magic[0] = 'M';
                frame->magic[1] = 'D';
                frame->msg_type = 1;      // MSG_TICK
                frame->payload_len = 88;  // TICK_PAYLOAD_LEN
                frame->seq = rec->seq;
                frame->status = rec->status;
                frame->is_crossed = rec->is_crossed;
                /* C-6: tell the reader when the 8-byte wire fields lost characters */
                frame->pad[0] = (char)((md_strnlen(rec->symbol, sizeof rec->symbol) > 8u ? 1 : 0) |
                                       (md_strnlen(rec->source, sizeof rec->source) > 8u ? 2 : 0));
                frame->pad[1] = 0;
                frame->engine_us = (float)rec->engine_us;
                frame->exchange_ts = rec->exchange_ts;
                frame->ingest_ts = rec->ingest_ts;
                frame->broadcast_ts = rec->broadcast_ts;
                frame->price = rec->price;
                frame->size = rec->size;
                frame->bid = rec->bid;
                frame->ask = rec->ask;
                memset(frame->symbol, 0, 8);
                memcpy(frame->symbol, rec->symbol, md_strnlen(rec->symbol, 8));
                memset(frame->source, 0, 8);
                memcpy(frame->source, rec->source, md_strnlen(rec->source, 8));
                offset += frame_len;
                frames_written++;
            }
        }
        if (s == to_seq) break;
    }
    return frames_written;
}

EXPORT void fastpath_engine_replay_stats(
    FastEngine *eng, uint64_t *out_min_seq, uint64_t *out_max_seq, uint64_t *out_total_recorded, int32_t *out_capacity
) {
    if (!eng) return;
    if (out_min_seq) *out_min_seq = eng->replay_min_seq;
    if (out_max_seq) *out_max_seq = eng->replay_max_seq;
    if (out_total_recorded) *out_total_recorded = eng->replay_total_recorded;
    if (out_capacity) *out_capacity = REPLAY_RING_SIZE;
}

EXPORT void fastpath_engine_replay_clear(FastEngine *eng) {
    if (!eng) return;
    memset(eng->replay_ring, 0, sizeof(eng->replay_ring));
    eng->replay_min_seq = 0;
    eng->replay_max_seq = 0;
    eng->replay_total_recorded = 0;
}

// ---------------------------------------------------------------------------
// Deprecated Global Singleton Compatibility Shim  (C-8: race-free lazy creation)
// ---------------------------------------------------------------------------
static FastEngine *g_default_engine = NULL;

static inline FastEngine *md_load_default(void) {
#if defined(_MSC_VER) && !defined(__clang__)
    return (FastEngine *)_InterlockedCompareExchangePointer((void *volatile *)&g_default_engine, NULL, NULL);
#else
    return __atomic_load_n(&g_default_engine, __ATOMIC_ACQUIRE);
#endif
}
static inline int md_cas_default(FastEngine *desired) {
#if defined(_MSC_VER) && !defined(__clang__)
    return _InterlockedCompareExchangePointer((void *volatile *)&g_default_engine, desired, NULL) == NULL;
#else
    FastEngine *expected = NULL;
    return __atomic_compare_exchange_n(&g_default_engine, &expected, desired, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE);
#endif
}
static inline FastEngine *get_default_engine(void) {
    FastEngine *e = md_load_default();
    if (!e) {
        FastEngine *fresh = fastpath_engine_create(0.05, 6.0, 50);
        if (fresh && !md_cas_default(fresh)) fastpath_engine_destroy(fresh);   /* lost the race */
        e = md_load_default();
    }
    return e;
}

EXPORT void fastpath_cleanup(void) {
    if (g_default_engine) {
        fastpath_engine_destroy(g_default_engine);
        g_default_engine = NULL;
    }
}

EXPORT void fastpath_init(double staleness_threshold_s, double anomaly_stddev, int32_t price_window) {
    if (g_default_engine) {
        fastpath_engine_destroy(g_default_engine);
    }
    g_default_engine = fastpath_engine_create(staleness_threshold_s, anomaly_stddev, price_window);
}

EXPORT void fastpath_reset(void) {
    if (g_default_engine) {
        fastpath_engine_reset(g_default_engine);
    } else {
        g_default_engine = fastpath_engine_create(0.05, 6.0, 50);
    }
}

EXPORT void fastpath_evaluate(const FastEvent *ev, FastResult *res) {
    fastpath_engine_evaluate(get_default_engine(), ev, res);
}

EXPORT void fastpath_evaluate_batch(const FastEvent *events, FastResult *results, int32_t count) {
    fastpath_engine_evaluate_batch(get_default_engine(), events, results, count);
}

EXPORT uint64_t fastpath_eval_fast(
    int32_t source_id, int32_t instrument_id, int32_t event_type,
    double exchange_ts, double receive_ts, int64_t sequence_num,
    double price, double quantity, double bid_price, double ask_price,
    double bid_size, double ask_size
) {
    return fastpath_engine_eval_fast(
        get_default_engine(), source_id, instrument_id, event_type,
        exchange_ts, receive_ts, sequence_num, price, quantity, bid_price, ask_price, bid_size, ask_size);
}

EXPORT uint64_t fastpath_noop(
    int32_t source_id, int32_t instrument_id, int32_t event_type,
    double exchange_ts, double receive_ts, int64_t sequence_num,
    double price, double quantity, double bid_price, double ask_price,
    double bid_size, double ask_size
) {
    (void)source_id; (void)instrument_id; (void)event_type;
    (void)exchange_ts; (void)receive_ts; (void)sequence_num;
    (void)price; (void)quantity; (void)bid_price; (void)ask_price;
    (void)bid_size; (void)ask_size;
    return 0;
}

EXPORT uint64_t fastpath_noop_struct(const FastEvent *ev) { (void)ev; return 0; }
EXPORT uint64_t fastpath_noop_scalar1(int64_t val) { (void)val; return 0; }

EXPORT void fastpath_replay_record(
    uint64_t seq, const char *symbol, const char *source, const char *event_type,
    double price, double size, double bid, double ask, double bid_size, double ask_size,
    uint8_t status, uint8_t is_crossed, double exchange_ts, double ingest_ts, double broadcast_ts, double engine_us
) {
    fastpath_engine_replay_record(
        get_default_engine(), seq, symbol, source, event_type, price, size, bid, ask, bid_size, ask_size,
        status, is_crossed, exchange_ts, ingest_ts, broadcast_ts, engine_us);
}

EXPORT int32_t fastpath_replay_slice(
    uint64_t from_seq, uint64_t to_seq, const char *symbol, FastReplayRecord *out_records, int32_t max_out
) {
    return fastpath_engine_replay_slice(get_default_engine(), from_seq, to_seq, symbol, out_records, max_out);
}

EXPORT int32_t fastpath_replay_binary_slice(
    uint64_t from_seq, uint64_t to_seq, const char *symbol, uint8_t *out_bytes, int32_t max_bytes
) {
    return fastpath_engine_replay_binary_slice(get_default_engine(), from_seq, to_seq, symbol, out_bytes, max_bytes);
}

EXPORT void fastpath_replay_stats(
    uint64_t *out_min_seq, uint64_t *out_max_seq, uint64_t *out_total_recorded, int32_t *out_capacity
) {
    fastpath_engine_replay_stats(get_default_engine(), out_min_seq, out_max_seq, out_total_recorded, out_capacity);
}

EXPORT void fastpath_replay_clear(void) { fastpath_engine_replay_clear(get_default_engine()); }

// ---------------------------------------------------------------------------
// Native Zero-Copy Shared Memory (SHM) Hot-Path Routines (Spec §18, Phase 1)
// ---------------------------------------------------------------------------
#pragma pack(push, 1)
typedef struct {
    uint64_t commit_seq;
    uint8_t event_type;
    uint8_t status;
    uint8_t is_crossed;
    uint8_t pad1[5];
    double exchange_ts;
    double ingest_ts;
    double broadcast_ts;
    float engine_us;
    uint8_t pad2[4];
    double price;
    double size;
    double bid;
    double ask;
    double bid_sz;
    double ask_sz;
    char symbol[16];
    char source[8];
    uint8_t pad3[8];
} NativeShmSlot; // exactly 128 bytes
#pragma pack(pop)
_Static_assert(sizeof(NativeShmSlot) == 128, "NativeShmSlot must be 128 bytes");

#define MD_UNCOMMITTED (~(uint64_t)0)   /* commit_seq value meaning "slot is being written / never written" */

/* ---- LEGACY layout (write_seq at header offset 20).  Ordering fixed; layout kept for wire compat. ---- */
EXPORT int32_t fastpath_shm_write_tick(
    uint8_t *shm_buf, uint32_t slot_count, uint64_t seq, const char *symbol, const char *source,
    double price, double size, double bid, double ask, double bid_sz, double ask_sz,
    uint8_t status, uint8_t is_crossed, double exchange_ts, double ingest_ts, double broadcast_ts, float engine_us
) {
    if (!shm_buf || slot_count < 2u || (slot_count & (slot_count - 1u))) return 0;  /* power of two required */
    uint32_t mask = slot_count - 1;
    uint32_t slot_idx = (uint32_t)(seq & mask);
    NativeShmSlot *slot = (NativeShmSlot *)(shm_buf + 128 + ((size_t)slot_idx * 128u));

    /* invalidate first so a reader that lands mid-write never validates a torn slot */
    MD_STORE_REL_U64((uint8_t *)slot, MD_UNCOMMITTED);
    MD_FENCE_RELEASE();

    // Phase 1: Write all payload fields
    slot->event_type = 1; // TICK
    slot->status = status;
    slot->is_crossed = is_crossed;
    memset(slot->pad1, 0, sizeof(slot->pad1));
    slot->exchange_ts = exchange_ts;
    slot->ingest_ts = ingest_ts;
    slot->broadcast_ts = broadcast_ts;
    slot->engine_us = engine_us;
    memset(slot->pad2, 0, sizeof(slot->pad2));
    slot->price = price;
    slot->size = size;
    slot->bid = bid;
    slot->ask = ask;
    slot->bid_sz = bid_sz;
    slot->ask_sz = ask_sz;
    memset(slot->symbol, 0, sizeof(slot->symbol));
    if (symbol) strncpy(slot->symbol, symbol, 15);
    memset(slot->source, 0, sizeof(slot->source));
    if (source) strncpy(slot->source, source, 7);
    memset(slot->pad3, 0, sizeof(slot->pad3));

    // Commit: release-store so all payload stores are visible before commit_seq
    MD_STORE_REL_U64((uint8_t *)slot, seq);

    // Phase 2: publish head in Header Line 1 (offset 20, legacy, unaligned)
    MD_FENCE_RELEASE();
    memcpy(shm_buf + 20, &seq, sizeof seq);
    return 1;
}

EXPORT int32_t fastpath_shm_read_slot(
    const uint8_t *shm_buf, uint32_t slot_count, uint64_t target_seq, NativeShmSlot *out_slot
) {
    if (!shm_buf || !out_slot || slot_count < 2u || (slot_count & (slot_count - 1u))) return 0;
    uint64_t head_seq;
    memcpy(&head_seq, shm_buf + 20, sizeof head_seq);
    MD_FENCE_ACQUIRE();
    if (target_seq > head_seq) return 0;                       // Not published yet
    if (head_seq - target_seq >= slot_count) return -1;        // Overrun (lapped)

    uint32_t mask = slot_count - 1;
    uint32_t slot_idx = (uint32_t)(target_seq & mask);
    const NativeShmSlot *slot = (const NativeShmSlot *)(shm_buf + 128 + ((size_t)slot_idx * 128u));

    const uint64_t c1 = MD_LOAD_ACQ_U64((const uint8_t *)slot);
    if (c1 != target_seq) return 0;                            // Uncommitted or being overwritten
    memcpy(out_slot, slot, sizeof(NativeShmSlot));             // seqlock copy
    MD_FENCE_ACQUIRE();
    const uint64_t c2 = MD_LOAD_RLX_U64((const uint8_t *)slot);
    if (c2 != target_seq) return 0;                            // Torn read detected (re-read AFTER the copy)
    return 1;
}

/* ---- v3 layout: aligned header, UNCOMMITTED marker, head = next seq to publish -------------------
 *  Header line 1 (64 B):  magic[4] "MDRP" | u16 version=3 | u16 slot_size=128 | u32 slot_count | u32 reserved
 *                         | u64 epoch @16 | u64 head @24 | 32 B pad
 *  Header line 2 (64 B):  heartbeat / diagnostics (owned by Python, unchanged)
 *  Python struct for line 1:  "<4sHHIIQQ32s"
 *  `head` == number of ticks published == last committed seq + 1 (0 = nothing published, so a
 *  zero-filled or freshly initialised segment can never yield a phantom "seq 0" tick).
 *  Return codes:  read -> 1 ok, 0 not ready (retry), -1 lapped/torn (skip forward)
 * ------------------------------------------------------------------------------------------------*/
#define SHM3_HDR_SIZE     128u
#define SHM3_SLOT_SIZE    128u
#define SHM3_VERSION      3u
#define SHM3_OFF_EPOCH    16u
#define SHM3_OFF_HEAD     24u
#define SHM3_PRESENT_PRICE 0x01u
#define SHM3_PRESENT_SIZE  0x02u
#define SHM3_PRESENT_BID   0x04u
#define SHM3_PRESENT_ASK   0x08u
#define SHM3_PRESENT_BSZ   0x10u
#define SHM3_PRESENT_ASZ   0x20u
#define SHM3_ST_UNKNOWN    0u        /* status byte: 0 = UNKNOWN (never "VALID" by default), 1 VALID, 2 SUSPICIOUS, 3 INVALID */

typedef struct {
    uint64_t commit_seq;      /*   0 */
    uint8_t  event_type;      /*   8 */
    uint8_t  status;          /*   9 */
    uint8_t  is_crossed;      /*  10 */
    uint8_t  present;         /*  11 : SHM3_PRESENT_* bits */
    uint8_t  trunc;           /*  12 : bit0 symbol truncated, bit1 source truncated */
    uint8_t  pad1[3];         /*  13 */
    double   exchange_ts;     /*  16 */
    double   ingest_ts;       /*  24 */
    double   broadcast_ts;    /*  32 */
    float    engine_us;       /*  40 */
    uint32_t pad2;            /*  44 */
    double   price;           /*  48 */
    double   size;
    double   bid;
    double   ask;
    double   bid_sz;
    double   ask_sz;          /*  88 */
    char     symbol[16];      /*  96 */
    char     source[8];       /* 112 */
    uint8_t  pad3[8];         /* 120 */
} ShmSlotV3;
_Static_assert(sizeof(ShmSlotV3) == 128, "ShmSlotV3 must be 128 bytes");

static inline int shm3_geometry_ok(const uint8_t *buf, size_t len, uint32_t slot_count) {
    if (!buf || slot_count < 2u || (slot_count & (slot_count - 1u))) return 0;
    if (((uintptr_t)buf & 7u) != 0u) return 0;                                   /* atomics need 8-byte alignment */
    if (len < (size_t)SHM3_HDR_SIZE + (size_t)slot_count * SHM3_SLOT_SIZE) return 0;
    return 1;
}

EXPORT int32_t fastpath_shm_init_v3(uint8_t *buf, size_t len, uint32_t slot_count, uint64_t epoch) {
    if (!shm3_geometry_ok(buf, len, slot_count)) return 0;
    memset(buf, 0, (size_t)SHM3_HDR_SIZE + (size_t)slot_count * SHM3_SLOT_SIZE);
    for (uint32_t i = 0; i < slot_count; ++i)
        MD_STORE_RLX_U64(buf + SHM3_HDR_SIZE + (size_t)i * SHM3_SLOT_SIZE, MD_UNCOMMITTED);
    uint16_t ver = (uint16_t)SHM3_VERSION, ssz = (uint16_t)SHM3_SLOT_SIZE;
    memcpy(buf + 4, &ver, 2); memcpy(buf + 6, &ssz, 2); memcpy(buf + 8, &slot_count, 4);
    MD_STORE_RLX_U64(buf + SHM3_OFF_EPOCH, epoch);
    MD_STORE_RLX_U64(buf + SHM3_OFF_HEAD, 0);
    MD_FENCE_RELEASE();
    memcpy(buf, "MDRP", 4);                                   /* magic last: readers reject until fully built */
    return 1;
}

EXPORT int32_t fastpath_shm_write_tick_v3(
    uint8_t *buf, size_t len, uint32_t slot_count, uint64_t seq,
    const char *symbol, const char *source,
    double price, double size, double bid, double ask, double bid_sz, double ask_sz,
    uint8_t status, uint8_t is_crossed, uint8_t present,
    double exchange_ts, double ingest_ts, double broadcast_ts, float engine_us
) {
    if (!shm3_geometry_ok(buf, len, slot_count)) return 0;
    const uint64_t head = MD_LOAD_RLX_U64(buf + SHM3_OFF_HEAD);      /* single producer: we own head */
    if (seq != head) return -2;                                      /* must publish contiguous sequence numbers */

    ShmSlotV3 tmp;
    memset(&tmp, 0, sizeof tmp);
    tmp.event_type = 1;
    tmp.status = status <= 3u ? status : (uint8_t)SHM3_ST_UNKNOWN;   /* unknown codes are never promoted to VALID */
    tmp.is_crossed = is_crossed;
    tmp.present = present;
    tmp.exchange_ts = exchange_ts; tmp.ingest_ts = ingest_ts; tmp.broadcast_ts = broadcast_ts;
    tmp.engine_us = engine_us;
    tmp.price = price; tmp.size = size; tmp.bid = bid; tmp.ask = ask; tmp.bid_sz = bid_sz; tmp.ask_sz = ask_sz;
    if (symbol) { size_t n = md_strnlen(symbol, 64); if (n > 16u) tmp.trunc |= 1u; memcpy(tmp.symbol, symbol, n > 16u ? 16u : n); }
    if (source) { size_t n = md_strnlen(source, 64); if (n > 8u)  tmp.trunc |= 2u; memcpy(tmp.source, source, n > 8u ? 8u : n); }

    uint8_t *slot = buf + SHM3_HDR_SIZE + (size_t)(seq & (slot_count - 1u)) * SHM3_SLOT_SIZE;
    uint64_t src[16];
    memcpy(src, &tmp, sizeof src);
    MD_STORE_RLX_U64(slot, MD_UNCOMMITTED);                          /* 1. invalidate                       */
    MD_FENCE_RELEASE();                                              /*    ...before touching the payload   */
    for (int w = 1; w < 16; ++w) MD_STORE_RLX_U64(slot + (size_t)w * 8u, src[w]);   /* 2. payload (word-atomic) */
    MD_STORE_REL_U64(slot, seq);                                     /* 3. commit: payload happens-before   */
    MD_STORE_REL_U64(buf + SHM3_OFF_HEAD, seq + 1u);                 /* 4. publish                          */
    return 1;
}

EXPORT int32_t fastpath_shm_read_slot_v3(
    const uint8_t *buf, size_t len, uint32_t slot_count, uint64_t target_seq, ShmSlotV3 *out
) {
    if (!out || !shm3_geometry_ok(buf, len, slot_count)) return 0;
    const uint64_t head = MD_LOAD_ACQ_U64(buf + SHM3_OFF_HEAD);
    if (target_seq >= head) return 0;                                /* not published yet */
    if (head - target_seq > slot_count) return -1;                   /* lapped */
    const uint8_t *slot = buf + SHM3_HDR_SIZE + (size_t)(target_seq & (slot_count - 1u)) * SHM3_SLOT_SIZE;
    const uint64_t c1 = MD_LOAD_ACQ_U64(slot);
    if (c1 != target_seq) return (c1 == MD_UNCOMMITTED || c1 < target_seq) ? 0 : -1;
    uint64_t words[16];
    words[0] = c1;
    for (int w = 1; w < 16; ++w) words[w] = MD_LOAD_RLX_U64(slot + (size_t)w * 8u);
    MD_FENCE_ACQUIRE();
    const uint64_t c2 = MD_LOAD_RLX_U64(slot);                       /* seqlock: re-read AFTER the copy */
    if (c2 != c1) return -1;
    memcpy(out, words, sizeof words);
    return 1;
}

EXPORT uint64_t fastpath_shm_head_v3(const uint8_t *buf)  { return buf ? MD_LOAD_ACQ_U64(buf + SHM3_OFF_HEAD)  : 0u; }
EXPORT uint64_t fastpath_shm_epoch_v3(const uint8_t *buf) { return buf ? MD_LOAD_ACQ_U64(buf + SHM3_OFF_EPOCH) : 0u; }

// ---------------------------------------------------------------------------
// Simple Binary Encoding (SBE) Wire Protocol Acceleration (Spec §18, §26)
// ---------------------------------------------------------------------------

#pragma pack(push, 1)
typedef struct {
    uint16_t block_length;
    uint16_t template_id;
    uint16_t schema_id;
    uint16_t version;
} SbeHeader;

typedef struct {
    uint64_t seq;
    double exchange_ts;
    double ingest_ts;
    double broadcast_ts;
    double price;
    double size;
    double bid;
    double ask;
    double bid_size;
    double ask_size;
    uint8_t status;
    uint8_t is_crossed;
    uint16_t reserved;
    float engine_us;
    char symbol[16];
    char source[16];
} SbeTickPayload;

typedef struct {
    SbeHeader header;
    SbeTickPayload payload;
} SbeTickFrame;
#pragma pack(pop)

EXPORT int32_t fastpath_sbe_pack_tick(
    uint8_t *out_buf,
    uint64_t seq,
    const char *symbol,
    const char *source,
    double price,
    double size,
    double bid,
    double ask,
    double bid_size,
    double ask_size,
    uint8_t status_code,
    uint8_t is_crossed,
    double exchange_ts,
    double ingest_ts,
    double broadcast_ts,
    float engine_us
) {
    if (!out_buf) return 0;
    SbeTickFrame *frame = (SbeTickFrame *)out_buf;
    frame->header.block_length = sizeof(SbeTickPayload);
    frame->header.template_id = 101;
    frame->header.schema_id = 1;
    frame->header.version = 1;

    frame->payload.seq = seq;
    frame->payload.exchange_ts = exchange_ts;
    frame->payload.ingest_ts = ingest_ts;
    frame->payload.broadcast_ts = broadcast_ts;
    frame->payload.price = price;
    frame->payload.size = size;
    frame->payload.bid = bid;
    frame->payload.ask = ask;
    frame->payload.bid_size = bid_size;
    frame->payload.ask_size = ask_size;
    frame->payload.status = status_code;
    frame->payload.is_crossed = is_crossed;
    frame->payload.reserved = 0;
    frame->payload.engine_us = engine_us;

    memset(frame->payload.symbol, 0, 16);
    if (symbol) strncpy(frame->payload.symbol, symbol, 15);
    memset(frame->payload.source, 0, 16);
    if (source) strncpy(frame->payload.source, source, 15);

    return sizeof(SbeTickFrame);
}

EXPORT int32_t fastpath_sbe_unpack_tick(
    const uint8_t *in_buf,
    size_t in_len,
    SbeTickPayload *out_payload
) {
    if (!in_buf || !out_payload || in_len < sizeof(SbeTickFrame)) return 0;
    const SbeTickFrame *frame = (const SbeTickFrame *)in_buf;
    if (frame->header.template_id != 101) return 0;
    memcpy(out_payload, &frame->payload, sizeof(SbeTickPayload));
    return 1;
}


/* >>> HUNK 1 (new helper, place above the stream function) <<< */
static inline uint64_t sbe_dedup_key(const FastEngine *e, const SbeTickPayload *p) {
    uint64_t h = hcomb(e->hash_seed, 0x5be0ULL);
    h = hbytes(h, p->source, sizeof p->source);      /* was: key = p->seq  (collided across sources/symbols) */
    h = hbytes(h, p->symbol, sizeof p->symbol);
    h = hcomb(h, p->seq);
    return mix64(h);
}

static int32_t sbe_stream_impl(
    const uint8_t *sbe_buffer,
    int32_t count,
    FastResult *out_results,
    uint8_t *shm_buffer,
    uint32_t shm_slot_count
) {
    if (!sbe_buffer || count <= 0) return 0;

    const SbeTickFrame *frames = (const SbeTickFrame *)sbe_buffer;
    int32_t valid_count = 0;
    FastEngine *sbe_eng = get_default_engine();              /* >>> HUNK 3a: hoisted out of the loop <<< */
    if (!sbe_eng) return 0;

    for (int32_t i = 0; i < count; ++i) {
        const SbeTickPayload *p = &frames[i].payload;
        int32_t status = STATUS_VALID;
        uint32_t reason_mask = REASON_NONE;

        // 1. Numerical Validity Bounds
        if ((!isnan(p->price) && (isinf(p->price) || p->price < 0.0)) ||
            (!isnan(p->bid) && (isinf(p->bid) || p->bid < 0.0)) ||
            (!isnan(p->ask) && (isinf(p->ask) || p->ask < 0.0))) {
            status = STATUS_INVALID;
            reason_mask |= REASON_SCHEMA_VIOLATION;
        }

        // 2. Crossed Quotes
        if (!isnan(p->bid) && !isnan(p->ask) && p->bid > 0.0 && p->ask > 0.0) {
            if (p->bid > p->ask) {
                status = STATUS_INVALID;
                reason_mask |= REASON_CROSSED_QUOTE;
            }
        }

        // 3. Deduplication Check
        if (p->seq > 0) {
            uint64_t key = sbe_dedup_key(sbe_eng, p);            /* >>> HUNK 3b <<< */
            if (dedup_check_insert(sbe_eng, key)) {              /* >>> HUNK 3c <<< */
                status = STATUS_INVALID;
                reason_mask |= REASON_DUPLICATE;
            }
        }

        if (out_results) {
            out_results[i].status = status;
            out_results[i]._reserved = 0;
            out_results[i].reason_mask = reason_mask & CORE_REASON_MASK;
        }

        if (status == STATUS_VALID) {
            valid_count++;
        }

        // 4. Zero-Copy Shared Memory Write
        if (shm_buffer && shm_slot_count > 0 && status != STATUS_INVALID) {
            fastpath_shm_write_tick(
                shm_buffer,
                shm_slot_count,
                p->seq,
                p->symbol,
                p->source,
                p->price,
                p->size,
                p->bid,
                p->ask,
                p->bid_size,
                p->ask_size,
                (uint8_t)status,
                p->is_crossed,
                p->exchange_ts,
                p->ingest_ts,
                p->broadcast_ts,
                p->engine_us
            );
        }
    }

    return valid_count;
}


/* >>> HUNK 4: exported entry points (place AFTER the closing brace of sbe_stream_impl) <<< */
EXPORT int32_t fastpath_process_sbe_stream(const uint8_t *sbe_buffer, int32_t count, FastResult *out_results,
                                           uint8_t *shm_buffer, uint32_t shm_slot_count) {
    FastEngine *e = get_default_engine();
    if (!e) return 0;
    MD_LOCK(&e->lock);
    int32_t r = sbe_stream_impl(sbe_buffer, count, out_results, shm_buffer, shm_slot_count);
    MD_UNLOCK(&e->lock);
    return r;
}
/* Bounds-checked variants: the caller states how big its buffers really are. */
EXPORT int32_t fastpath_process_sbe_stream_n(const uint8_t *sbe_buffer, size_t sbe_len, int32_t count,
                                             FastResult *out_results, int32_t out_capacity,
                                             uint8_t *shm_buffer, size_t shm_len, uint32_t shm_slot_count) {
    if (!sbe_buffer || count <= 0) return 0;
    if ((size_t)count > sbe_len / sizeof(SbeTickFrame)) return -1;
    if (out_results && out_capacity < count) return -1;
    if (shm_buffer && !(shm_slot_count >= 2u && !(shm_slot_count & (shm_slot_count - 1u)) &&
                        shm_len >= 128u + (size_t)shm_slot_count * 128u)) return -1;
    return fastpath_process_sbe_stream(sbe_buffer, count, out_results, shm_buffer, shm_slot_count);
}
EXPORT int32_t fastpath_sbe_pack_tick_n(uint8_t *out_buf, size_t out_len, uint64_t seq, const char *symbol, const char *source,
    double price, double size, double bid, double ask, double bid_size, double ask_size,
    uint8_t status_code, uint8_t is_crossed, double exchange_ts, double ingest_ts, double broadcast_ts, float engine_us) {
    if (!out_buf || out_len < sizeof(SbeTickFrame)) return 0;
    return fastpath_sbe_pack_tick(out_buf, seq, symbol, source, price, size, bid, ask, bid_size, ask_size,
                                  status_code, is_crossed, exchange_ts, ingest_ts, broadcast_ts, engine_us);
}

EXPORT int32_t fastpath_sbe_generate_stream(
    uint8_t *sbe_buffer,
    int32_t count,
    double anomaly_rate
) {
    if (!sbe_buffer || count <= 0) return 0;
    SbeTickFrame *frames = (SbeTickFrame *)sbe_buffer;
    const char *symbols[] = {"AAPL", "MSFT", "NVDA", "BTC/USD", "ES.c.0"};
    const char *sources[] = {"NASDAQ", "BATS", "ARCA", "EDGX", "IEX"};
    int num_syms = 5;
    int num_srcs = 5;
    double base_prices[] = {150.0, 420.0, 130.0, 65000.0, 5800.0};

    for (int32_t i = 0; i < count; ++i) {
        int sym_idx = i % num_syms;
        int src_idx = (i / num_syms) % num_srcs;
        double base = base_prices[sym_idx];
        double drift = (double)((i % 100) - 50) * 0.01;
        double price = base + drift;
        double bid = price - 0.05;
        double ask = price + 0.05;
        uint64_t seq = (uint64_t)(i + 1);

        if (anomaly_rate > 0.0 && ((i % 1000) < (int)(anomaly_rate * 1000.0))) {
            int kind = i % 3;
            if (kind == 0) {
                // Crossed quote
                bid = ask + 0.10;
            } else if (kind == 1) {
                // Negative price
                price = -1.0;
            } else {
                // Duplicate sequence
                if (i > 10) seq = (uint64_t)(i - 5);
            }
        }

        frames[i].header.block_length = (uint16_t)sizeof(SbeTickPayload);
        frames[i].header.template_id = 101;
        frames[i].header.schema_id = 1;
        frames[i].header.version = 1;

        SbeTickPayload *p = &frames[i].payload;
        p->seq = seq;
        p->price = price;
        p->size = 100.0f;
        p->bid = bid;
        p->ask = ask;
        p->bid_size = 50.0f;
        p->ask_size = 50.0f;
        p->status = 0;
        p->is_crossed = (bid > ask) ? 1 : 0;
        p->reserved = 0;
        p->exchange_ts = 1700000000.0 + (double)i * 0.00001;
        p->ingest_ts = p->exchange_ts + 0.000002;
        p->broadcast_ts = p->exchange_ts + 0.000005;
        p->engine_us = 0.05f;

        memset(p->symbol, 0, 16);
        strncpy(p->symbol, symbols[sym_idx], 15);
        memset(p->source, 0, 16);
        strncpy(p->source, sources[src_idx], 15);
    }
    return count;
}


// ===========================================================================
// Phase 12: High-Performance Vectorized Geodesic & Spatial Geofencing Engine
// ===========================================================================

#define FASTPATH_DEG2RAD (3.14159265358979323846 / 180.0)
#define FASTPATH_R_NM 3440.065

#pragma pack(push, 8)
typedef struct {
    double lat;
    double lon;
    double radius_nm;
    double dlat_max;       // radius_nm / 60.0
    double dlon_max;       // radius_nm / (60.0 * cos(lat_rad))
    double lat_rad;
    double lon_rad;
    double cos_lat;
    double sin_lat;
} FastChokepoint;
#pragma pack(pop)

EXPORT double fastpath_haversine_nm(double lat1, double lon1, double lat2, double lon2) {
    if (isnan(lat1) || isnan(lon1) || isnan(lat2) || isnan(lon2)) return -1.0;
    if (isinf(lat1) || isinf(lon1) || isinf(lat2) || isinf(lon2)) return -1.0;
    if (lat1 < -90.0 || lat1 > 90.0 || lat2 < -90.0 || lat2 > 90.0) return -1.0;
    if (lon1 < -180.0 || lon1 > 180.0 || lon2 < -180.0 || lon2 > 180.0) return -1.0;

    double phi1 = lat1 * FASTPATH_DEG2RAD;
    double phi2 = lat2 * FASTPATH_DEG2RAD;
    double dphi = (lat2 - lat1) * FASTPATH_DEG2RAD;
    double dlam = (lon2 - lon1) * FASTPATH_DEG2RAD;

    double s_dphi = sin(dphi * 0.5);
    double s_dlam = sin(dlam * 0.5);
    double a = s_dphi * s_dphi + cos(phi1) * cos(phi2) * s_dlam * s_dlam;
    if (a < 0.0) a = 0.0;
    if (a > 1.0) a = 1.0;
    double c = 2.0 * atan2(sqrt(a), sqrt(1.0 - a));
    return round(FASTPATH_R_NM * c * 10.0) / 10.0;
}

EXPORT double fastpath_equirectangular_nm(double lat1, double lon1, double lat2, double lon2) {
    if (isnan(lat1) || isnan(lon1) || isnan(lat2) || isnan(lon2)) return -1.0;
    if (isinf(lat1) || isinf(lon1) || isinf(lat2) || isinf(lon2)) return -1.0;
    if (lat1 < -90.0 || lat1 > 90.0 || lat2 < -90.0 || lat2 > 90.0) return -1.0;
    if (lon1 < -180.0 || lon1 > 180.0 || lon2 < -180.0 || lon2 > 180.0) return -1.0;

    double phi_m = ((lat1 + lat2) * 0.5) * FASTPATH_DEG2RAD;
    double dphi = (lat2 - lat1) * FASTPATH_DEG2RAD;
    double dlam = (lon2 - lon1) * FASTPATH_DEG2RAD;
    double x = dlam * cos(phi_m);
    double y = dphi;
    return round(FASTPATH_R_NM * sqrt(x * x + y * y) * 10.0) / 10.0;
}

EXPORT int32_t fastpath_vessel_chokepoint_eval(
    double v_lat, double v_lon,
    const FastChokepoint *chokepoints, int32_t cp_count,
    int32_t *out_nearest_idx, double *out_nearest_dist, uint8_t *out_in_chokepoint
) {
    if (cp_count <= 0 || !chokepoints) return -1;
    if (isnan(v_lat) || isnan(v_lon) || isinf(v_lat) || isinf(v_lon)) return -1;
    if (v_lat < -90.0 || v_lat > 90.0 || v_lon < -180.0 || v_lon > 180.0) return -1;

    double min_dist = 1e9;
    int32_t nearest = 0;
    uint8_t in_chokepoint = 0;

    double v_phi = v_lat * FASTPATH_DEG2RAD;
    double cos_phi = cos(v_phi);

    for (int32_t i = 0; i < cp_count; ++i) {
        const FastChokepoint *cp = &chokepoints[i];

        double dlam = (v_lon - cp->lon) * FASTPATH_DEG2RAD;
        double s_dphi = sin((cp->lat - v_lat) * FASTPATH_DEG2RAD * 0.5);
        double s_dlam = sin(dlam * 0.5);
        double a = s_dphi * s_dphi + cos_phi * cp->cos_lat * s_dlam * s_dlam;
        if (a < 0.0) a = 0.0;
        if (a > 1.0) a = 1.0;
        double dist = FASTPATH_R_NM * 2.0 * atan2(sqrt(a), sqrt(1.0 - a));

        if (dist < min_dist) {
            min_dist = dist;
            nearest = i;
        }
        if (dist <= cp->radius_nm) {
            in_chokepoint = 1;
        }
    }

    if (out_nearest_idx) *out_nearest_idx = nearest;
    if (out_nearest_dist) *out_nearest_dist = round(min_dist * 10.0) / 10.0;
    if (out_in_chokepoint) *out_in_chokepoint = in_chokepoint;
    return 0;
}

EXPORT int32_t fastpath_batch_fleet_geofence(
    const double *v_lats, const double *v_lons, int32_t v_count,
    const FastChokepoint *chokepoints, int32_t cp_count,
    int32_t *out_nearest_indices, double *out_nearest_distances, uint8_t *out_in_chokepoints
) {
    if (!v_lats || !v_lons || v_count <= 0 || !chokepoints || cp_count <= 0) return -1;

    for (int32_t v = 0; v < v_count; ++v) {
        int32_t n_idx = 0;
        double n_dist = 0.0;
        uint8_t in_cp = 0;

        int32_t rc = fastpath_vessel_chokepoint_eval(
            v_lats[v], v_lons[v], chokepoints, cp_count,
            &n_idx, &n_dist, &in_cp
        );
        if (rc != 0) return rc;

        if (out_nearest_indices) out_nearest_indices[v] = n_idx;
        if (out_nearest_distances) out_nearest_distances[v] = n_dist;
        if (out_in_chokepoints) out_in_chokepoints[v] = in_cp;
    }
    return 0;
}


// ===========================================================================
// Phase 13: Native C Hot-Path Accelerators for Quantitative Analytics & Risk
// ===========================================================================

#define C_PI 3.14159265358979323846
#define C_SQRT2 1.41421356237309504880
#define C_INV_SQRT_2PI 0.39894228040143267794

static inline double _c_norm_cdf(double x) {
    return 0.5 * (1.0 + erf(x / C_SQRT2));
}

static inline double _c_norm_pdf(double x) {
    return exp(-0.5 * x * x) * C_INV_SQRT_2PI;
}

static inline double _c_d1(double S, double K, double T, double r, double sigma) {
    if (T <= 0.0 || sigma <= 0.0) return 0.0;
    return (log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt(T));
}

static inline double _c_d2(double S, double K, double T, double r, double sigma) {
    if (T <= 0.0 || sigma <= 0.0) return 0.0;
    return _c_d1(S, K, T, r, sigma) - sigma * sqrt(T);
}

EXPORT double fastpath_bsm_price(double S, double K, double T, double r, double sigma, int32_t is_call) {
    if (T <= 0.0) {
        return is_call ? (S > K ? S - K : 0.0) : (K > S ? K - S : 0.0);
    }
    if (sigma <= 0.0) {
        double disc_k = K * exp(-r * T);
        return is_call ? (S > disc_k ? S - disc_k : 0.0) : (disc_k > S ? disc_k - S : 0.0);
    }

    double d1 = _c_d1(S, K, T, r, sigma);
    double d2 = _c_d2(S, K, T, r, sigma);

    if (is_call) {
        return S * _c_norm_cdf(d1) - K * exp(-r * T) * _c_norm_cdf(d2);
    } else {
        return K * exp(-r * T) * _c_norm_cdf(-d2) - S * _c_norm_cdf(-d1);
    }
}

EXPORT void fastpath_bsm_greeks(
    double S, double K, double T, double r, double sigma, int32_t is_call,
    double *out_greeks
) {
    if (!out_greeks) return;

    if (T <= 0.0 || sigma <= 0.0) {
        double delta = 0.0;
        if (is_call && S > K) delta = 1.0;
        else if (!is_call && S < K) delta = -1.0;
        out_greeks[0] = delta; // delta
        out_greeks[1] = 0.0;   // gamma
        out_greeks[2] = 0.0;   // theta
        out_greeks[3] = 0.0;   // vega
        out_greeks[4] = 0.0;   // rho
        out_greeks[5] = 0.0;   // vanna
        out_greeks[6] = 0.0;   // volga
        return;
    }

    double d1 = _c_d1(S, K, T, r, sigma);
    double d2 = _c_d2(S, K, T, r, sigma);

    double nd1 = _c_norm_cdf(d1);
    double nd2 = _c_norm_cdf(d2);
    double n_d1 = _c_norm_pdf(d1);
    double sqrt_t = sqrt(T);
    double exp_rt = exp(-r * T);

    double delta, theta_base, rho;
    if (is_call) {
        delta = nd1;
        theta_base = (-S * n_d1 * sigma) / (2.0 * sqrt_t) - r * K * exp_rt * nd2;
        rho = K * T * exp_rt * nd2 / 100.0;
    } else {
        delta = nd1 - 1.0;
        theta_base = (-S * n_d1 * sigma) / (2.0 * sqrt_t) + r * K * exp_rt * _c_norm_cdf(-d2);
        rho = -K * T * exp_rt * _c_norm_cdf(-d2) / 100.0;
    }

    double gamma = n_d1 / (S * sigma * sqrt_t);
    double theta = theta_base / 365.0;
    double vega = S * n_d1 * sqrt_t / 100.0;
    double vanna = -n_d1 * d2 / sigma;
    double volga = vega * 100.0 * d1 * d2 / sigma;

    out_greeks[0] = delta;
    out_greeks[1] = gamma;
    out_greeks[2] = theta;
    out_greeks[3] = vega;
    out_greeks[4] = rho;
    out_greeks[5] = vanna;
    out_greeks[6] = volga;
}

EXPORT double fastpath_binomial_price(
    double S, double K, double T, double r, double sigma, int32_t is_call, int32_t steps
) {
    if (T <= 0.0) {
        return is_call ? (S > K ? S - K : 0.0) : (K > S ? K - S : 0.0);
    }
    if (sigma <= 0.0) {
        return fastpath_bsm_price(S, K, T, r, sigma, is_call);
    }
    if (steps <= 0) steps = 200;
    if (steps > 512) steps = 512;

    double dt = T / (double)steps;
    double sqrt_dt = sqrt(dt);
    double u = exp(sigma * sqrt_dt);
    double d = 1.0 / u;
    double d2 = d * d;
    double a = exp(r * dt);
    double p = (a - d) / (u - d);
    double one_minus_p = 1.0 - p;
    double disc = exp(-r * dt);

    double prices[513];
    double spot_t = S * pow(u, steps);
    for (int i = 0; i <= steps; ++i) {
        prices[i] = is_call ? (spot_t > K ? spot_t - K : 0.0) : (K > spot_t ? K - spot_t : 0.0);
        spot_t *= d2;
    }

    for (int j = steps - 1; j >= 0; --j) {
        spot_t = S * pow(u, j);
        for (int i = 0; i <= j; ++i) {
            double continuation = disc * (p * prices[i] + one_minus_p * prices[i + 1]);
            double exercise = is_call ? (spot_t > K ? spot_t - K : 0.0) : (K > spot_t ? K - spot_t : 0.0);
            prices[i] = continuation > exercise ? continuation : exercise;
            spot_t *= d2;
        }
    }

    return prices[0];
}

EXPORT double fastpath_implied_volatility(
    double market_price, double S, double K, double T, double r, int32_t is_call,
    double tol, int32_t max_iter
) {
    if (tol <= 0.0) tol = 1e-6;
    if (max_iter <= 0) max_iter = 100;

    double sigma = 0.3;
    for (int iter = 0; iter < max_iter; ++iter) {
        double price = fastpath_bsm_price(S, K, T, r, sigma, is_call);
        double diff = price - market_price;
        if (fabs(diff) < tol) {
            return sigma;
        }

        double greeks[7];
        fastpath_bsm_greeks(S, K, T, r, sigma, is_call, greeks);
        double vega_raw = greeks[3] * 100.0;
        if (vega_raw < 1e-12) {
            break;
        }

        sigma -= diff / vega_raw;
        if (sigma < 0.001) sigma = 0.001;
        if (sigma > 5.0) sigma = 5.0;
    }
    return sigma;
}

// ---------------------------------------------------------------------------
// Technical Features: RSI, EMA, Bollinger Bands, ATR
// ---------------------------------------------------------------------------

EXPORT void fastpath_calc_rsi(const double *prices, int32_t n, int32_t period, double *out_rsi) {
    if (!prices || !out_rsi || n <= 0 || period <= 0) return;

    for (int32_t i = 0; i < n && i < period; ++i) {
        out_rsi[i] = NAN;
    }
    if (n <= period) return;

    double sum_gain = 0.0;
    double sum_loss = 0.0;
    for (int32_t i = 1; i <= period; ++i) {
        double change = prices[i] - prices[i - 1];
        if (change > 0.0) sum_gain += change;
        else sum_loss += fabs(change);
    }

    double avg_gain = sum_gain / (double)period;
    double avg_loss = sum_loss / (double)period;

    if (avg_loss == 0.0) {
        out_rsi[period] = 100.0;
    } else {
        double rs = avg_gain / avg_loss;
        out_rsi[period] = 100.0 - (100.0 / (1.0 + rs));
    }

    for (int32_t i = period + 1; i < n; ++i) {
        double change = prices[i] - prices[i - 1];
        double gain = change > 0.0 ? change : 0.0;
        double loss = change < 0.0 ? fabs(change) : 0.0;

        avg_gain = (avg_gain * (period - 1) + gain) / (double)period;
        avg_loss = (avg_loss * (period - 1) + loss) / (double)period;

        if (avg_loss == 0.0) {
            out_rsi[i] = 100.0;
        } else {
            double rs = avg_gain / avg_loss;
            out_rsi[i] = 100.0 - (100.0 / (1.0 + rs));
        }
    }
}

EXPORT void fastpath_calc_ema(const double *prices, int32_t n, int32_t period, double *out_ema) {
    if (!prices || !out_ema || n <= 0 || period <= 0) return;

    for (int32_t i = 0; i < n && i < period - 1; ++i) {
        out_ema[i] = NAN;
    }
    if (n < period) return;

    double sum = 0.0;
    for (int32_t i = 0; i < period; ++i) {
        sum += prices[i];
    }
    out_ema[period - 1] = sum / (double)period;

    double multiplier = 2.0 / (double)(period + 1);
    for (int32_t i = period; i < n; ++i) {
        out_ema[i] = (prices[i] - out_ema[i - 1]) * multiplier + out_ema[i - 1];
    }
}

EXPORT void fastpath_calc_bollinger(
    const double *prices, int32_t n, int32_t period, double num_std,
    double *out_upper, double *out_mid, double *out_lower
) {
    if (!prices || !out_upper || !out_mid || !out_lower || n <= 0 || period <= 0) return;

    for (int32_t i = 0; i < n; ++i) {
        if (i < period - 1) {
            out_upper[i] = NAN;
            out_mid[i] = NAN;
            out_lower[i] = NAN;
        } else {
            double sum = 0.0;
            for (int32_t k = i - period + 1; k <= i; ++k) {
                sum += prices[k];
            }
            double mean = sum / (double)period;
            out_mid[i] = mean;

            double sum_sq = 0.0;
            for (int32_t k = i - period + 1; k <= i; ++k) {
                double diff = prices[k] - mean;
                sum_sq += diff * diff;
            }
            double stdev = period > 1 ? sqrt(sum_sq / (double)(period - 1)) : 0.0;
            out_upper[i] = mean + num_std * stdev;
            out_lower[i] = mean - num_std * stdev;
        }
    }
}

EXPORT void fastpath_calc_atr(
    const double *highs, const double *lows, const double *closes,
    int32_t n, int32_t period, double *out_atr
) {
    if (!highs || !lows || !closes || !out_atr || n <= 0 || period <= 0) return;

    double *tr = (double *)malloc(n * sizeof(double));
    if (!tr) return;

    for (int32_t i = 0; i < n; ++i) {
        if (i == 0) {
            tr[i] = highs[i] - lows[i];
        } else {
            double hl = highs[i] - lows[i];
            double hc = fabs(highs[i] - closes[i - 1]);
            double lc = fabs(lows[i] - closes[i - 1]);
            double max_val = hl > hc ? hl : hc;
            tr[i] = max_val > lc ? max_val : lc;
        }
    }

    for (int32_t i = 0; i < n && i < period - 1; ++i) {
        out_atr[i] = NAN;
    }

    if (n >= period) {
        double tr_sum = 0.0;
        for (int32_t i = 0; i < period; ++i) {
            tr_sum += tr[i];
        }
        out_atr[period - 1] = tr_sum / (double)period;

        for (int32_t i = period; i < n; ++i) {
            out_atr[i] = (out_atr[i - 1] * (period - 1) + tr[i]) / (double)period;
        }
    }

    free(tr);
}

// ---------------------------------------------------------------------------
// High-Speed Portfolio Risk Engine: Monte Carlo Simulation
// ---------------------------------------------------------------------------

// 64-bit XorShift128+ PRNG state
typedef struct {
    uint64_t s[2];
} FastRngState;

static inline uint64_t _xorshift128plus(FastRngState *rng) {
    uint64_t s1 = rng->s[0];
    const uint64_t s0 = rng->s[1];
    rng->s[0] = s0;
    s1 ^= s1 << 23;
    rng->s[1] = s1 ^ s0 ^ (s1 >> 18) ^ (s0 >> 5);
    return rng->s[1] + s0;
}

static inline double _rng_uniform(FastRngState *rng) {
    return (_xorshift128plus(rng) >> 11) * (1.0 / 9007199254740992.0);
}

// Box-Muller Gaussian sample
static inline double _rng_gauss(FastRngState *rng, double mean, double stddev) {
    double u1 = _rng_uniform(rng);
    double u2 = _rng_uniform(rng);
    while (u1 <= 1e-15) u1 = _rng_uniform(rng);
    double z = sqrt(-2.0 * log(u1)) * cos(2.0 * C_PI * u2);
    return mean + z * stddev;
}

static int _cmp_doubles(const void *a, const void *b) {
    double da = *(const double *)a;
    double db = *(const double *)b;
    if (da < db) return -1;
    if (da > db) return 1;
    return 0;
}

EXPORT double fastpath_monte_carlo_var(
    double mean, double std_dev, int32_t n_simulations, int32_t horizon_days,
    double initial_val, double confidence, uint64_t seed
) {
    if (n_simulations <= 0 || horizon_days <= 0) return 0.0;

    double *sims = (double *)malloc(n_simulations * sizeof(double));
    if (!sims) return 0.0;

    FastRngState rng;
    rng.s[0] = seed ? seed : 42ULL;
    rng.s[1] = (seed ? seed : 42ULL) ^ 0x6a09e667f3bcc908ULL;
    if (rng.s[0] == 0 && rng.s[1] == 0) rng.s[0] = 1ULL;

    for (int32_t i = 0; i < n_simulations; ++i) {
        double sim_ret = 0.0;
        for (int32_t d = 0; d < horizon_days; ++d) {
            sim_ret += _rng_gauss(&rng, mean, std_dev);
        }
        sims[i] = sim_ret;
    }

    qsort(sims, n_simulations, sizeof(double), _cmp_doubles);

    int32_t idx = (int32_t)((1.0 - confidence) * (double)n_simulations);
    if (idx < 0) idx = 0;
    if (idx >= n_simulations) idx = n_simulations - 1;

    double var_pct = -sims[idx];
    free(sims);

    double res = var_pct * initial_val;
    return res > 0.0 ? res : 0.0;
}

// ---------------------------------------------------------------------------
// High-Speed FIX Protocol Checksum Calculation
// ---------------------------------------------------------------------------

EXPORT uint32_t fastpath_fix_checksum(const uint8_t *buf, int32_t len) {
    if (!buf || len <= 0) return 0;
    uint32_t sum = 0;
    for (int32_t i = 0; i < len; ++i) {
        sum += (uint32_t)buf[i];
    }
    return sum % 256;
}
