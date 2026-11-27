/*
 * ============================================================================
 *  MDRAP Native Python C-API Extension Module (_fastpath_c)
 * ============================================================================
 *  Eliminates ctypes FFI marshaling and boxing overhead for the Python compute loop.
 *  Calls fastpath engine and shared memory routines directly via Python C-API.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "fastpath.c"
#ifdef _WIN32
#include <windows.h>
#else
#include <sched.h>
#endif

typedef struct {
    FastEngine *engine;
    volatile long active_batch_calls;
    int closing;
} FastEngineHandle;

static const char *FAST_ENGINE_CAPSULE = "mdrap.FastEngine";

static FastEngineHandle *get_engine_handle(PyObject *obj) {
    FastEngineHandle *handle = (FastEngineHandle *)PyCapsule_GetPointer(
        obj, FAST_ENGINE_CAPSULE
    );
    if (!handle) return NULL;
    if (!handle->engine || handle->closing) {
        PyErr_SetString(PyExc_RuntimeError, "FastQualityEngine is closed");
        return NULL;
    }
    return handle;
}

static void engine_handle_capsule_destroy(PyObject *capsule) {
    FastEngineHandle *handle = (FastEngineHandle *)PyCapsule_GetPointer(
        capsule, FAST_ENGINE_CAPSULE
    );
    if (!handle) {
        PyErr_Clear();
        return;
    }
    if (handle->engine) fastpath_engine_destroy(handle->engine);
    free(handle);
}

static void yield_during_engine_close(void) {
#ifdef _WIN32
    Sleep(1);
#else
    sched_yield();
#endif
}

#if defined(_MSC_VER)
  #include <intrin.h>
  static inline uint64_t md_spsc_load_acq(volatile const uint64_t *p) {
      uint64_t v = *p;
      _ReadWriteBarrier();
      return v;
  }
  #define SPSC_LOAD_ACQ(p)       md_spsc_load_acq((volatile const uint64_t *)(p))
  #define SPSC_STORE_REL(p, v)   do { _ReadWriteBarrier(); *(volatile uint64_t *)(p) = (uint64_t)(v); } while (0)
#else
  #define SPSC_LOAD_ACQ(p)       __atomic_load_n((p), __ATOMIC_ACQUIRE)
  #define SPSC_STORE_REL(p, v)   __atomic_store_n((p), (v), __ATOMIC_RELEASE)
#endif

/* Pre-interned string keys for fast dict construction */
static PyObject *str_type = NULL;
static PyObject *str_seq = NULL;
static PyObject *str_sym = NULL;
static PyObject *str_source = NULL;
static PyObject *str_price = NULL;
static PyObject *str_size = NULL;
static PyObject *str_bid = NULL;
static PyObject *str_ask = NULL;
static PyObject *str_bid_size = NULL;
static PyObject *str_ask_size = NULL;
static PyObject *str_status = NULL;
static PyObject *str_is_crossed = NULL;
static PyObject *str_exchange_ts = NULL;
static PyObject *str_ingest_ts = NULL;
static PyObject *str_broadcast_ts = NULL;
static PyObject *str_engine_us = NULL;
static PyObject *str_micro_price = NULL;
static PyObject *str_ofi = NULL;
static PyObject *str_bids = NULL;
static PyObject *str_asks = NULL;

static PyObject *val_tick = NULL;
static PyObject *val_depth = NULL;
static PyObject *val_valid = NULL;
static PyObject *val_suspicious = NULL;
static PyObject *val_invalid = NULL;
static PyObject *val_unknown = NULL;

/* ---------------------------------------------------------------------------
 * Fast Evaluation API
 * ---------------------------------------------------------------------------*/

static PyObject *py_eval_fast2(PyObject *self, PyObject *const *args, Py_ssize_t nargs) {
    (void)self;
    if (nargs < 14) {
        PyErr_SetString(PyExc_TypeError, "eval_fast2 requires 14 positional arguments");
        return NULL;
    }
    FastEngineHandle *handle = get_engine_handle(args[0]);
    if (!handle) return NULL;

    int32_t s_id = (int32_t)PyLong_AsLong(args[1]);
    int32_t i_id = (int32_t)PyLong_AsLong(args[2]);
    int32_t ev_type = (int32_t)PyLong_AsLong(args[3]);
    double ex_ts = PyFloat_AsDouble(args[4]);
    double rc_ts = PyFloat_AsDouble(args[5]);
    int64_t seq = (int64_t)PyLong_AsLongLong(args[6]);
    double price = PyFloat_AsDouble(args[7]);
    double qty = PyFloat_AsDouble(args[8]);
    double bid = PyFloat_AsDouble(args[9]);
    double ask = PyFloat_AsDouble(args[10]);
    double bid_sz = PyFloat_AsDouble(args[11]);
    double ask_sz = PyFloat_AsDouble(args[12]);
    uint32_t pm = (uint32_t)PyLong_AsUnsignedLong(args[13]);

    if (PyErr_Occurred()) return NULL;

    uint64_t packed = fastpath_engine_eval_fast2(
        handle->engine, s_id, i_id, ev_type, ex_ts, rc_ts, seq, price, qty, bid, ask, bid_sz, ask_sz, pm
    );

    return PyLong_FromUnsignedLongLong(packed);
}

