/*
 * menai_vm_bridge.c — Python boundary layer for all Menai runtime value types.
 */
#define _POSIX_C_SOURCE 200809L
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "menai_vm_c.h"
#include "menai_vm_atomic.h"

static MenaiValue *slow_value_to_menai_value(MenaiVMState *vs, PyObject *src);
static PyObject *menai_value_to_slow_value(MenaiVMState *vs, MenaiValue *val);

/*
 * Module-level state fetched at init
 */
static PyObject *_VMRuntimeError_type = NULL;

/*
 * The CodeObject type from menai.menai_bytecode — used to identify prelude
 * CodeObjects in bridge_set_prelude.  Fetched once during bridge init.
 */
static PyTypeObject *_py_code_object_type = NULL;

/*
 * Slow-world type objects — fetched once at module init.
 * Used by slow_value_to_menai_value to identify slow objects by type.
 * Will be removed in Phase 2 when the compiler emits fast types directly.
 */
static PyTypeObject *Slow_NoneType = NULL;
static PyTypeObject *Slow_BooleanType = NULL;
static PyTypeObject *Slow_IntegerType = NULL;
static PyTypeObject *Slow_FloatType = NULL;
static PyTypeObject *Slow_ComplexType = NULL;
static PyTypeObject *Slow_StringType = NULL;
static PyTypeObject *Slow_SymbolType = NULL;
static PyTypeObject *Slow_ListType = NULL;
static PyTypeObject *Slow_DictType = NULL;
static PyTypeObject *Slow_SetType = NULL;
static PyTypeObject *Slow_FunctionType = NULL;
static PyTypeObject *Slow_StructTypeType = NULL;
static PyTypeObject *Slow_StructType = NULL;
static PyTypeObject *Slow_BytesType = NULL;

/*
 * Conversion helpers — Python boundary only.
 * These are the sole Menai <-> Python conversion functions for types that
 * have both a native representation and a Python representation.
 */
static MenaiString *
alloc_menai_string_from_pyunicode(MenaiVMState *vs, PyObject *pystr)
{
    ssize_t nbytes;
    const char *utf8 = PyUnicode_AsUTF8AndSize(pystr, &nbytes);
    if (!utf8) {
        return NULL;
    }

    return alloc_menai_string_from_utf8(vs, utf8, nbytes);
}

static PyObject *
alloc_pyunicode_from_menai_string(MenaiString *s)
{
    ssize_t nbytes;
    char *utf8 = alloc_utf8_from_menai_string(s, &nbytes);
    if (!utf8) {
        return NULL;
    }

    PyObject *result = PyUnicode_FromStringAndSize(utf8, nbytes);
    free(utf8);
    return result;
}
/*
 * _read_int — read a named integer attribute from a Python object.
 */
static int
_read_int(PyObject *obj, const char *attr, int *out)
{
    PyObject *v = PyObject_GetAttrString(obj, attr);
    if (!v) {
        return -1;
    }

    long val = PyLong_AsLong(v);
    Py_DECREF(v);
    if (val == -1 && PyErr_Occurred()) {
        return -1;
    }

    *out = (int)val;
    return 0;
}

/*
 * _read_bool — read a named boolean attribute from a Python object.
 */
static int
_read_bool(PyObject *obj, const char *attr, int *out)
{
    PyObject *v = PyObject_GetAttrString(obj, attr);
    if (!v) {
        return -1;
    }

    int r = PyObject_IsTrue(v);
    Py_DECREF(v);
    if (r < 0) {
        return -1;
    }

    *out = r;
    return 0;
}

/*
 * menai_code_object_from_python — build a MenaiCodeObject tree from a Python
 * CodeObject.  All constants are converted to fast MenaiValues.  Returns a
 * new reference (ob_refcnt == 1), or NULL on error with a Python exception set.
 */
static MenaiCodeObject *
menai_code_object_from_python(MenaiVMState *vs, PyObject *py_code)
{
    MenaiCodeObject *co = (MenaiCodeObject *)calloc(1, sizeof(MenaiCodeObject));
    if (!co) {
        PyErr_NoMemory();
        return NULL;
    }

    co->ob_refcnt = 1;

    /* Scalar fields */
    if (_read_int(py_code, "param_count", &co->param_count) < 0) {
        goto fail;
    }

    if (_read_int(py_code, "local_count", &co->local_count) < 0) {
        goto fail;
    }

    if (_read_int(py_code, "outgoing_arg_slots", &co->outgoing_arg_slots) < 0) {
        goto fail;
    }

    if (_read_bool(py_code, "is_variadic", &co->is_variadic) < 0) {
        goto fail;
    }

    /* name — optional, used only for error messages */
    PyObject *py_name = PyObject_GetAttrString(py_code, "name");
    if (py_name) {
        if (py_name != Py_None) {
            const char *s = PyUnicode_AsUTF8(py_name);
            if (s) {
                co->name = strdup(s);
            }
        }

        Py_DECREF(py_name);
    } else {
        PyErr_Clear();
    }

    /* ncap — length of free_vars list */
    PyObject *fv = PyObject_GetAttrString(py_code, "free_vars");
    if (!fv) {
        goto fail;
    }

    co->ncap = PyList_GET_SIZE(fv);
    Py_DECREF(fv);

    /* param_names — strdup each parameter name string */
    PyObject *py_param_names = PyObject_GetAttrString(py_code, "param_names");
    if (!py_param_names) {
        goto fail;
    }

    co->nparam_names = PyList_GET_SIZE(py_param_names);
    if (co->nparam_names > 0) {
        co->param_names = (char **)calloc((size_t)co->nparam_names, sizeof(char *));
        if (!co->param_names) {
            Py_DECREF(py_param_names);
            PyErr_NoMemory();
            goto fail;
        }

        for (ssize_t i = 0; i < co->nparam_names; i++) {
            const char *s = PyUnicode_AsUTF8(PyList_GET_ITEM(py_param_names, i));
            if (!s) {
                Py_DECREF(py_param_names);
                goto fail;
            }

            co->param_names[i] = strdup(s);
            if (!co->param_names[i]) {
                Py_DECREF(py_param_names);
                PyErr_NoMemory();
                goto fail;
            }
        }
    }

    Py_DECREF(py_param_names);

    /* instructions — copy the packed array.array buffer */
    PyObject *instrs_obj = PyObject_GetAttrString(py_code, "instructions");
    if (!instrs_obj) {
        goto fail;
    }

    Py_buffer view;
    if (PyObject_GetBuffer(instrs_obj, &view, PyBUF_SIMPLE) < 0) {
        Py_DECREF(instrs_obj);
        goto fail;
    }

    co->code_len = (int)(view.len / sizeof(uint64_t));
    if (co->code_len > 0) {
        co->instrs = (uint64_t *)malloc(view.len);
        if (!co->instrs) {
            PyBuffer_Release(&view);
            Py_DECREF(instrs_obj);
            PyErr_NoMemory();
            goto fail;
        }

        memcpy(co->instrs, view.buf, view.len);
    }

    PyBuffer_Release(&view);
    Py_DECREF(instrs_obj);

    /* names — strdup each global name string */
    PyObject *py_names = PyObject_GetAttrString(py_code, "names");
    if (!py_names) {
        goto fail;
    }

    co->nnames = PyList_GET_SIZE(py_names);
    if (co->nnames > 0) {
        co->names = (const char **)calloc((size_t)co->nnames, sizeof(char *));
        if (!co->names) {
            Py_DECREF(py_names);
            PyErr_NoMemory();
            goto fail;
        }

        for (ssize_t i = 0; i < co->nnames; i++) {
            const char *s = PyUnicode_AsUTF8(PyList_GET_ITEM(py_names, i));
            if (!s) {
                Py_DECREF(py_names);
                goto fail;
            }

            co->names[i] = strdup(s);
            if (!co->names[i]) {
                Py_DECREF(py_names);
                PyErr_NoMemory();
                goto fail;
            }
        }
    }

    Py_DECREF(py_names);

    /* name_hashes — precompute FNV-1a hash of each global name string */
    if (co->nnames > 0) {
        co->name_hashes = (hash_t *)malloc((size_t)co->nnames * sizeof(hash_t));
        if (!co->name_hashes) {
            PyErr_NoMemory();
            goto fail;
        }

        for (ssize_t i = 0; i < co->nnames; i++) {
            co->name_hashes[i] = menai_name_str_hash(co->names[i]);
        }
    }

    /*
     * children — recurse first so that when we convert constants that are
     * functions, their children already exist and can be referenced.
     */
    {
        PyObject *py_children = PyObject_GetAttrString(py_code, "code_objects");
        if (!py_children) {
            goto fail;
        }

        co->nchildren = PyList_GET_SIZE(py_children);
        if (co->nchildren > 0) {
            co->children = (MenaiCodeObject **)calloc(
                (size_t)co->nchildren, sizeof(MenaiCodeObject *));
            if (!co->children) {
                Py_DECREF(py_children);
                PyErr_NoMemory();
                goto fail;
            }

            for (ssize_t i = 0; i < co->nchildren; i++) {
                co->children[i] = menai_code_object_from_python(
                    vs, PyList_GET_ITEM(py_children, i));
                if (!co->children[i]) {
                    Py_DECREF(py_children);
                    goto fail;
                }
            }
        }

        Py_DECREF(py_children);
    }

    /*
     * constants — convert each slow Python value to a fast MenaiValue *.
     */
    PyObject *py_constants = PyObject_GetAttrString(py_code, "constants");
    if (!py_constants) {
        goto fail;
    }

    co->nconst = PyList_GET_SIZE(py_constants);
    if (co->nconst > 0) {
        co->constants = (MenaiValue **)calloc(
            (size_t)co->nconst, sizeof(MenaiValue *));
        if (!co->constants) {
            Py_DECREF(py_constants);
            PyErr_NoMemory();
            goto fail;
        }

        for (ssize_t i = 0; i < co->nconst; i++) {
            PyObject *orig = PyList_GET_ITEM(py_constants, i);
            MenaiValue *fast = slow_value_to_menai_value(vs, orig);
            if (!fast) {
                Py_DECREF(py_constants);
                goto fail;
            }

            co->constants[i] = fast;
        }
    }

    Py_DECREF(py_constants);

    return co;

fail:
    menai_code_object_release(vs, co);
    return NULL;
}

