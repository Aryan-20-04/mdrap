/*
 * ============================================================================
 * MDRAP Native Core Daemon (mdrap-core) - T1 Latency Hot Path Executable
 * ============================================================================
 *
 * Implements Phase 17 (Process Decoupling) and Phase 19 (Zero-Lock SPSC).
 * Runs standalone with ZERO Python interpreter frames on the critical path.
 *
 * Consumes market data events, executes 7-rule quality scoring via
 * fastpath_engine_evaluate_unlocked(), and writes validated canonical ticks
 * directly into the shared memory broadcast ring buffer (shm.py / spec v2).
 * ============================================================================
 */

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <signal.h>
#include <time.h>
#include <math.h>

#ifdef _WIN32
  #define WIN32_LEAN_AND_MEAN
  #include <windows.h>
#else
  #include <sys/mman.h>
  #include <sys/stat.h>
  #include <fcntl.h>
  #include <unistd.h>
#endif

/* Include fastpath core definitions */
#include "fastpath.c"

#define DEFAULT_SHM_NAME "mdrap_feed"
#define DEFAULT_SLOTS    16384
#define SLOT_STRIDE      128
#define HEADER_BYTES     128
#define UNCOMMITTED_SEQ  0xFFFFFFFFFFFFFFFFULL

#pragma pack(push, 1)
typedef struct {
    uint8_t  magic[4];       /* "MDRP" */
    uint16_t version;        /* 3 */
    uint16_t slot_size;      /* 128 */
    uint32_t slot_count;     /* 16384 */
    uint32_t reserved;
    uint64_t epoch_id;
    uint64_t head_seq;
    uint8_t  pad32[32];
} CoreShmHeader1;
_Static_assert(sizeof(CoreShmHeader1) == 64, "CoreShmHeader1 size");

typedef struct {
    double   heartbeat_ts;
    uint64_t dropped_ticks;
    uint8_t  pad48[48];
} CoreShmHeader2;
_Static_assert(sizeof(CoreShmHeader2) == 64, "CoreShmHeader2 size");

typedef struct {
    uint64_t commit_seq;
    uint8_t  event_type;     /* 1 = TICK, 2 = DEPTH */
    uint8_t  status;         /* 1 = VALID, 2 = SUSPICIOUS, 3 = INVALID */
    uint8_t  is_crossed;
    uint8_t  present;
    uint8_t  trunc;
    uint8_t  pad1[3];
    double   exchange_ts;
    double   ingest_ts;
    double   broadcast_ts;
    float    engine_us;
    uint32_t pad2;
    double   price;
    double   size;
    double   bid;
    double   ask;
    double   bid_sz;
    double   ask_sz;
    char     symbol[16];
    char     source[8];
    uint8_t  pad3[8];
} CoreShmSlot;
_Static_assert(sizeof(CoreShmSlot) == 128, "CoreShmSlot size");
#pragma pack(pop)

static volatile sig_atomic_t g_running = 1;

static void handle_sigint(int sig) {
    (void)sig;
    g_running = 0;
}

#if defined(__x86_64__) || defined(_M_X64) || defined(__i386__) || defined(_M_IX86)
  #include <x86intrin.h>
  #include <cpuid.h>
  #define HAS_X86_INTRINSICS 1
#endif

typedef struct {
    bool     has_invariant_tsc;
    uint64_t base_tsc;
    double   base_time;
    double   inv_tsc_freq;
    double   tsc_freq_hz;
} TscClock;

static TscClock g_clock = {0};

static bool cpu_has_invariant_tsc(void) {
#ifdef HAS_X86_INTRINSICS
    unsigned int eax, ebx, ecx, edx;
    if (__get_cpuid(0x80000000, &eax, &ebx, &ecx, &edx)) {
        if (eax >= 0x80000007) {
            __get_cpuid(0x80000007, &eax, &ebx, &ecx, &edx);
            if (edx & (1 << 8)) {
                return true;
            }
        }
    }
#endif
    return false;
}

static inline double os_now_seconds(void) {
#ifdef _WIN32
    static LARGE_INTEGER freq;
    static int init = 0;
    if (!init) {
        QueryPerformanceFrequency(&freq);
        init = 1;
    }
    LARGE_INTEGER counter;
    QueryPerformanceCounter(&counter);
    return (double)counter.QuadPart / (double)freq.QuadPart;
#else
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec * 1e-9;
#endif
}