/* ---------------------------------------------------------------------------
 * Engine Lifecycle API
 * ---------------------------------------------------------------------------*/

static PyObject *py_engine_create(PyObject *self, PyObject *args) {
    (void)self;
    double staleness_thresh = 0.5;
    double stddev = 6.0;
    int32_t window = 128;
    if (!PyArg_ParseTuple(args, "|ddi", &staleness_thresh, &stddev, &window)) {
        return NULL;
    }
    FastEngine *eng = fastpath_engine_create(staleness_thresh, stddev, window);
    if (!eng) {
        PyErr_SetString(PyExc_RuntimeError, "Failed to allocate FastEngine");
        return NULL;
    }
    FastEngineHandle *handle = (FastEngineHandle *)calloc(1, sizeof(FastEngineHandle));
    if (!handle) {
        fastpath_engine_destroy(eng);
        return PyErr_NoMemory();
    }
    handle->engine = eng;
    PyObject *capsule = PyCapsule_New(handle, FAST_ENGINE_CAPSULE, engine_handle_capsule_destroy);
    if (!capsule) {
        fastpath_engine_destroy(eng);
        free(handle);
    }
    return capsule;
}

static PyObject *py_engine_address(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule = NULL;
    if (!PyArg_ParseTuple(args, "O", &capsule)) return NULL;
    FastEngineHandle *handle = get_engine_handle(capsule);
    if (!handle) return NULL;
    return PyLong_FromUnsignedLongLong((uintptr_t)handle->engine);
}

static PyObject *py_engine_destroy(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule = NULL;
    if (!PyArg_ParseTuple(args, "O", &capsule)) {
        return NULL;
    }
    FastEngineHandle *handle = (FastEngineHandle *)PyCapsule_GetPointer(
        capsule, FAST_ENGINE_CAPSULE
    );
    if (!handle) return NULL;
    if (!handle->engine) Py_RETURN_NONE;
    handle->closing = 1;
    while (handle->active_batch_calls > 0) {
        Py_BEGIN_ALLOW_THREADS
        yield_during_engine_close();
        Py_END_ALLOW_THREADS
    }
    FastEngine *eng = handle->engine;
    handle->engine = NULL;
    handle->closing = 0;
    fastpath_engine_destroy(eng);
    Py_RETURN_NONE;
}

static PyObject *py_engine_configure(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule = NULL;
    int32_t price_min_samples = -1;
    int32_t price_reseed_after = -1;
    double price_sigma_floor_rel = -1.0;
    double price_reseed_band_rel = -1.0;
    double max_future_skew_s = -1.0;
    int64_t seq_jump_limit = -1;
    int32_t unseq_dup_status = -1;
    int32_t allow_negative = -1;

    if (!PyArg_ParseTuple(
        args, "OiidddLii",
        &capsule,
        &price_min_samples,
        &price_reseed_after,
        &price_sigma_floor_rel,
        &price_reseed_band_rel,
        &max_future_skew_s,
        &seq_jump_limit,
        &unseq_dup_status,
        &allow_negative
    )) {
        return NULL;
    }

    FastEngineHandle *handle = get_engine_handle(capsule);
    if (!handle) return NULL;
    {
        fastpath_engine_configure(
            handle->engine,
            price_min_samples,
            price_reseed_after,
            price_sigma_floor_rel,
            price_reseed_band_rel,
            max_future_skew_s,
            seq_jump_limit,
            unseq_dup_status,
            allow_negative
        );
    }
    Py_RETURN_NONE;
}

static PyObject *py_engine_reset(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule = NULL;
    if (!PyArg_ParseTuple(args, "O", &capsule)) {
        return NULL;
    }
    FastEngineHandle *handle = get_engine_handle(capsule);
    if (!handle) return NULL;
    fastpath_engine_reset(handle->engine);
    Py_RETURN_NONE;
}