/*
 * slow_value_to_menai_value — convert one slow menai_value.py object to a fast
 * MenaiValue *.
 *
 * Returns a new reference, or NULL on error with a Python exception set.
 * src must be a slow menai_value.py object; passing a fast C value is a
 * programming error and will abort.
 */
static inline MenaiValue *
slow_none_to_fast(MenaiVMState *vs, PyObject *src)
{
    MenaiNone *s = menai_none(vs);
    menai_value_retain((MenaiValue *)s);
    return (MenaiValue *)s;
}

static inline MenaiValue *
slow_boolean_to_fast(MenaiVMState *vs, PyObject *src)
{
    PyObject *bv = PyObject_GetAttrString(src, "value");
    if (!bv) {
        return NULL;
    }

    int b = PyObject_IsTrue(bv);
    Py_DECREF(bv);
    if (b < 0) {
        return NULL;
    }

    MenaiBoolean *r = b ? menai_boolean_true(vs) : menai_boolean_false(vs);
    menai_value_retain((MenaiValue *)r);
    return (MenaiValue *)r;
}

static inline MenaiValue *
slow_integer_to_fast(MenaiVMState *vs, PyObject *src)
{
    PyObject *v = PyObject_GetAttrString(src, "value");
    if (!v) {
        return NULL;
    }

    if (!PyLong_Check(v)) {
        Py_DECREF(v);
        PyErr_SetString(PyExc_TypeError, "MenaiInteger requires an int");
        return NULL;
    }

    int overflow = 0;
    long lv = PyLong_AsLongAndOverflow(v, &overflow);
    if (!overflow) {
        if (lv == -1 && PyErr_Occurred()) {
            Py_DECREF(v);
            return NULL;
        }

        Py_DECREF(v);
        return (MenaiValue *)alloc_menai_integer_from_long(vs, lv);
    }

    int sign = 0;
#if PY_VERSION_HEX >= 0x030E00A1
    PyLong_GetSign(v, &sign);
#else
    sign = _PyLong_Sign(v);
#endif

    int is_neg = (sign < 0);

    size_t nbits = (size_t)_PyLong_NumBits(v);
    if (nbits == (size_t)-1 && PyErr_Occurred()) {
        Py_DECREF(v);
        return NULL;
    }

    int needs_extra = (is_neg || (nbits % 8 == 0));
    size_t nbytes = (nbits + (needs_extra ? 8 : 7)) / 8;
    if (nbytes == 0) {
        nbytes = 1;
    }

    unsigned char *buf = (unsigned char *)malloc(nbytes);
    if (buf == NULL) {
        Py_DECREF(v);
        PyErr_NoMemory();
        return NULL;
    }

#if PY_VERSION_HEX >= 0x030D0000
    int bytearray_ret = _PyLong_AsByteArray((PyLongObject *)v, buf, nbytes, 1, 1, 1);
#else
    int bytearray_ret = _PyLong_AsByteArray((PyLongObject *)v, buf, nbytes, 1, 1);
#endif
    if (bytearray_ret < 0) {
        free(buf);
        Py_DECREF(v);
        return NULL;
    }

    if (is_neg) {
        int carry = 1;
        for (size_t i = 0; i < nbytes; i++) {
            int val = (~buf[i] & 0xFF) + carry;
            buf[i] = (unsigned char)(val & 0xFF);
            carry = val >> 8;
        }
    }

    ssize_t ndigits = (ssize_t)((nbytes + 3) / 4);
    uint32_t *digits = (uint32_t *)malloc((size_t)ndigits * sizeof(uint32_t));
    if (digits == NULL) {
        free(buf);
        Py_DECREF(v);
        PyErr_NoMemory();
        return NULL;
    }

    for (ssize_t i = 0; i < ndigits; i++) {
        uint32_t d = 0;
        for (int b = 0; b < 4; b++) {
            size_t byte_idx = (size_t)(i * 4 + b);
            if (byte_idx < nbytes) {
                d |= ((uint32_t)buf[byte_idx]) << (b * 8);
            }
        }

        digits[i] = d;
    }

    free(buf);
    Py_DECREF(v);

    MenaiBigInt big;
    menai_bigint_init(&big);
    big.digits = digits;
    big.length = ndigits;
    big.sign = is_neg ? -1 : 1;
    menai_bigint_normalize(&big);
    return (MenaiValue *)alloc_menai_integer_from_bigint(vs, big);
}

