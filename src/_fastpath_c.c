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
    uint64_t engine_ptr = PyLong_AsUnsignedLongLong(args[0]);
    if (PyErr_Occurred()) return NULL;

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

    FastEngine *eng = (FastEngine *)(uintptr_t)engine_ptr;
    uint64_t packed = fastpath_engine_eval_fast2(
        eng, s_id, i_id, ev_type, ex_ts, rc_ts, seq, price, qty, bid, ask, bid_sz, ask_sz, pm
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
    return PyLong_FromUnsignedLongLong((uintptr_t)eng);
}

static PyObject *py_engine_destroy(PyObject *self, PyObject *args) {
    (void)self;
    uint64_t engine_ptr = 0;
    if (!PyArg_ParseTuple(args, "K", &engine_ptr)) {
        return NULL;
    }
    if (engine_ptr) {
        fastpath_engine_destroy((FastEngine *)(uintptr_t)engine_ptr);
    }
    Py_RETURN_NONE;
}

static PyObject *py_engine_configure(PyObject *self, PyObject *args) {
    (void)self;
    uint64_t engine_ptr = 0;
    int32_t price_min_samples = -1;
    int32_t price_reseed_after = -1;
    double price_sigma_floor_rel = -1.0;
    double price_reseed_band_rel = -1.0;
    double max_future_skew_s = -1.0;
    int64_t seq_jump_limit = -1;
    int32_t unseq_dup_status = -1;
    int32_t allow_negative = -1;

    if (!PyArg_ParseTuple(
        args, "KiidddLii",
        &engine_ptr,
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

    if (engine_ptr) {
        fastpath_engine_configure(
            (FastEngine *)(uintptr_t)engine_ptr,
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
    uint64_t engine_ptr = 0;
    if (!PyArg_ParseTuple(args, "K", &engine_ptr)) {
        return NULL;
    }
    if (engine_ptr) {
        fastpath_engine_reset((FastEngine *)(uintptr_t)engine_ptr);
    }
    Py_RETURN_NONE;
}

static PyObject *py_engine_source_reset(PyObject *self, PyObject *args) {
    (void)self;
    uint64_t engine_ptr = 0;
    int32_t source_id = 0;
    if (!PyArg_ParseTuple(args, "Ki", &engine_ptr, &source_id)) {
        return NULL;
    }
    if (engine_ptr) {
        fastpath_engine_source_reset((FastEngine *)(uintptr_t)engine_ptr, source_id);
    }
    Py_RETURN_NONE;
}

static PyObject *py_engine_set_hash_seed(PyObject *self, PyObject *args) {
    (void)self;
    uint64_t engine_ptr = 0;
    uint64_t seed = 0;
    if (!PyArg_ParseTuple(args, "KK", &engine_ptr, &seed)) {
        return NULL;
    }
    if (engine_ptr) {
        fastpath_engine_set_hash_seed((FastEngine *)(uintptr_t)engine_ptr, seed);
    }
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

    /* Format strings safely */
    char sym_str[17] = {0};
    memcpy(sym_str, slot.symbol, 16);
    char src_str[9] = {0};
    memcpy(src_str, slot.source, 8);

    PyObject *st_val = val_unknown;
    if (slot.status == 1) st_val = val_valid;
    else if (slot.status == 2) st_val = val_suspicious;
    else if (slot.status == 3) st_val = val_invalid;

    PyObject *dict = PyDict_New();
    if (!dict) return NULL;

    PyDict_SetItem(dict, str_seq, PyLong_FromUnsignedLongLong(slot.commit_seq));
    PyDict_SetItem(dict, str_sym, PyUnicode_FromString(sym_str));
    PyDict_SetItem(dict, str_source, PyUnicode_FromString(src_str));
    PyDict_SetItem(dict, str_status, st_val);
    PyDict_SetItem(dict, str_is_crossed, slot.is_crossed ? Py_True : Py_False);
    PyDict_SetItem(dict, str_exchange_ts, PyFloat_FromDouble(slot.exchange_ts));
    PyDict_SetItem(dict, str_ingest_ts, PyFloat_FromDouble(slot.ingest_ts));
    PyDict_SetItem(dict, str_broadcast_ts, PyFloat_FromDouble(slot.broadcast_ts));
    PyDict_SetItem(dict, str_engine_us, PyFloat_FromDouble((double)slot.engine_us));

    if (slot.present & SHM3_PRESENT_PRICE) PyDict_SetItem(dict, str_price, PyFloat_FromDouble(slot.price));
    else PyDict_SetItem(dict, str_price, Py_None);

    if (slot.present & SHM3_PRESENT_SIZE) PyDict_SetItem(dict, str_size, PyFloat_FromDouble(slot.size));
    else PyDict_SetItem(dict, str_size, Py_None);

    if (slot.present & SHM3_PRESENT_BID) PyDict_SetItem(dict, str_bid, PyFloat_FromDouble(slot.bid));
    else PyDict_SetItem(dict, str_bid, Py_None);

    if (slot.present & SHM3_PRESENT_ASK) PyDict_SetItem(dict, str_ask, PyFloat_FromDouble(slot.ask));
    else PyDict_SetItem(dict, str_ask, Py_None);

    if (slot.present & SHM3_PRESENT_BSZ) PyDict_SetItem(dict, str_bid_size, PyFloat_FromDouble(slot.bid_sz));
    else PyDict_SetItem(dict, str_bid_size, Py_None);

    if (slot.present & SHM3_PRESENT_ASZ) PyDict_SetItem(dict, str_ask_size, PyFloat_FromDouble(slot.ask_sz));
    else PyDict_SetItem(dict, str_ask_size, Py_None);

    if (slot.event_type == 2) {
        PyDict_SetItem(dict, str_type, val_depth);
        PyDict_SetItem(dict, str_micro_price, PyFloat_FromDouble(slot.price));
        PyDict_SetItem(dict, str_ofi, PyFloat_FromDouble(slot.size));

        PyObject *bid_item = Py_BuildValue("[dds]", slot.bid, slot.bid_sz, "AGG");
        PyObject *bids_list = PyList_New(1);
        PyList_SET_ITEM(bids_list, 0, bid_item);
        PyDict_SetItem(dict, str_bids, bids_list);
        Py_DECREF(bids_list);

        PyObject *ask_item = Py_BuildValue("[dds]", slot.ask, slot.ask_sz, "AGG");
        PyObject *asks_list = PyList_New(1);
        PyList_SET_ITEM(asks_list, 0, ask_item);
        PyDict_SetItem(dict, str_asks, asks_list);
        Py_DECREF(asks_list);
    } else {
        PyDict_SetItem(dict, str_type, val_tick);
    }

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
    PyObject *sym_bytes = NULL;
    if (PyUnicode_Check(args[3])) {
        sym_bytes = PyUnicode_AsUTF8String(args[3]);
        if (sym_bytes) symbol = PyBytes_AsString(sym_bytes);
    } else if (PyBytes_Check(args[3])) {
        symbol = PyBytes_AsString(args[3]);
    }

    const char *source = NULL;
    PyObject *src_bytes = NULL;
    if (PyUnicode_Check(args[4])) {
        src_bytes = PyUnicode_AsUTF8String(args[4]);
        if (src_bytes) source = PyBytes_AsString(src_bytes);
    } else if (PyBytes_Check(args[4])) {
        source = PyBytes_AsString(args[4]);
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

    int32_t res = fastpath_shm_write_tick_v3(
        (uint8_t *)view.buf, (size_t)view.len, slot_count, seq,
        symbol, source, price, size, bid, ask, bid_sz, ask_sz,
        status, is_crossed, present, ex_ts, in_ts, bc_ts, eng_us
    );

    Py_XDECREF(sym_bytes);
    Py_XDECREF(src_bytes);
    PyBuffer_Release(&view);

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
 * Method Table & Module Definition
 * ---------------------------------------------------------------------------*/

static PyMethodDef FastpathMethods[] = {
    {"eval_fast2", (PyCFunction)py_eval_fast2, METH_FASTCALL, "Evaluate an event on FastEngine returning packed (status << 32 | reason_mask)"},
    {"engine_create", (PyCFunction)py_engine_create, METH_VARARGS, "Create FastEngine context"},
    {"engine_destroy", (PyCFunction)py_engine_destroy, METH_VARARGS, "Destroy FastEngine context"},
    {"engine_configure", (PyCFunction)py_engine_configure, METH_VARARGS, "Configure FastEngine thresholds"},
    {"engine_reset", (PyCFunction)py_engine_reset, METH_VARARGS, "Reset FastEngine internal statistics"},
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

    return m;
}