static PyObject *py_engine_evaluate_batch(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule = NULL;
    uint64_t events_ptr = 0;
    uint64_t results_ptr = 0;
    int32_t count = 0;
    if (!PyArg_ParseTuple(args, "OKKi", &capsule, &events_ptr, &results_ptr, &count)) {
        return NULL;
    }
    FastEngineHandle *handle = get_engine_handle(capsule);
    if (!handle) return NULL;
    FastEngine *eng = handle->engine;
    const FastEvent *evs = (const FastEvent *)(uintptr_t)events_ptr;
    FastResult *res = (FastResult *)(uintptr_t)results_ptr;
    if (!eng || !evs || !res || count <= 0) {
        Py_RETURN_NONE;
    }
    handle->active_batch_calls++;
    Py_BEGIN_ALLOW_THREADS
    fastpath_engine_evaluate_batch(eng, evs, res, count);
    Py_END_ALLOW_THREADS
    handle->active_batch_calls--;
    Py_RETURN_NONE;
}

static PyObject *py_engine_source_reset(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule = NULL;
    int32_t source_id = 0;
    if (!PyArg_ParseTuple(args, "Oi", &capsule, &source_id)) {
        return NULL;
    }
    FastEngineHandle *handle = get_engine_handle(capsule);
    if (!handle) return NULL;
    fastpath_engine_source_reset(handle->engine, source_id);
    Py_RETURN_NONE;
}

static PyObject *py_engine_set_hash_seed(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *capsule = NULL;
    uint64_t seed = 0;
    if (!PyArg_ParseTuple(args, "OK", &capsule, &seed)) {
        return NULL;
    }
    FastEngineHandle *handle = get_engine_handle(capsule);
    if (!handle) return NULL;
    fastpath_engine_set_hash_seed(handle->engine, seed);
    Py_RETURN_NONE;
}

/* ---------------------------------------------------------------------------
 * Shared Memory Acceleration API
 * ---------------------------------------------------------------------------*/