static inline MenaiValue *
slow_float_to_fast(MenaiVMState *vs, PyObject *src)
{
    PyObject *v = PyObject_GetAttrString(src, "value");
    if (!v) {
        return NULL;
    }

    double d = PyFloat_AsDouble(v);
    Py_DECREF(v);
    if (d == -1.0 && PyErr_Occurred()) {
        return NULL;
    }

    return (MenaiValue *)alloc_menai_float(vs, d);
}

static inline MenaiValue *
slow_complex_to_fast(MenaiVMState *vs, PyObject *src)
{
    PyObject *v = PyObject_GetAttrString(src, "value");
    if (!v) {
        return NULL;
    }

    double real = PyComplex_RealAsDouble(v);
    double imag = PyComplex_ImagAsDouble(v);
    Py_DECREF(v);
    return (MenaiValue *)alloc_menai_complex(vs, real, imag);
}

static inline MenaiValue *
slow_string_to_fast(MenaiVMState *vs, PyObject *src)
{
    PyObject *v = PyObject_GetAttrString(src, "value");
    if (!v) {
        return NULL;
    }

    MenaiString *r = alloc_menai_string_from_pyunicode(vs, v);
    Py_DECREF(v);
    return (MenaiValue *)r;
}

static inline MenaiValue *
slow_bytes_to_fast(MenaiVMState *vs, PyObject *src)
{
    PyObject *v = PyObject_GetAttrString(src, "value");
    if (!v) {
        return NULL;
    }

    Py_ssize_t n;
    char *buf;
    if (PyBytes_AsStringAndSize(v, &buf, &n) < 0) {
        Py_DECREF(v);
        return NULL;
    }

    MenaiBytes *r = alloc_menai_bytes_from_raw(vs, (const uint8_t *)buf, (ssize_t)n);
    Py_DECREF(v);
    return (MenaiValue *)r;
}

static inline MenaiValue *
slow_symbol_to_fast(MenaiVMState *vs, PyObject *src)
{
    PyObject *n = PyObject_GetAttrString(src, "name");
    if (!n) {
        return NULL;
    }

    MenaiString *name_str = alloc_menai_string_from_pyunicode(vs, n);
    Py_DECREF(n);
    if (!name_str) {
        return NULL;
    }

    MenaiSymbol *r = alloc_menai_symbol(vs, name_str);
    menai_value_release(vs, (MenaiValue *)name_str);
    return (MenaiValue *)r;
}

static inline MenaiValue *
slow_list_to_fast(MenaiVMState *vs, PyObject *src)
{
    PyObject *elems = PyObject_GetAttrString(src, "elements");
    if (!elems) {
        return NULL;
    }

    Py_ssize_t n = PyTuple_GET_SIZE(elems);
    MenaiList *lst = alloc_menai_list(vs, n);
    if (!lst) {
        Py_DECREF(elems);
        PyErr_NoMemory();
        return NULL;
    }

    MenaiValue **arr = lst->elements;
    for (Py_ssize_t i = 0; i < n; i++) {
        arr[i] = slow_value_to_menai_value(vs, PyTuple_GET_ITEM(elems, i));
        if (!arr[i]) {
            for (Py_ssize_t j = 0; j < i; j++) {
                menai_value_release(vs, arr[j]);
            }

            menai_value_release(vs, (MenaiValue *)lst);
            Py_DECREF(elems);
            return NULL;
        }
    }

    Py_DECREF(elems);
    return (MenaiValue *)lst;
}

static inline MenaiValue *
slow_dict_to_fast(MenaiVMState *vs, PyObject *src)
{
    PyObject *pairs = PyObject_GetAttrString(src, "pairs");
    if (!pairs) {
        return NULL;
    }

    Py_ssize_t n = PyTuple_GET_SIZE(pairs);

    if (n == 0) {
        Py_DECREF(pairs);
        return (MenaiValue *)alloc_menai_dict(vs);
    }

    MenaiValue **keys = (MenaiValue **)malloc(n * sizeof(MenaiValue *));
    if (!keys) {
        Py_DECREF(pairs);
        PyErr_NoMemory();
        return NULL;
    }

    MenaiValue **values = (MenaiValue **)malloc(n * sizeof(MenaiValue *));
    if (!values) {
        free(keys);
        Py_DECREF(pairs);
        PyErr_NoMemory();
        return NULL;
    }

    hash_t *hashes = (hash_t *)malloc(n * sizeof(hash_t));
    if (!hashes) {
        free(values);
        free(keys);
        Py_DECREF(pairs);
        PyErr_NoMemory();
        return NULL;
    }

    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *pair = PyTuple_GET_ITEM(pairs, i);
        MenaiValue *fk = slow_value_to_menai_value(vs, PyTuple_GET_ITEM(pair, 0));
        if (!fk) {
            for (Py_ssize_t j = 0; j < i; j++) {
                menai_value_release(vs, keys[j]);
                menai_value_release(vs, values[j]);
            }

            free(hashes);
            free(values);
            free(keys);
            Py_DECREF(pairs);
            return NULL;
        }

        MenaiValue *fv = slow_value_to_menai_value(vs, PyTuple_GET_ITEM(pair, 1));
        if (!fv) {
            menai_value_release(vs, fk);
            for (Py_ssize_t j = 0; j < i; j++) {
                menai_value_release(vs, keys[j]);
                menai_value_release(vs, values[j]);
            }

            free(hashes);
            free(values);
            free(keys);
            Py_DECREF(pairs);
            return NULL;
        }

        hash_t h = menai_value_hash(fk);
        if (h == -1) {
            menai_value_release(vs, fk);
            menai_value_release(vs, fv);
            for (Py_ssize_t j = 0; j < i; j++) {
                menai_value_release(vs, keys[j]);
                menai_value_release(vs, values[j]);
            }

            free(hashes);
            free(values);
            free(keys);
            Py_DECREF(pairs);
            PyErr_SetString(PyExc_TypeError, "unhashable dict key");
            return NULL;
        }

        keys[i] = fk;
        values[i] = fv;
        hashes[i] = h;
    }

    Py_DECREF(pairs);
    return (MenaiValue *)alloc_menai_dict_from_arrays_steal(vs, keys, values, hashes, n);
}

static inline MenaiValue *
slow_set_to_fast(MenaiVMState *vs, PyObject *src)
{
    PyObject *elems = PyObject_GetAttrString(src, "elements");
    if (!elems) {
        return NULL;
    }

    Py_ssize_t n = PyTuple_GET_SIZE(elems);
    MenaiSet *s = alloc_menai_set(vs, n);
    if (!s) {
        Py_DECREF(elems);
        PyErr_NoMemory();
        return NULL;
    }

    MenaiValue **elements = s->elements;
    hash_t *hashes = s->hashes;
    for (Py_ssize_t i = 0; i < n; i++) {
        MenaiValue *fe = slow_value_to_menai_value(vs, PyTuple_GET_ITEM(elems, i));
        if (!fe) {
            for (Py_ssize_t j = 0; j < i; j++) {
                menai_value_release(vs, elements[j]);
            }

            menai_value_release(vs, (MenaiValue *)s);
            Py_DECREF(elems);
            return NULL;
        }

        hash_t h = menai_value_hash(fe);
        if (h == -1) {
            menai_value_release(vs, fe);
            for (Py_ssize_t j = 0; j < i; j++) {
                menai_value_release(vs, elements[j]);
            }

            menai_value_release(vs, (MenaiValue *)s);
            Py_DECREF(elems);
            PyErr_SetString(PyExc_TypeError, "unhashable set element");
            return NULL;
        }

        elements[i] = fe;
        hashes[i] = h;
    }

    Py_DECREF(elems);
    s->length = n;
    if (menai_ht_init(&s->ht, n) < 0) {
        menai_value_release(vs, (MenaiValue *)s);
        return NULL;
    }

    for (ssize_t i = 0; i < n; i++) {
        menai_ht_insert(&s->ht, elements[i], hashes[i], i);
    }

    return (MenaiValue *)s;
}