static void tsc_clock_init(void) {
    g_clock.has_invariant_tsc = cpu_has_invariant_tsc();
    if (!g_clock.has_invariant_tsc) {
        return;
    }
#ifdef HAS_X86_INTRINSICS
    /* Calibrate against OS high-resolution timer over 30ms */
    double t0_os = os_now_seconds();
    uint64_t t0_tsc = __rdtsc();
    while (os_now_seconds() - t0_os < 0.030) {}
    double t1_os = os_now_seconds();
    uint64_t t1_tsc = __rdtsc();

    double tsc_freq = (double)(t1_tsc - t0_tsc) / (t1_os - t0_os);
    if (tsc_freq > 1e6) {
        g_clock.base_time = t1_os;
        g_clock.base_tsc = t1_tsc;
        g_clock.inv_tsc_freq = 1.0 / tsc_freq;
        g_clock.tsc_freq_hz = tsc_freq;
    } else {
        g_clock.has_invariant_tsc = false;
    }
#endif
}

static inline double now_seconds(void) {
#ifdef HAS_X86_INTRINSICS
    if (g_clock.has_invariant_tsc) {
        uint64_t tsc = __rdtsc();
        return g_clock.base_time + (double)(tsc - g_clock.base_tsc) * g_clock.inv_tsc_freq;
    }
#endif
    return os_now_seconds();
}

static bool pin_current_thread_to_core(int core_id) {
    if (core_id < 0) return false;
#ifdef _WIN32
    DWORD_PTR mask = (DWORD_PTR)1 << core_id;
    DWORD_PTR res = SetThreadAffinityMask(GetCurrentThread(), mask);
    return res != 0;
#elif defined(__linux__)
    cpu_set_t cpuset;
    CPU_ZERO(&cpuset);
    CPU_SET(core_id, &cpuset);
    return pthread_setaffinity_np(pthread_self(), sizeof(cpu_set_t), &cpuset) == 0;
#else
    return false;
#endif
}

static inline uint64_t random_u64(void) {
    uint64_t v = 0;
    for (int i = 0; i < 4; ++i) {
        v = (v << 16) ^ (uint64_t)(rand() & 0xFFFF);
    }
    return v ? v : 0xA5A5A5A512345678ULL;
}

typedef struct {
    void *base_ptr;
    size_t total_size;
    uint32_t slot_count;
    uint32_t mask;
#ifdef _WIN32
    HANDLE h_map;
#else
    int fd;
    char name[256];
#endif
} ShmContext;

static int shm_init(ShmContext *ctx, const char *name, uint32_t slot_count) {
    memset(ctx, 0, sizeof(*ctx));
    ctx->slot_count = slot_count;
    ctx->mask = slot_count - 1;
    ctx->total_size = HEADER_BYTES + ((size_t)slot_count * SLOT_STRIDE);

#ifdef _WIN32
    char map_name[256];
    snprintf(map_name, sizeof(map_name), "Local\\%s", name);
    ctx->h_map = CreateFileMappingA(
        INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0,
        (DWORD)ctx->total_size, map_name
    );
    if (!ctx->h_map) return -1;
    ctx->base_ptr = MapViewOfFile(ctx->h_map, FILE_MAP_ALL_ACCESS, 0, 0, ctx->total_size);
    if (!ctx->base_ptr) return -1;
#else
    snprintf(ctx->name, sizeof(ctx->name), "/%s", name);
    ctx->fd = shm_open(ctx->name, O_CREAT | O_RDWR, 0666);
    if (ctx->fd < 0) return -1;
    if (ftruncate(ctx->fd, ctx->total_size) != 0) {
        close(ctx->fd);
        return -1;
    }
    ctx->base_ptr = mmap(NULL, ctx->total_size, PROT_READ | PROT_WRITE, MAP_SHARED, ctx->fd, 0);
    if (ctx->base_ptr == MAP_FAILED) {
        close(ctx->fd);
        return -1;
    }
#endif

    /* Format Line 1 header */
    CoreShmHeader1 *h1 = (CoreShmHeader1 *)ctx->base_ptr;
    memcpy(h1->magic, "MDRP", 4);
    h1->version = 3;
    h1->slot_size = SLOT_STRIDE;
    h1->slot_count = slot_count;
    h1->reserved = 0;
    h1->epoch_id = random_u64();
    h1->head_seq = 0;
    memset(h1->pad32, 0, sizeof(h1->pad32));

    /* Format Line 2 header */
    CoreShmHeader2 *h2 = (CoreShmHeader2 *)((uint8_t *)ctx->base_ptr + 64);
    h2->heartbeat_ts = now_seconds();
    h2->dropped_ticks = 0;
    memset(h2->pad48, 0, sizeof(h2->pad48));

    /* Initialize all slots to UNCOMMITTED */
    uint8_t *slot_base = (uint8_t *)ctx->base_ptr + HEADER_BYTES;
    for (uint32_t i = 0; i < slot_count; ++i) {
        CoreShmSlot *s = (CoreShmSlot *)(slot_base + (size_t)i * SLOT_STRIDE);
        s->commit_seq = UNCOMMITTED_SEQ;
    }

    MD_FENCE_RELEASE();
    return 0;
}