static PyObject *py_shm_read_slot_v3(PyObject *self, PyObject *const *args, Py_ssize_t nargs) {
    (void)self;
    if (nargs < 3) {
        PyErr_SetString(PyExc_TypeError, "shm_read_slot_v3 requires (buffer, slot_count, target_seq)");
        return NULL;
    }

    Py_buffer view;
    if (PyObject_GetBuffer(args[0], &view, PyBUF_SIMPLE) != 0) {
        return NULL;
    }

    uint32_t slot_count = (uint32_t)PyLong_AsUnsignedLong(args[1]);
    uint64_t target_seq = PyLong_AsUnsignedLongLong(args[2]);

    ShmSlotV3 slot;
    int32_t rc = fastpath_shm_read_slot_v3((const uint8_t *)view.buf, (size_t)view.len, slot_count, target_seq, &slot);
    PyBuffer_Release(&view);

    if (rc != 1) {
        Py_RETURN_NONE;
    }

    size_t sym_len = 0u;
    while (sym_len < 16u && slot.symbol[sym_len] != '\0') ++sym_len;

    PyObject *st_val = val_unknown;
    if (slot.status == 1) st_val = val_valid;
    else if (slot.status == 2) st_val = val_suspicious;
    else if (slot.status == 3) st_val = val_invalid;

    PyObject *dict = PyDict_New();
    if (!dict) return NULL;

    #define SET_NEW_ITEM(k, v) do { PyObject *_val = (v); if (!_val) { Py_DECREF(dict); return NULL; } if (PyDict_SetItem(dict, (k), _val) < 0) { Py_DECREF(_val); Py_DECREF(dict); return NULL; } Py_DECREF(_val); } while (0)

    SET_NEW_ITEM(str_seq, PyLong_FromUnsignedLongLong(slot.commit_seq));
    SET_NEW_ITEM(str_sym, PyUnicode_DecodeUTF8(slot.symbol, (Py_ssize_t)sym_len, "replace"));
    PyDict_SetItem(dict, str_status, st_val);
    PyDict_SetItem(dict, str_is_crossed, slot.is_crossed ? Py_True : Py_False);
    SET_NEW_ITEM(str_exchange_ts, PyFloat_FromDouble(slot.exchange_ts));
    SET_NEW_ITEM(str_ingest_ts, PyFloat_FromDouble(slot.ingest_ts));
    SET_NEW_ITEM(str_broadcast_ts, PyFloat_FromDouble(slot.broadcast_ts));
    SET_NEW_ITEM(str_engine_us, PyFloat_FromDouble((double)slot.engine_us));

    if (slot.present & SHM3_PRESENT_BID) SET_NEW_ITEM(str_bid, PyFloat_FromDouble(slot.bid));
    else PyDict_SetItem(dict, str_bid, Py_None);

    if (slot.present & SHM3_PRESENT_ASK) SET_NEW_ITEM(str_ask, PyFloat_FromDouble(slot.ask));
    else PyDict_SetItem(dict, str_ask, Py_None);

    if (slot.present & SHM3_PRESENT_BSZ) SET_NEW_ITEM(str_bid_size, PyFloat_FromDouble(slot.bid_sz));
    else PyDict_SetItem(dict, str_bid_size, Py_None);

    if (slot.present & SHM3_PRESENT_ASZ) SET_NEW_ITEM(str_ask_size, PyFloat_FromDouble(slot.ask_sz));
    else PyDict_SetItem(dict, str_ask_size, Py_None);

    if (slot.event_type == 2) {
        PyDict_SetItem(dict, str_type, val_depth);
        if (slot.present & SHM3_PRESENT_PRICE) SET_NEW_ITEM(str_micro_price, PyFloat_FromDouble(slot.price));
        else PyDict_SetItem(dict, str_micro_price, Py_None);

        if (slot.present & SHM3_PRESENT_SIZE) SET_NEW_ITEM(str_ofi, PyFloat_FromDouble(slot.size));
        else PyDict_SetItem(dict, str_ofi, Py_None);

        if (slot.present & SHM3_PRESENT_BID) {
            PyObject *bsz_obj = (slot.present & SHM3_PRESENT_BSZ) ? PyFloat_FromDouble(slot.bid_sz) : (Py_INCREF(Py_None), Py_None);
            if (!bsz_obj) { Py_DECREF(dict); return NULL; }
            PyObject *bid_item = Py_BuildValue("[dOs]", slot.bid, bsz_obj, "AGG");
            Py_DECREF(bsz_obj);
            if (!bid_item) { Py_DECREF(dict); return NULL; }
            PyObject *bids_list = PyList_New(1);
            if (!bids_list) { Py_DECREF(bid_item); Py_DECREF(dict); return NULL; }
            PyList_SET_ITEM(bids_list, 0, bid_item);
            PyDict_SetItem(dict, str_bids, bids_list);
            Py_DECREF(bids_list);
        } else {
            PyObject *empty_bids = PyList_New(0);
            if (!empty_bids) { Py_DECREF(dict); return NULL; }
            PyDict_SetItem(dict, str_bids, empty_bids);
            Py_DECREF(empty_bids);
        }

        if (slot.present & SHM3_PRESENT_ASK) {
            PyObject *asz_obj = (slot.present & SHM3_PRESENT_ASZ) ? PyFloat_FromDouble(slot.ask_sz) : (Py_INCREF(Py_None), Py_None);
            if (!asz_obj) { Py_DECREF(dict); return NULL; }
            PyObject *ask_item = Py_BuildValue("[dOs]", slot.ask, asz_obj, "AGG");
            Py_DECREF(asz_obj);
            if (!ask_item) { Py_DECREF(dict); return NULL; }
            PyObject *asks_list = PyList_New(1);
            if (!asks_list) { Py_DECREF(ask_item); Py_DECREF(dict); return NULL; }
            PyList_SET_ITEM(asks_list, 0, ask_item);
            PyDict_SetItem(dict, str_asks, asks_list);
            Py_DECREF(asks_list);
        } else {
            PyObject *empty_asks = PyList_New(0);
            if (!empty_asks) { Py_DECREF(dict); return NULL; }
            PyDict_SetItem(dict, str_asks, empty_asks);
            Py_DECREF(empty_asks);
        }
    } else {
        size_t src_len = 0u;
        const char *src_ptr = shm3_decode_source(slot.source, &src_len);
        SET_NEW_ITEM(str_source, PyUnicode_DecodeUTF8(src_ptr, (Py_ssize_t)src_len, "replace"));

        if (slot.present & SHM3_PRESENT_PRICE) SET_NEW_ITEM(str_price, PyFloat_FromDouble(slot.price));
        else PyDict_SetItem(dict, str_price, Py_None);

        if (slot.present & SHM3_PRESENT_SIZE) SET_NEW_ITEM(str_size, PyFloat_FromDouble(slot.size));
        else PyDict_SetItem(dict, str_size, Py_None);

        PyDict_SetItem(dict, str_type, val_tick);
    }

    #undef SET_NEW_ITEM
    return dict;
}