static inline MenaiValue *
slow_structtype_to_fast(MenaiVMState *vs, PyObject *src)
{
    PyObject *name = PyObject_GetAttrString(src, "name");
    if (!name) {
        return NULL;
    }

    MenaiString *name_str = alloc_menai_string_from_pyunicode(vs, name);
    Py_DECREF(name);
    if (!name_str) {
        return NULL;
    }

    PyObject *tag = PyObject_GetAttrString(src, "tag");
    if (!tag) {
        menai_value_release(vs, (MenaiValue *)name_str);
        return NULL;
    }

    int tag_val = (int)PyLong_AsLong(tag);
    Py_DECREF(tag);
    if (PyErr_Occurred()) {
        menai_value_release(vs, (MenaiValue *)name_str);
        return NULL;
    }

    PyObject *fn = PyObject_GetAttrString(src, "field_names");
    if (!fn) {
        menai_value_release(vs, (MenaiValue *)name_str);
        return NULL;
    }

    PyObject *fn_tup = PySequence_Tuple(fn);
    Py_DECREF(fn);
    if (!fn_tup) {
        menai_value_release(vs, (MenaiValue *)name_str);
        return NULL;
    }

    ssize_t nfields = PyTuple_GET_SIZE(fn_tup);
    MenaiString **field_names_arr = NULL;
    if (nfields > 0) {
        field_names_arr = (MenaiString **)calloc((size_t)nfields, sizeof(MenaiString *));
        if (!field_names_arr) {
            menai_value_release(vs, (MenaiValue *)name_str);
            Py_DECREF(fn_tup);
            return NULL;
        }

        for (ssize_t i = 0; i < nfields; i++) {
            PyObject *fname = PyTuple_GET_ITEM(fn_tup, i);
            MenaiString *fname_str = alloc_menai_string_from_pyunicode(vs, fname);
            if (!fname_str) {
                for (ssize_t j = 0; j < i; j++) {
                    menai_value_release(vs, (MenaiValue *)field_names_arr[j]);
                }

                free(field_names_arr);
                menai_value_release(vs, (MenaiValue *)name_str);
                Py_DECREF(fn_tup);
                return NULL;
            }
            field_names_arr[i] = fname_str;
        }
    }

    MenaiStructType *result = alloc_menai_structtype(vs, name_str, tag_val, field_names_arr, nfields);
    menai_value_release(vs, (MenaiValue *)name_str);
    for (ssize_t i = 0; i < nfields; i++) {
        menai_value_release(vs, (MenaiValue *)field_names_arr[i]);
    }

    free(field_names_arr);
    Py_DECREF(fn_tup);
    return (MenaiValue *)result;
}

static inline MenaiValue *
slow_struct_to_fast(MenaiVMState *vs, PyObject *src)
{
    PyObject *st = PyObject_GetAttrString(src, "struct_type");
    if (!st) {
        return NULL;
    }

    MenaiStructType *fast_st = (MenaiStructType *)slow_value_to_menai_value(vs, st);
    Py_DECREF(st);
    if (!fast_st) {
        return NULL;
    }

    PyObject *fields = PyObject_GetAttrString(src, "fields");
    if (!fields) {
        menai_value_release(vs, (MenaiValue *)fast_st);
        return NULL;
    }

    Py_ssize_t n = PyTuple_GET_SIZE(fields);
    MenaiValue **fast_arr = n > 0 ? (MenaiValue **)malloc(n * sizeof(MenaiValue *)) : NULL;
    if (n > 0 && !fast_arr) {
        menai_value_release(vs, (MenaiValue *)fast_st);
        Py_DECREF(fields);
        PyErr_NoMemory();
        return NULL;
    }

    for (Py_ssize_t i = 0; i < n; i++) {
        MenaiValue *ff = slow_value_to_menai_value(vs, PyTuple_GET_ITEM(fields, i));
        if (!ff) {
            for (Py_ssize_t j = 0; j < i; j++) {
                menai_value_release(vs, fast_arr[j]);
            }

            free(fast_arr);
            menai_value_release(vs, (MenaiValue *)fast_st);
            Py_DECREF(fields);
            return NULL;
        }

        fast_arr[i] = ff;
    }

    Py_DECREF(fields);
    /*
     * alloc_menai_struct retains fast_st and each element of fast_arr
     * internally, so we release our references afterward.
     */
    MenaiStruct *r = alloc_menai_struct(vs, fast_st, fast_arr, n);
    for (Py_ssize_t i = 0; i < n; i++) {
        menai_value_release(vs, fast_arr[i]);
    }

    free(fast_arr);
    menai_value_release(vs, (MenaiValue *)fast_st);
    return (MenaiValue *)r;
}

static inline MenaiValue *
slow_function_to_fast(MenaiVMState *vs, PyObject *src)
{
    PyObject *bc = PyObject_GetAttrString(src, "bytecode");
    if (!bc) {
        return NULL;
    }

    MenaiCodeObject *co = menai_code_object_from_python(vs, bc);
    Py_DECREF(bc);
    if (!co) {
        return NULL;
    }

    MenaiFunction *f = alloc_menai_function(vs, co, menai_none(vs));
    menai_code_object_release(vs, co);
    if (!f) {
        return NULL;
    }

    PyObject *cap = PyObject_GetAttrString(src, "captured_values");
    if (!cap) {
        menai_value_release(vs, (MenaiValue *)f);
        return NULL;
    }

    for (Py_ssize_t ci = 0; ci < f->bytecode->ncap; ci++) {
        MenaiValue *fast_cv = slow_value_to_menai_value(vs, PyList_GET_ITEM(cap, ci));
        if (!fast_cv) {
            menai_value_release(vs, (MenaiValue *)f);
            Py_DECREF(cap);
            return NULL;
        }

        menai_value_release(vs, f->captures[ci]);
        f->captures[ci] = fast_cv;
    }

    Py_DECREF(cap);
    return (MenaiValue *)f;
}

static MenaiValue *
slow_value_to_menai_value(MenaiVMState *vs, PyObject *src)
{
    PyTypeObject *t = Py_TYPE(src);

    if (t == Slow_NoneType) {
        return slow_none_to_fast(vs, src);
    }

    if (t == Slow_BooleanType) {
        return slow_boolean_to_fast(vs, src);
    }

    if (t == Slow_IntegerType) {
        return slow_integer_to_fast(vs, src);
    }

    if (t == Slow_FloatType) {
        return slow_float_to_fast(vs, src);
    }

    if (t == Slow_ComplexType) {
        return slow_complex_to_fast(vs, src);
    }

    if (t == Slow_StringType) {
        return slow_string_to_fast(vs, src);
    }

    if (t == Slow_BytesType) {
        return slow_bytes_to_fast(vs, src);
    }

    if (t == Slow_SymbolType) {
        return slow_symbol_to_fast(vs, src);
    }

    if (t == Slow_ListType) {
        return slow_list_to_fast(vs, src);
    }

    if (t == Slow_DictType) {
        return slow_dict_to_fast(vs, src);
    }

    if (t == Slow_SetType) {
        return slow_set_to_fast(vs, src);
    }

    if (t == Slow_StructTypeType) {
        return slow_structtype_to_fast(vs, src);
    }

    if (t == Slow_StructType) {
        return slow_struct_to_fast(vs, src);
    }

    if (t == Slow_FunctionType) {
        return slow_function_to_fast(vs, src);
    }

    PyErr_Format(PyExc_TypeError, "slow_value_to_menai_value: unexpected type %R", (PyObject *)t);
    return NULL;
}
/*
 * menai_value_to_slow_value — convert a fast MenaiValue * to its equivalent
 * slow menai_value.py Python object.
 *
 * This is the inverse of slow_value_to_menai_value.  It is used at the C VM
 * execute boundary to ensure all values returned to Python callers are proper
 * Python objects with the full MenaiValue interface (to_python, describe, etc.).
 *
 * Returns a new reference, or NULL on error with a Python exception set.
 */