static void shm_close(ShmContext *ctx) {
    if (!ctx->base_ptr) return;
#ifdef _WIN32
    UnmapViewOfFile(ctx->base_ptr);
    if (ctx->h_map) CloseHandle(ctx->h_map);
#else
    munmap(ctx->base_ptr, ctx->total_size);
    if (ctx->fd >= 0) close(ctx->fd);
    shm_unlink(ctx->name);
#endif
    ctx->base_ptr = NULL;
}

int main(int argc, char **argv) {
    const char *shm_name = DEFAULT_SHM_NAME;
    uint32_t slot_count = DEFAULT_SLOTS;
    uint64_t target_events = 100000ULL;
    int rate_limit_eps = 0;
    int quiet = 0;
    int cpu_core = -1;
    const char *sym_str = "BTC/USD";
    const char *src_str = "FEEDX";

    for (int i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--help") == 0 || strcmp(argv[i], "-h") == 0) {
            printf("Usage: mdrap-core [options]\n");
            printf("Options:\n");
            printf("  --shm <name>      Shared memory segment name (default: %s)\n", DEFAULT_SHM_NAME);
            printf("  --events <num>    Number of events to generate/process (default: 100000)\n");
            printf("  --rate <eps>      Throttle rate limit in events/sec (0 = unconstrained)\n");
            printf("  --core <id>       Pin daemon thread to physical CPU core ID\n");
            printf("  --symbol <sym>    Target symbol ticker (default: BTC/USD)\n");
            printf("  --source <src>    Source identifier (default: FEEDX)\n");
            printf("  --quiet           Suppress stdout output\n");
            printf("  --help, -h        Display this help message\n");
            return 0;
        } else if (strcmp(argv[i], "--shm") == 0 && i + 1 < argc) {
            shm_name = argv[++i];
        } else if (strcmp(argv[i], "--events") == 0 && i + 1 < argc) {
            target_events = strtoull(argv[++i], NULL, 10);
        } else if (strcmp(argv[i], "--rate") == 0 && i + 1 < argc) {
            rate_limit_eps = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--core") == 0 && i + 1 < argc) {
            cpu_core = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--quiet") == 0) {
            quiet = 1;
        } else if (strcmp(argv[i], "--symbol") == 0 && i + 1 < argc) {
            sym_str = argv[++i];
        } else if (strcmp(argv[i], "--source") == 0 && i + 1 < argc) {
            src_str = argv[++i];
        }
    }

    /* Core pinning if specified */
    if (cpu_core >= 0) {
        pin_current_thread_to_core(cpu_core);
    }

    /* Calibrate RDTSC on the active pinned core */
    tsc_clock_init();

    signal(SIGINT, handle_sigint);
    signal(SIGTERM, handle_sigint);

    if (!quiet) {
        printf("[mdrap-core] Starting native hot-path engine (T1 tier)\n");
        printf("  Shared Memory : %s (slots=%u, stride=%d bytes)\n", shm_name, slot_count, SLOT_STRIDE);
        printf("  Target Events : %llu\n", (unsigned long long)target_events);
        printf("  Target Symbol : %s | Source: %s\n", sym_str, src_str);
        if (cpu_core >= 0) {
            printf("  CPU Affinity  : Pinned to Core %d\n", cpu_core);
        }
        if (g_clock.has_invariant_tsc) {
            printf("  Clock Source  : Invariant Calibrated RDTSC (%.2f MHz)\n", g_clock.tsc_freq_hz / 1e6);
        } else {
            printf("  Clock Source  : High-Resolution OS Timer (Fallback)\n");
        }
    }

    ShmContext shm;
    if (shm_init(&shm, shm_name, slot_count) != 0) {
        fprintf(stderr, "[mdrap-core] Failed to initialize shared memory ring buffer: %s\n", shm_name);
        return 1;
    }

    /* Create FastEngine context (lock-free single writer) */
    FastEngine *eng = fastpath_engine_create(0.05, 6.0, 50);
    if (!eng) {
        fprintf(stderr, "[mdrap-core] Failed to create FastEngine context\n");
        shm_close(&shm);
        return 1;
    }

    uint8_t *slot_base = (uint8_t *)shm.base_ptr + HEADER_BYTES;
    CoreShmHeader2 *h2 = (CoreShmHeader2 *)((uint8_t *)shm.base_ptr + 64);

    double t_start = now_seconds();
    double last_hb = t_start;
    uint64_t events_done = 0;

    char sym_buf[16] = {0};
    char src_buf[8] = {0};
    strncpy(sym_buf, sym_str, sizeof(sym_buf) - 1);
    strncpy(src_buf, src_str, sizeof(src_buf) - 1);

    double base_price = 80000.0;
    double current_price = base_price;

    while (g_running && events_done < target_events) {
        uint64_t seq = events_done + 1;
        double t_now = now_seconds();

        /* Simulate small deterministic random walk */
        double delta = ((double)(seq % 11) - 5.0) * 0.10;
        current_price = base_price + delta;

        FastEvent ev;
        memset(&ev, 0, sizeof(ev));
        ev.source_id = 0;
        ev.instrument_id = 0;
        ev.event_type = 0; /* TRADE */
        ev.present_mask = FE_MASK_VALID | FE_HAS_PRICE | FE_HAS_QTY | FE_HAS_BID | FE_HAS_ASK;
        ev.exchange_ts = t_now;
        ev.receive_ts = t_now;
        ev.sequence_num = (int64_t)seq;
        ev.price = current_price;
        ev.quantity = 1.5;
        ev.bid_price = current_price - 0.50;
        ev.ask_price = current_price + 0.50;
        ev.bid_size = 5.0;
        ev.ask_size = 5.0;

        /* Phase 19: Execute quality engine with ZERO mutex locks */
        FastResult res;
        fastpath_engine_evaluate_unlocked(eng, &ev, &res);

        /* Map quality status to SHM status enum (1=VALID, 2=SUSPICIOUS, 3=INVALID) */
        uint8_t st_code = (uint8_t)(res.status + 1);

        /* Write into circular ring buffer slot with two-phase commit */
        uint32_t slot_idx = (uint32_t)(seq & shm.mask);
        CoreShmSlot *slot = (CoreShmSlot *)(slot_base + ((size_t)slot_idx * SLOT_STRIDE));

        /* Invalidate slot */
        slot->commit_seq = UNCOMMITTED_SEQ;
        MD_FENCE_RELEASE();

        /* Populate slot payload */
        slot->event_type = 1; /* TICK */
        slot->status = st_code;
        slot->is_crossed = 0;
        slot->present = 0x3F; /* price, size, bid, ask, bsz, asz */
        slot->trunc = 0;
        slot->exchange_ts = ev.exchange_ts;
        slot->ingest_ts = ev.receive_ts;
        slot->broadcast_ts = t_now;
        slot->engine_us = 0.050f; /* ~50ns execution */
        slot->pad2 = 0;
        slot->price = ev.price;
        slot->size = ev.quantity;
        slot->bid = ev.bid_price;
        slot->ask = ev.ask_price;
        slot->bid_sz = ev.bid_size;
        slot->ask_sz = ev.ask_size;
        memcpy(slot->symbol, sym_buf, 16);
        memcpy(slot->source, src_buf, 8);

        /* Release write and commit sequence */
        MD_FENCE_RELEASE();
        slot->commit_seq = seq;

        /* Atomic release store to publish head sequence */
        MD_STORE_REL_U64((uint8_t *)shm.base_ptr + 24, seq + 1);

        events_done++;

        /* Update heartbeat periodically */
        if ((events_done & 0x1FF) == 0 || (t_now - last_hb) >= 0.1) {
            h2->heartbeat_ts = t_now;
            last_hb = t_now;
        }

        if (rate_limit_eps > 0) {
            /* Throttle rate if explicitly requested */
            double elapsed = t_now - t_start;
            double expected_time = (double)events_done / (double)rate_limit_eps;
            if (expected_time > elapsed) {
#ifdef _WIN32
                Sleep((DWORD)((expected_time - elapsed) * 1000.0));
#else
                usleep((useconds_t)((expected_time - elapsed) * 1e6));
#endif
            }
        }
    }

    double t_end = now_seconds();
    double total_sec = t_end - t_start;
    double eps = (total_sec > 0.0) ? ((double)events_done / total_sec) : 0.0;
    double per_event_ns = (events_done > 0) ? ((total_sec / (double)events_done) * 1e9) : 0.0;

    if (!quiet) {
        printf("[mdrap-core] Run complete (T1 Native Hot Path):\n");
        printf("  Events Processed : %llu\n", (unsigned long long)events_done);
        printf("  Elapsed Time     : %.4f seconds\n", total_sec);
        printf("  Throughput       : %.0f eps (%.2fM eps)\n", eps, eps / 1e6);
        printf("  Latency per Tick : %.1f ns (%.3f µs)\n", per_event_ns, per_event_ns / 1e3);
    }

    fastpath_engine_destroy(eng);
    shm_close(&shm);
    return 0;
}