static PyObject *py_shm_write_tick_v3(PyObject *self, PyObject *const *args, Py_ssize_t nargs) {
    (void)self;
    if (nargs < 18) {
        PyErr_SetString(PyExc_TypeError, "shm_write_tick_v3 requires 18 arguments");
        return NULL;
    }

    Py_buffer view;
    if (PyObject_GetBuffer(args[0], &view, PyBUF_WRITABLE) != 0) {
        return NULL;
    }

    uint32_t slot_count = (uint32_t)PyLong_AsUnsignedLong(args[1]);
    uint64_t seq = PyLong_AsUnsignedLongLong(args[2]);

    const char *symbol = NULL;
    if (args[3] == Py_None) {
        symbol = NULL;
    } else if (PyUnicode_Check(args[3])) {
        Py_ssize_t sym_n = 0;
        symbol = PyUnicode_AsUTF8AndSize(args[3], &sym_n);
        if (!symbol) {
            PyBuffer_Release(&view);
            PyErr_Clear();
            PyErr_SetString(PyExc_ValueError, "SHM symbol must be valid UTF-8");
            return NULL;
        }
        if (memchr(symbol, '\0', (size_t)sym_n) != NULL) {
            PyBuffer_Release(&view);
            PyErr_SetString(PyExc_ValueError, "SHM symbol must not contain embedded NUL bytes");
            return NULL;
        }
    } else if (PyBytes_Check(args[3])) {
        Py_ssize_t sym_n = PyBytes_GET_SIZE(args[3]);
        symbol = PyBytes_AS_STRING(args[3]);
        if (memchr(symbol, '\0', (size_t)sym_n) != NULL) {
            PyBuffer_Release(&view);
            PyErr_SetString(PyExc_ValueError, "SHM symbol must not contain embedded NUL bytes");
            return NULL;
        }
    } else {
        PyBuffer_Release(&view);
        PyErr_SetString(PyExc_ValueError, "SHM symbol must be str");
        return NULL;
    }

    const char *source = NULL;
    if (args[4] == Py_None) {
        source = NULL;
    } else if (PyUnicode_Check(args[4])) {
        Py_ssize_t src_n = 0;
        source = PyUnicode_AsUTF8AndSize(args[4], &src_n);
        if (!source) {
            PyBuffer_Release(&view);
            PyErr_Clear();
            PyErr_SetString(PyExc_ValueError, "SHM source must be ASCII");
            return NULL;
        }
        if (memchr(source, '\0', (size_t)src_n) != NULL) {
            PyBuffer_Release(&view);
            PyErr_SetString(PyExc_ValueError, "SHM source must not contain embedded NUL bytes");
            return NULL;
        }
    } else if (PyBytes_Check(args[4])) {
        Py_ssize_t src_n = PyBytes_GET_SIZE(args[4]);
        source = PyBytes_AS_STRING(args[4]);
        if (memchr(source, '\0', (size_t)src_n) != NULL) {
            PyBuffer_Release(&view);
            PyErr_SetString(PyExc_ValueError, "SHM source must not contain embedded NUL bytes");
            return NULL;
        }
    } else {
        PyBuffer_Release(&view);
        PyErr_SetString(PyExc_ValueError, "SHM source must be str");
        return NULL;
    }

    double price = PyFloat_AsDouble(args[5]);
    double size = PyFloat_AsDouble(args[6]);
    double bid = PyFloat_AsDouble(args[7]);
    double ask = PyFloat_AsDouble(args[8]);
    double bid_sz = PyFloat_AsDouble(args[9]);
    double ask_sz = PyFloat_AsDouble(args[10]);
    uint8_t status = (uint8_t)PyLong_AsLong(args[11]);
    uint8_t is_crossed = (uint8_t)PyLong_AsLong(args[12]);
    uint8_t present = (uint8_t)PyLong_AsLong(args[13]);
    double ex_ts = PyFloat_AsDouble(args[14]);
    double in_ts = PyFloat_AsDouble(args[15]);
    double bc_ts = PyFloat_AsDouble(args[16]);
    float eng_us = (float)PyFloat_AsDouble(args[17]);

    if (PyErr_Occurred()) {
        PyBuffer_Release(&view);
        return NULL;
    }

    int32_t res = fastpath_shm_write_tick_v3(
        (uint8_t *)view.buf, (size_t)view.len, slot_count, seq,
        symbol, source, price, size, bid, ask, bid_sz, ask_sz,
        status, is_crossed, present, ex_ts, in_ts, bc_ts, eng_us
    );

    PyBuffer_Release(&view);

    if (res == -3) {
        PyErr_Format(
            PyExc_ValueError,
            "SHM source %R is invalid, uses reserved wire prefix '@MDRP', or exceeds 8-byte v3 limit",
            args[4]
        );
        return NULL;
    }
    if (res == -4) {
        PyErr_Format(
            PyExc_ValueError,
            "SHM symbol %R is invalid UTF-8 or exceeds 16-byte v3 wire limit",
            args[3]
        );
        return NULL;
    }

    return PyLong_FromLong(res);
}