static inline PyObject *
fast_none_to_slow(MenaiVMState *vs, MenaiValue *val)
{
    return PyObject_CallNoArgs((PyObject *)Slow_NoneType);
}

static inline PyObject *
fast_boolean_to_slow(MenaiVMState *vs, MenaiValue *val)
{
    int b = ((MenaiBoolean *)val)->value;
    return PyObject_CallOneArg((PyObject *)Slow_BooleanType, b ? Py_True : Py_False);
}

static inline PyObject *
fast_integer_to_slow(MenaiVMState *vs, MenaiValue *val)
{
    MenaiInteger *obj = (MenaiInteger *)val;
    PyObject *py_int;

    if (!obj->is_big) {
        py_int = PyLong_FromLong(obj->fixed);
    } else {
        MenaiBigInt *a = &obj->big;
        if (a->length == 0) {
            py_int = PyLong_FromLong(0);
        } else {
            size_t nbytes = (size_t)a->length * 4;
            unsigned char *buf = (unsigned char *)malloc(nbytes);
            if (buf == NULL) {
                PyErr_NoMemory();
                return NULL;
            }

            for (ssize_t i = 0; i < a->length; i++) {
                uint32_t d = a->digits[i];
                buf[i * 4 + 0] = (unsigned char)(d & 0xFF);
                buf[i * 4 + 1] = (unsigned char)((d >> 8) & 0xFF);
                buf[i * 4 + 2] = (unsigned char)((d >> 16) & 0xFF);
                buf[i * 4 + 3] = (unsigned char)((d >> 24) & 0xFF);
            }

            py_int = _PyLong_FromByteArray(buf, nbytes, 1, 0);
            free(buf);
            if (py_int == NULL) {
                return NULL;
            }

            if (a->sign == -1) {
                PyObject *neg = PyNumber_Negative(py_int);
                Py_DECREF(py_int);
                py_int = neg;
            }
        }
    }

    if (!py_int) {
        return NULL;
    }

    PyObject *result = PyObject_CallOneArg((PyObject *)Slow_IntegerType, py_int);
    Py_DECREF(py_int);
    return result;
}

static inline PyObject *
fast_float_to_slow(MenaiVMState *vs, MenaiValue *val)
{
    PyObject *py_float = PyFloat_FromDouble(((MenaiFloat *)val)->value);
    if (!py_float) {
        return NULL;
    }

    PyObject *result = PyObject_CallOneArg((PyObject *)Slow_FloatType, py_float);
    Py_DECREF(py_float);
    return result;
}

static inline PyObject *
fast_complex_to_slow(MenaiVMState *vs, MenaiValue *val)
{
    MenaiComplex *c = (MenaiComplex *)val;
    PyObject *py_complex = PyComplex_FromDoubles(c->real, c->imag);
    if (!py_complex) {
        return NULL;
    }

    PyObject *result = PyObject_CallOneArg((PyObject *)Slow_ComplexType, py_complex);
    Py_DECREF(py_complex);
    return result;
}

static inline PyObject *
fast_string_to_slow(MenaiVMState *vs, MenaiValue *val)
{
    PyObject *py_str = alloc_pyunicode_from_menai_string((MenaiString *)val);
    if (!py_str) {
        return NULL;
    }

    PyObject *result = PyObject_CallOneArg((PyObject *)Slow_StringType, py_str);
    Py_DECREF(py_str);
    return result;
}

static inline PyObject *
fast_bytes_to_slow(MenaiVMState *vs, MenaiValue *val)
{
    MenaiBytes *mb = (MenaiBytes *)val;
    PyObject *py_bytes = PyBytes_FromStringAndSize((const char *)mb->data, (Py_ssize_t)mb->length);
    if (!py_bytes) {
        return NULL;
    }

    PyObject *result = PyObject_CallOneArg((PyObject *)Slow_BytesType, py_bytes);
    Py_DECREF(py_bytes);
    return result;
}

static inline PyObject *
fast_symbol_to_slow(MenaiVMState *vs, MenaiValue *val)
{
    PyObject *py_str = alloc_pyunicode_from_menai_string(((MenaiSymbol *)val)->name);
    if (!py_str) {
        return NULL;
    }

    PyObject *result = PyObject_CallOneArg((PyObject *)Slow_SymbolType, py_str);
    Py_DECREF(py_str);
    return result;
}

static inline PyObject *
fast_list_to_slow(MenaiVMState *vs, MenaiValue *val)
{
    MenaiList *lst = (MenaiList *)val;
    Py_ssize_t n = lst->length;
    PyObject *py_tuple = PyTuple_New(n);
    if (!py_tuple) {
        return NULL;
    }

    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *elem = menai_value_to_slow_value(vs, lst->elements[i]);
        if (!elem) {
            Py_DECREF(py_tuple);
            return NULL;
        }

        PyTuple_SET_ITEM(py_tuple, i, elem);
    }

    PyObject *result = PyObject_CallOneArg((PyObject *)Slow_ListType, py_tuple);
    Py_DECREF(py_tuple);
    return result;
}

static inline PyObject *
fast_dict_to_slow(MenaiVMState *vs, MenaiValue *val)
{
    MenaiDict *d = (MenaiDict *)val;
    Py_ssize_t n = d->length;
    PyObject *py_pairs = PyTuple_New(n);
    if (!py_pairs) {
        return NULL;
    }

    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *slow_key = menai_value_to_slow_value(vs, d->keys[i]);
        if (!slow_key) {
            Py_DECREF(py_pairs);
            return NULL;
        }

        PyObject *slow_val = menai_value_to_slow_value(vs, d->values[i]);
        if (!slow_val) {
            Py_DECREF(slow_key);
            Py_DECREF(py_pairs);
            return NULL;
        }

        PyObject *pair = PyTuple_Pack(2, slow_key, slow_val);
        Py_DECREF(slow_key);
        Py_DECREF(slow_val);
        if (!pair) {
            Py_DECREF(py_pairs);
            return NULL;
        }

        PyTuple_SET_ITEM(py_pairs, i, pair);
    }

    PyObject *result = PyObject_CallOneArg((PyObject *)Slow_DictType, py_pairs);
    Py_DECREF(py_pairs);
    return result;
}