static PyObject *py_shm_head_v3(PyObject *self, PyObject *buf_obj) {
    (void)self;
    Py_buffer view;
    if (PyObject_GetBuffer(buf_obj, &view, PyBUF_SIMPLE) != 0) {
        return NULL;
    }
    uint64_t head = fastpath_shm_head_v3((const uint8_t *)view.buf);
    PyBuffer_Release(&view);
    return PyLong_FromUnsignedLongLong(head);
}

static PyObject *py_shm_epoch_v3(PyObject *self, PyObject *buf_obj) {
    (void)self;
    Py_buffer view;
    if (PyObject_GetBuffer(buf_obj, &view, PyBUF_SIMPLE) != 0) {
        return NULL;
    }
    uint64_t epoch = fastpath_shm_epoch_v3((const uint8_t *)view.buf);
    PyBuffer_Release(&view);
    return PyLong_FromUnsignedLongLong(epoch);
}

/* ---------------------------------------------------------------------------
 * Native C11 Atomic Lock-Free SPSC Ring Queue Type
 * ---------------------------------------------------------------------------*/

#ifdef _MSC_VER
#define SPSC_ALIGNED_64 __declspec(align(64))
#else
#define SPSC_ALIGNED_64 __attribute__((aligned(64)))
#endif

typedef struct {
    /* Producer cache line (64 bytes) */
    SPSC_ALIGNED_64 uint64_t head;
    uint64_t cached_tail;
    uint8_t _pad_prod[48];

    /* Consumer cache line (64 bytes) */
    SPSC_ALIGNED_64 uint64_t tail;
    uint64_t cached_head;
    uint8_t _pad_cons[48];

    /* Shared read-only configuration cache line (64 bytes) */
    SPSC_ALIGNED_64 uint32_t capacity;
    uint32_t mask;
    PyObject **items;
    uint8_t _pad_shared[48];
} NativeRing;

typedef struct {
    PyObject_HEAD
    NativeRing *ring;
} NativeSpscQueueObject;

static void NativeSpscQueue_dealloc(NativeSpscQueueObject *self) {
    NativeRing *r = self->ring;
    if (r) {
        if (r->items) {
            uint64_t tail = r->tail;
            uint64_t head = r->head;
            while (tail != head) {
                uint32_t idx = (uint32_t)(tail & r->mask);
                Py_XDECREF(r->items[idx]);
                r->items[idx] = NULL;
                tail++;
            }
            free(r->items);
            r->items = NULL;
        }
#if defined(_WIN32) || defined(_WIN64)
        _aligned_free(r);
#else
        free(r);
#endif
        self->ring = NULL;
    }
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *NativeSpscQueue_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    (void)args;
    (void)kwds;
    NativeSpscQueueObject *self = (NativeSpscQueueObject *)type->tp_alloc(type, 0);
    if (self) {
        self->ring = NULL;
    }
    return (PyObject *)self;
}

static int NativeSpscQueue_init(NativeSpscQueueObject *self, PyObject *args, PyObject *kwds) {
    int req_cap = 65536;
    static char *kwlist[] = {"capacity", NULL};
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|i", kwlist, &req_cap)) {
        return -1;
    }
    if (req_cap <= 0) req_cap = 16;
    uint32_t cap = 1;
    while (cap < (uint32_t)req_cap && cap < 0x40000000) {
        cap <<= 1;
    }

#if defined(_WIN32) || defined(_WIN64)
    NativeRing *r = (NativeRing *)_aligned_malloc(sizeof(NativeRing), 64);
#else
    NativeRing *r = NULL;
    if (posix_memalign((void **)&r, 64, sizeof(NativeRing)) != 0) {
        r = NULL;
    }
#endif
    if (!r) {
        PyErr_NoMemory();
        return -1;
    }
    memset(r, 0, sizeof(NativeRing));
    r->capacity = cap;
    r->mask = cap - 1;
    r->items = (PyObject **)calloc(cap, sizeof(PyObject *));
    if (!r->items) {
#if defined(_WIN32) || defined(_WIN64)
        _aligned_free(r);
#else
        free(r);
#endif
        PyErr_NoMemory();
        return -1;
    }
    self->ring = r;
    return 0;
}

static PyObject *NativeSpscQueue_offer(NativeSpscQueueObject *self, PyObject *item) {
    NativeRing *r = self->ring;
    if (!r) {
        PyErr_SetString(PyExc_RuntimeError, "Queue not initialized");
        return NULL;
    }
    uint64_t head = r->head;
    uint64_t tail = r->cached_tail;
    if (head - tail >= r->capacity) {
        tail = SPSC_LOAD_ACQ(&r->tail);
        r->cached_tail = tail;
        if (head - tail >= r->capacity) {
            Py_RETURN_FALSE;
        }
    }
    Py_INCREF(item);
    r->items[head & r->mask] = item;
    SPSC_STORE_REL(&r->head, head + 1);
    Py_RETURN_TRUE;
}

static PyObject *NativeSpscQueue_poll(NativeSpscQueueObject *self, PyObject *Py_UNUSED(ignored)) {
    NativeRing *r = self->ring;
    if (!r) {
        PyErr_SetString(PyExc_RuntimeError, "Queue not initialized");
        return NULL;
    }
    uint64_t tail = r->tail;
    uint64_t head = r->cached_head;
    if (tail == head) {
        head = SPSC_LOAD_ACQ(&r->head);
        r->cached_head = head;
        if (tail == head) {
            Py_RETURN_NONE;
        }
    }
    uint32_t idx = (uint32_t)(tail & r->mask);
    PyObject *item = r->items[idx];
    r->items[idx] = NULL;
    SPSC_STORE_REL(&r->tail, tail + 1);
    return item;
}

static PyObject *NativeSpscQueue_qsize(NativeSpscQueueObject *self, PyObject *Py_UNUSED(ignored)) {
    NativeRing *r = self->ring;
    if (!r) return PyLong_FromLong(0);
    uint64_t head = SPSC_LOAD_ACQ(&r->head);
    uint64_t tail = SPSC_LOAD_ACQ(&r->tail);
    int64_t diff = (int64_t)head - (int64_t)tail;
    return PyLong_FromLongLong(diff > 0 ? diff : 0);
}

static PyObject *NativeSpscQueue_empty(NativeSpscQueueObject *self, PyObject *Py_UNUSED(ignored)) {
    NativeRing *r = self->ring;
    if (!r) Py_RETURN_TRUE;
    uint64_t head = SPSC_LOAD_ACQ(&r->head);
    uint64_t tail = SPSC_LOAD_ACQ(&r->tail);
    if (head == tail) {
        Py_RETURN_TRUE;
    } else {
        Py_RETURN_FALSE;
    }
}

static PyObject *NativeSpscQueue_full(NativeSpscQueueObject *self, PyObject *Py_UNUSED(ignored)) {
    NativeRing *r = self->ring;
    if (!r) Py_RETURN_FALSE;
    uint64_t head = SPSC_LOAD_ACQ(&r->head);
    uint64_t tail = SPSC_LOAD_ACQ(&r->tail);
    int64_t diff = (int64_t)head - (int64_t)tail;
    if (diff >= (int64_t)r->capacity) {
        Py_RETURN_TRUE;
    } else {
        Py_RETURN_FALSE;
    }
}

static PyObject *NativeSpscQueue_get_capacity(NativeSpscQueueObject *self, void *closure) {
    (void)closure;
    if (!self->ring) return PyLong_FromLong(0);
    return PyLong_FromUnsignedLong(self->ring->capacity);
}

static PyObject *NativeSpscQueue_get_mask(NativeSpscQueueObject *self, void *closure) {
    (void)closure;
    if (!self->ring) return PyLong_FromLong(0);
    return PyLong_FromUnsignedLong(self->ring->mask);
}

static PyGetSetDef NativeSpscQueue_getseters[] = {
    {"capacity", (getter)NativeSpscQueue_get_capacity, NULL, "Queue capacity", NULL},
    {"mask", (getter)NativeSpscQueue_get_mask, NULL, "Queue bitmask", NULL},
    {NULL, NULL, NULL, NULL, NULL}
};

static PyMethodDef NativeSpscQueue_methods[] = {
    {"offer", (PyCFunction)NativeSpscQueue_offer, METH_O, "Enqueue item without blocking. Returns False if full."},
    {"poll", (PyCFunction)NativeSpscQueue_poll, METH_NOARGS, "Dequeue item without blocking. Returns None if empty."},
    {"qsize", (PyCFunction)NativeSpscQueue_qsize, METH_NOARGS, "Current queue depth."},
    {"empty", (PyCFunction)NativeSpscQueue_empty, METH_NOARGS, "True if queue is empty."},
    {"full", (PyCFunction)NativeSpscQueue_full, METH_NOARGS, "True if queue is full."},
    {NULL, NULL, 0, NULL}
};

static PyTypeObject NativeSpscQueueType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "_fastpath_c.NativeSpscQueue",
    .tp_doc = PyDoc_STR("High-performance lock-free C11 atomic SPSC circular ring buffer"),
    .tp_basicsize = sizeof(NativeSpscQueueObject),
    .tp_itemsize = 0,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_new = NativeSpscQueue_new,
    .tp_init = (initproc)NativeSpscQueue_init,
    .tp_dealloc = (destructor)NativeSpscQueue_dealloc,
    .tp_methods = NativeSpscQueue_methods,
    .tp_getset = NativeSpscQueue_getseters,
};