static inline PyObject *
fast_set_to_slow(MenaiVMState *vs, MenaiValue *val)
{
    MenaiSet *s = (MenaiSet *)val;
    Py_ssize_t n = s->length;
    PyObject *py_tuple = PyTuple_New(n);
    if (!py_tuple) {
        return NULL;
    }

    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *elem = menai_value_to_slow_value(vs, s->elements[i]);
        if (!elem) {
            Py_DECREF(py_tuple);
            return NULL;
        }

        PyTuple_SET_ITEM(py_tuple, i, elem);
    }

    PyObject *result = PyObject_CallOneArg((PyObject *)Slow_SetType, py_tuple);
    Py_DECREF(py_tuple);
    return result;
}

static inline PyObject *
fast_structtype_to_slow(MenaiVMState *vs, MenaiValue *val)
{
    MenaiStructType *st = (MenaiStructType *)val;
    PyObject *py_name = alloc_pyunicode_from_menai_string(st->name);
    if (!py_name) {
        return NULL;
    }

    PyObject *py_tag = PyLong_FromLong((long)st->tag);
    if (!py_tag) {
        Py_DECREF(py_name);
        return NULL;
    }

    PyObject *py_fields = PyTuple_New(st->nfields);
    if (!py_fields) {
        Py_DECREF(py_name);
        Py_DECREF(py_tag);
        return NULL;
    }

    for (int i = 0; i < st->nfields; i++) {
        PyObject *fname = alloc_pyunicode_from_menai_string(st->fields[i].name);
        if (!fname) {
            Py_DECREF(py_name);
            Py_DECREF(py_tag);
            Py_DECREF(py_fields);
            return NULL;
        }

        PyTuple_SET_ITEM(py_fields, i, fname);
    }

    PyObject *result = PyObject_CallFunctionObjArgs(
        (PyObject *)Slow_StructTypeType, py_name, py_tag, py_fields, NULL);
    Py_DECREF(py_name);
    Py_DECREF(py_tag);
    Py_DECREF(py_fields);
    return result;
}

static inline PyObject *
fast_struct_to_slow(MenaiVMState *vs, MenaiValue *val)
{
    MenaiStruct *s = (MenaiStruct *)val;
    PyObject *slow_st = menai_value_to_slow_value(vs, (MenaiValue *)s->struct_type);
    if (!slow_st) {
        return NULL;
    }

    PyObject *py_fields = PyTuple_New(s->nfields);
    if (!py_fields) {
        Py_DECREF(slow_st);
        return NULL;
    }

    for (int i = 0; i < s->nfields; i++) {
        PyObject *fval = menai_value_to_slow_value(vs, s->items[i]);
        if (!fval) {
            Py_DECREF(slow_st);
            Py_DECREF(py_fields);
            return NULL;
        }

        PyTuple_SET_ITEM(py_fields, i, fval);
    }

    PyObject *result = PyObject_CallFunctionObjArgs(
        (PyObject *)Slow_StructType, slow_st, py_fields, NULL);
    Py_DECREF(slow_st);
    Py_DECREF(py_fields);
    return result;
}

static inline PyObject *
fast_function_to_slow(MenaiVMState *vs, MenaiValue *val)
{
    MenaiFunction *fn = (MenaiFunction *)val;
    MenaiCodeObject *co = fn->bytecode;

    PyObject *py_params = PyTuple_New(co->nparam_names);
    if (!py_params) {
        return NULL;
    }

    for (Py_ssize_t i = 0; i < co->nparam_names; i++) {
        PyObject *p = PyUnicode_FromString(co->param_names[i]);
        if (!p) {
            Py_DECREF(py_params);
            return NULL;
        }

        PyTuple_SET_ITEM(py_params, i, p);
    }

    PyObject *py_name = co->name ? PyUnicode_FromString(co->name) : (Py_INCREF(Py_None), Py_None);
    if (!py_name) {
        Py_DECREF(py_params);
        return NULL;
    }

    PyObject *py_caps = PyList_New(0);
    if (!py_caps) {
        Py_DECREF(py_params);
        Py_DECREF(py_name);
        return NULL;
    }

    PyObject *py_variadic = co->is_variadic ? Py_True : Py_False;
    PyObject *result = PyObject_CallFunctionObjArgs(
        (PyObject *)Slow_FunctionType,
        py_params,
        py_name,
        Py_None,
        py_caps,
        py_variadic,
        NULL);
    Py_DECREF(py_params);
    Py_DECREF(py_name);
    Py_DECREF(py_caps);
    return result;
}

static PyObject *
menai_value_to_slow_value(MenaiVMState *vs, MenaiValue *val)
{
    MenaiType t = val->ob_type;

    if (t == MENAITYPE_NONE) {
        return fast_none_to_slow(vs, val);
    }

    if (t == MENAITYPE_BOOLEAN) {
        return fast_boolean_to_slow(vs, val);
    }

    if (t == MENAITYPE_INTEGER) {
        return fast_integer_to_slow(vs, val);
    }

    if (t == MENAITYPE_FLOAT) {
        return fast_float_to_slow(vs, val);
    }

    if (t == MENAITYPE_COMPLEX) {
        return fast_complex_to_slow(vs, val);
    }

    if (t == MENAITYPE_STRING) {
        return fast_string_to_slow(vs, val);
    }

    if (t == MENAITYPE_BYTES) {
        return fast_bytes_to_slow(vs, val);
    }

    if (t == MENAITYPE_SYMBOL) {
        return fast_symbol_to_slow(vs, val);
    }

    if (t == MENAITYPE_LIST) {
        return fast_list_to_slow(vs, val);
    }

    if (t == MENAITYPE_DICT) {
        return fast_dict_to_slow(vs, val);
    }

    if (t == MENAITYPE_SET) {
        return fast_set_to_slow(vs, val);
    }

    if (t == MENAITYPE_STRUCTTYPE) {
        return fast_structtype_to_slow(vs, val);
    }

    if (t == MENAITYPE_STRUCT) {
        return fast_struct_to_slow(vs, val);
    }

    if (t == MENAITYPE_FUNCTION) {
        return fast_function_to_slow(vs, val);
    }

    PyErr_Format(PyExc_TypeError,
        "menai_value_to_slow_value: unknown type tag 0x%08x", (unsigned)t);
    return NULL;
}

/*
 * bridge_translate_error - package a MenaiVMError from the native VM
 * into a _MenaiVMRuntimeError sentinel exception.
 *
 * Must only be called when no Python exception is already set.
 *
 * The C VM sets a granular MENAI_ERR_* code at each error site and
 * fills a MenaiVMError struct with diagnostic context (opcode, ip,
 * call_depth).  This function packages all of that into a
 * _MenaiVMRuntimeError Python exception object.  The Python wrapper
 * (MenaiVM.execute) catches this sentinel and translates it into the
 * final user-facing exception using the error table in
 * menai_vm_errors.py.
 *
 * For MENAI_ERR_USER_ERROR, the user-supplied message is carried in
 * err->user_message (a malloc'd C string).  This function frees it
 * after packaging it into the Python exception.
 */
static void
bridge_translate_error(const MenaiVMError *err)
{
    /*
     * Construct _MenaiVMRuntimeError(code, opcode, ip, call_depth,
     * user_message).
     */
    PyObject *py_user_msg;
    PyObject *args;
    PyObject *exc;

    if (err->user_message) {
        py_user_msg = PyUnicode_FromString(err->user_message);
        free((void *)err->user_message);
        if (!py_user_msg) {
            return;
        }
    } else {
        py_user_msg = Py_None;
        Py_INCREF(py_user_msg);
    }

    args = Py_BuildValue("(iiiiN)",
        err->code, err->opcode, err->ip, err->call_depth,
        py_user_msg);
    if (!args) {
        return;
    }

    exc = PyObject_CallObject(_VMRuntimeError_type, args);
    Py_DECREF(args);
    if (!exc) {
        return;
    }

    PyErr_SetObject((PyObject *)Py_TYPE(exc), exc);
    Py_DECREF(exc);
}