/* ---------------------------------------------------------------------------
 * Method Table & Module Definition
 * ---------------------------------------------------------------------------*/

static PyMethodDef FastpathMethods[] = {
    {"eval_fast2", (PyCFunction)py_eval_fast2, METH_FASTCALL, "Evaluate an event on FastEngine returning packed (status << 32 | reason_mask)"},
    {"engine_create", (PyCFunction)py_engine_create, METH_VARARGS, "Create FastEngine context"},
    {"engine_address", (PyCFunction)py_engine_address, METH_VARARGS, "Return native address for diagnostics"},
    {"engine_destroy", (PyCFunction)py_engine_destroy, METH_VARARGS, "Destroy FastEngine context"},
    {"engine_configure", (PyCFunction)py_engine_configure, METH_VARARGS, "Configure FastEngine thresholds"},
    {"engine_reset", (PyCFunction)py_engine_reset, METH_VARARGS, "Reset FastEngine internal statistics"},
    {"engine_evaluate_batch", (PyCFunction)py_engine_evaluate_batch, METH_VARARGS, "Evaluate a batch of FastEvents in C releasing GIL"},
    {"engine_source_reset", (PyCFunction)py_engine_source_reset, METH_VARARGS, "Reset a single source session in FastEngine"},
    {"engine_set_hash_seed", (PyCFunction)py_engine_set_hash_seed, METH_VARARGS, "Set hash seed for FastEngine"},
    {"shm_read_slot_v3", (PyCFunction)py_shm_read_slot_v3, METH_FASTCALL, "Read slot from SHM buffer v3"},
    {"shm_write_tick_v3", (PyCFunction)py_shm_write_tick_v3, METH_FASTCALL, "Write tick to SHM buffer v3"},
    {"shm_head_v3", (PyCFunction)py_shm_head_v3, METH_O, "Read head from SHM buffer v3"},
    {"shm_epoch_v3", (PyCFunction)py_shm_epoch_v3, METH_O, "Read epoch from SHM buffer v3"},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef fastpathmodule = {
    PyModuleDef_HEAD_INIT,
    "_fastpath_c",
    "MDRAP Native Python C-API Extension Module",
    -1,
    FastpathMethods
};

PyMODINIT_FUNC PyInit__fastpath_c(void) {
    PyObject *m = PyModule_Create(&fastpathmodule);
    if (!m) return NULL;

    /* Initialize interned strings */
    str_type = PyUnicode_InternFromString("type");
    str_seq = PyUnicode_InternFromString("seq");
    str_sym = PyUnicode_InternFromString("sym");
    str_source = PyUnicode_InternFromString("source");
    str_price = PyUnicode_InternFromString("price");
    str_size = PyUnicode_InternFromString("size");
    str_bid = PyUnicode_InternFromString("bid");
    str_ask = PyUnicode_InternFromString("ask");
    str_bid_size = PyUnicode_InternFromString("bid_size");
    str_ask_size = PyUnicode_InternFromString("ask_size");
    str_status = PyUnicode_InternFromString("status");
    str_is_crossed = PyUnicode_InternFromString("is_crossed");
    str_exchange_ts = PyUnicode_InternFromString("exchange_ts");
    str_ingest_ts = PyUnicode_InternFromString("ingest_ts");
    str_broadcast_ts = PyUnicode_InternFromString("broadcast_ts");
    str_engine_us = PyUnicode_InternFromString("engine_us");
    str_micro_price = PyUnicode_InternFromString("micro_price");
    str_ofi = PyUnicode_InternFromString("ofi");
    str_bids = PyUnicode_InternFromString("bids");
    str_asks = PyUnicode_InternFromString("asks");

    val_tick = PyUnicode_InternFromString("TICK");
    val_depth = PyUnicode_InternFromString("DEPTH");
    val_valid = PyUnicode_InternFromString("VALID");
    val_suspicious = PyUnicode_InternFromString("SUSPICIOUS");
    val_invalid = PyUnicode_InternFromString("INVALID");
    val_unknown = PyUnicode_InternFromString("UNKNOWN");

    if (PyType_Ready(&NativeSpscQueueType) < 0) {
        Py_DECREF(m);
        return NULL;
    }
    Py_INCREF(&NativeSpscQueueType);
    if (PyModule_AddObject(m, "NativeSpscQueue", (PyObject *)&NativeSpscQueueType) < 0) {
        Py_DECREF(&NativeSpscQueueType);
        Py_DECREF(m);
        return NULL;
    }

#if defined(Py_GIL_DISABLED)
    PyUnstable_Module_SetGIL(m, Py_MOD_GIL_NOT_USED);
#endif

    return m;
}