/*
 * bridge_set_prelude — execute a prelude CodeObject and store the resulting
 * GlobalsTable permanently in the VM state.
 *
 * The prelude is executed once via menai_vm_execute_native to produce a
 * MenaiDict, which is unpacked into vs->_globals.  The table persists for
 * the lifetime of the VM state and is never rebuilt.
 * Returns 0 on success, -1 on error with a Python exception set.
 */
static int
bridge_set_prelude(MenaiVMState *vs, PyObject *prelude_code)
{
    if (!_py_code_object_type || Py_TYPE(prelude_code) != _py_code_object_type) {
        PyErr_SetString(PyExc_TypeError, "Prelude must be a CodeObject");
        return -1;
    }

    MenaiCodeObject *prelude_co = menai_code_object_from_python(vs, prelude_code);
    if (!prelude_co) {
        return -1;
    }

    MenaiVMError vm_err;
    MenaiValue *result = menai_vm_execute_native(vs, prelude_co, NULL, &vm_err);
    menai_code_object_release(vs, prelude_co);
    if (!result) {
        if (!PyErr_Occurred()) {
            bridge_translate_error(&vm_err);
        }

        return -1;
    }

    if (!IS_MENAI_DICT(result)) {
        menai_value_release(vs, result);
        PyErr_SetString(PyExc_TypeError, "Prelude must evaluate to a dict");
        return -1;
    }

    int rc = globals_build_from_dict(vs, &vs->_globals, (MenaiDict *)result);
    menai_value_release(vs, result);
    if (rc < 0) {
        return -1;
    }

    vs->_globals_valid = 1;
    return 0;
}

/*
 * menai_dict_from_pydict — convert a Python dict of (str, MenaiValue) pairs
 * to a native MenaiDict.  Keys are converted to MenaiString, values via
 * slow_value_to_menai_value.  Returns a new reference, or NULL on error.
 */
static MenaiDict *
menai_dict_from_pydict(MenaiVMState *vs, PyObject *pydict)
{
    Py_ssize_t n = PyDict_Size(pydict);
    if (n == 0) {
        return alloc_menai_dict(vs);
    }

    MenaiValue **keys = (MenaiValue **)malloc((size_t)n * sizeof(MenaiValue *));
    if (!keys) {
        PyErr_NoMemory();
        return NULL;
    }

    MenaiValue **values = (MenaiValue **)malloc((size_t)n * sizeof(MenaiValue *));
    if (!values) {
        free(keys);
        PyErr_NoMemory();
        return NULL;
    }

    hash_t *hashes = (hash_t *)malloc((size_t)n * sizeof(hash_t));
    if (!hashes) {
        free(keys);
        free(values);
        PyErr_NoMemory();
        return NULL;
    }

    Py_ssize_t i = 0;
    PyObject *key, *val;
    Py_ssize_t pos = 0;
    while (PyDict_Next(pydict, &pos, &key, &val)) {
        keys[i] = (MenaiValue *)alloc_menai_string_from_pyunicode(vs, key);
        if (!keys[i]) {
            goto fail;
        }

        values[i] = slow_value_to_menai_value(vs, val);
        if (!values[i]) {
            menai_value_release(vs, keys[i]);
            goto fail;
        }

        hashes[i] = menai_value_hash(keys[i]);
        if (hashes[i] == -1) {
            menai_value_release(vs, keys[i]);
            menai_value_release(vs, values[i]);
            goto fail;
        }

        i++;
    }

    return alloc_menai_dict_from_arrays_steal(vs, keys, values, hashes, (ssize_t)n);

fail:
    for (Py_ssize_t j = 0; j < i; j++) {
        menai_value_release(vs, keys[j]);
        menai_value_release(vs, values[j]);
    }
    free(keys);
    free(values);
    free(hashes);
    return NULL;
}

/*
 * menai_vm_c_execute — the Python-callable entry point.
 *
 * Parses arguments (code, extra_bindings, state_capsule),
 * converts the code tree, uses the prelude globals already stored in the VM
 * state, and calls menai_vm_execute_native to run the VM.  The result is
 * converted back to a slow Python MenaiValue before returning.
 */
static PyObject *
menai_vm_c_execute(PyObject *self, PyObject *args)
{
    PyObject *code;
    PyObject *extra_bindings = NULL;
    PyObject *state_capsule = NULL;

    if (!PyArg_ParseTuple(args, "O|OO", &code, &extra_bindings, &state_capsule)) {
        return NULL;
    }

    MenaiVMState *vs = NULL;
    if (state_capsule && state_capsule != Py_None) {
        vs = (MenaiVMState *)PyCapsule_GetPointer(state_capsule, "menai_vm_state");
        if (!vs) {
            return NULL;
        }
    }

    /* Clear any stale cancellation from a previous call. */
    vs->_cancel_flag = 0;

    MenaiCodeObject *native_code = menai_code_object_from_python(vs, code);
    if (!native_code) {
        return NULL;
    }

    GlobalsTable extra_globals;
    int has_extra = 0;
    if (extra_bindings && extra_bindings != Py_None) {
        MenaiDict *native_extra = menai_dict_from_pydict(vs, extra_bindings);
        if (!native_extra) {
            menai_code_object_release(vs, native_code);
            return NULL;
        }
        int gerr = globals_build_from_dict(vs, &extra_globals, native_extra);
        menai_value_release(vs, (MenaiValue *)native_extra);
        if (gerr < 0) {
            menai_code_object_release(vs, native_code);
            return NULL;
        }
        has_extra = 1;
    }

    MenaiVMError vm_err;
    MenaiValue *result;

    /*
     * Release the GIL for the duration of VM execution.  The execute loop is
     * pure C operating on Menai values — it does not touch any Python objects.
     * This allows other Python threads (e.g. the event loop requesting
     * cancellation) to run without contention.
     */
    Py_BEGIN_ALLOW_THREADS
    result = menai_vm_execute_native(vs, native_code, has_extra ? &extra_globals : NULL, &vm_err);
    Py_END_ALLOW_THREADS

    menai_code_object_release(vs, native_code);
    if (has_extra) {
        globals_free(vs, &extra_globals);
    }

    if (result == NULL) {
        if (!PyErr_Occurred()) {
            bridge_translate_error(&vm_err);
        }

        return NULL;
    }

    PyObject *slow = menai_value_to_slow_value(vs, result);
    menai_value_release(vs, result);
    return slow;
}

int
menai_vm_bridge_init(void)
{
    /* Fetch slow-world types — needed by slow_value_to_menai_value. */
    PyObject *slow_mod = PyImport_ImportModule("menai.menai_value");
    if (!slow_mod) {
        return 0;
    }

    PyObject *none_type = PyObject_GetAttrString(slow_mod, "MenaiNone");
    if (!none_type) {
        goto fail;
    }

    Slow_NoneType = (PyTypeObject *)none_type;

    PyObject *boolean_type = PyObject_GetAttrString(slow_mod, "MenaiBoolean");
    if (!boolean_type) {
        goto fail;
    }

    Slow_BooleanType = (PyTypeObject *)boolean_type;

    PyObject *integer_type = PyObject_GetAttrString(slow_mod, "MenaiInteger");
    if (!integer_type) {
        goto fail;
    }

    Slow_IntegerType = (PyTypeObject *)integer_type;

    PyObject *float_type = PyObject_GetAttrString(slow_mod, "MenaiFloat");
    if (!float_type) {
        goto fail;
    }

    Slow_FloatType = (PyTypeObject *)float_type;

    PyObject *complex_type = PyObject_GetAttrString(slow_mod, "MenaiComplex");
    if (!complex_type) {
        goto fail;
    }

    Slow_ComplexType = (PyTypeObject *)complex_type;

    PyObject *string_type = PyObject_GetAttrString(slow_mod, "MenaiString");
    if (!string_type) {
        goto fail;
    }

    Slow_StringType = (PyTypeObject *)string_type;

    PyObject *symbol_type = PyObject_GetAttrString(slow_mod, "MenaiSymbol");
    if (!symbol_type) {
        goto fail;
    }

    Slow_SymbolType = (PyTypeObject *)symbol_type;

    PyObject *list_type = PyObject_GetAttrString(slow_mod, "MenaiList");
    if (!list_type) {
        goto fail;
    }

    Slow_ListType = (PyTypeObject *)list_type;

    PyObject *dict_type = PyObject_GetAttrString(slow_mod, "MenaiDict");
    if (!dict_type) {
        goto fail;
    }

    Slow_DictType = (PyTypeObject *)dict_type;

    PyObject *set_type = PyObject_GetAttrString(slow_mod, "MenaiSet");
    if (!set_type) {
        goto fail;
    }

    Slow_SetType = (PyTypeObject *)set_type;

    PyObject *function_type = PyObject_GetAttrString(slow_mod, "MenaiFunction");
    if (!function_type) {
        goto fail;
    }

    Slow_FunctionType = (PyTypeObject *)function_type;

    PyObject *struct_type_type = PyObject_GetAttrString(slow_mod, "MenaiStructType");
    if (!struct_type_type) {
        goto fail;
    }

    Slow_StructTypeType = (PyTypeObject *)struct_type_type;

    PyObject *struct_type = PyObject_GetAttrString(slow_mod, "MenaiStruct");
    if (!struct_type) {
        goto fail;
    }

    Slow_StructType = (PyTypeObject *)struct_type;

    PyObject *bytes_type = PyObject_GetAttrString(slow_mod, "MenaiBytes");
    if (!bytes_type) {
        goto fail;
    }

    Slow_BytesType = (PyTypeObject *)bytes_type;

    Py_DECREF(slow_mod);
    slow_mod = NULL;

    /* Fetch the CodeObject type — used by bridge_set_prelude to identify
     * prelude CodeObjects. */
    PyObject *bytecode_mod = PyImport_ImportModule("menai.bytecode.menai_bytecode");
    if (!bytecode_mod) {
        return 0;
    }

    PyObject *co_type = PyObject_GetAttrString(bytecode_mod, "CodeObject");
    Py_DECREF(bytecode_mod);
    if (!co_type) {
        return 0;
    }

    _py_code_object_type = (PyTypeObject *)co_type;

    return 1;

fail:
    Py_XDECREF(slow_mod);
    return 0;
}

/*
 * menai_vm_c_cancel — Python-callable wrapper for menai_vm_cancel.
 *
 * cancel(state_capsule) atomically sets the cancellation flag in the VM
 * state.  Thread-safe: may be called from a different thread while the
 * VM is executing.
 *
 */
static PyObject *
menai_vm_c_cancel(PyObject *self, PyObject *capsule)
{
    MenaiVMState *vs = (MenaiVMState *)PyCapsule_GetPointer(capsule, "menai_vm_state");
    if (!vs) {
        return NULL;
    }

    menai_vm_cancel(vs);
    Py_RETURN_NONE;
}

/*
 * Python-callable wrappers for the per-instance VM state lifecycle.
 *
 * state_alloc() returns a PyCapsule wrapping a heap-allocated MenaiVMState.
 * state_free(capsule) frees it.
 */
static PyObject *
menai_vm_c_state_alloc(PyObject *self, PyObject *args)
{
    MenaiVMState *vs = menai_vm_state_alloc();
    if (!vs) {
        PyErr_NoMemory();
        return NULL;
    }

    return PyCapsule_New(vs, "menai_vm_state", NULL);
}

static PyObject *
menai_vm_c_state_free(PyObject *self, PyObject *capsule)
{
    MenaiVMState *vs = (MenaiVMState *)PyCapsule_GetPointer(capsule, "menai_vm_state");
    if (!vs) {
        return NULL;
    }

    menai_vm_state_free(vs);
    Py_RETURN_NONE;
}

/*
 * menai_vm_c_set_prelude — Python-callable wrapper for bridge_set_prelude.
 *
 * set_prelude(state_capsule, prelude_code) executes the prelude CodeObject
 * once and stores the resulting GlobalsTable permanently in the VM state.
 */
static PyObject *
menai_vm_c_set_prelude(PyObject *self, PyObject *args)
{
    PyObject *state_capsule;
    PyObject *prelude_code;

    if (!PyArg_ParseTuple(args, "OO", &state_capsule, &prelude_code)) {
        return NULL;
    }

    MenaiVMState *vs = (MenaiVMState *)PyCapsule_GetPointer(state_capsule, "menai_vm_state");
    if (!vs) {
        return NULL;
    }

    if (bridge_set_prelude(vs, prelude_code) < 0) {
        return NULL;
    }

    Py_RETURN_NONE;
}

/*
 * Module definition
 */
static PyMethodDef menai_vm_c_methods[] = {
    {
        "execute",
        menai_vm_c_execute,
        METH_VARARGS,
        "Execute a Menai CodeObject and return the result."
    },
    {
        "cancel",
        menai_vm_c_cancel,
        METH_O,
        "Set the cancellation flag in the VM state to request cancellation."
    },
    {
        "state_alloc",
        menai_vm_c_state_alloc,
        METH_NOARGS,
        "Allocate a per-instance VM state and return it as a PyCapsule."
    },
    {
        "state_free",
        menai_vm_c_state_free,
        METH_O,
        "Free a VM state allocated by state_alloc."
    },
    {
        "set_prelude",
        menai_vm_c_set_prelude,
        METH_VARARGS,
        "Execute a prelude CodeObject and store its globals in the VM state."
    },
    { NULL, NULL, 0, NULL }
};

static struct PyModuleDef menai_vm_c_module = {
    PyModuleDef_HEAD_INIT,
    "menai.menai_vm_c",
    NULL,
    -1,
    menai_vm_c_methods
};

static int
menai_vm_shim_init(void)
{
    if (!menai_vm_bridge_init()) {
        return -1;
    }

    PyObject *err_mod = PyImport_ImportModule("menai.vm.menai_vm_errors");
    if (err_mod == NULL) {
        return -1;
    }

    _VMRuntimeError_type = PyObject_GetAttrString(err_mod, "_MenaiVMRuntimeError");
    Py_DECREF(err_mod);
    if (_VMRuntimeError_type == NULL) {
        Py_XDECREF(_VMRuntimeError_type);
        return -1;
    }

    return 0;
}

PyMODINIT_FUNC
PyInit_menai_vm_c(void)
{
    PyObject *module = PyModule_Create(&menai_vm_c_module);
    if (module == NULL) {
        return NULL;
    }

    if (menai_vm_shim_init() < 0) {
        Py_DECREF(module);
        return NULL;
    }

    return module;
}
