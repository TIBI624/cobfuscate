#ifndef COBFUSCATE_OBFUSCATE_H
#define COBFUSCATE_OBFUSCATE_H

#include <Python.h>

// Function prototypes
PyObject* obfuscate_string_b64(PyObject* self, PyObject* args);

// Method definitions
static PyMethodDef ObfuscateMethods[] = {
    {"obfuscate_string_b64", obfuscate_string_b64, METH_VARARGS,
     "Obfuscate a string with multi-byte XOR and return as Base64."},
    {NULL, NULL, 0, NULL}
};

// Module definition – must be non-static, defined in obfuscate.c
extern struct PyModuleDef obfuscatemodule;

// Module initializer
PyMODINIT_FUNC PyInit_obfuscate(void);

#endif // COBFUSCATE_OBFUSCATE_H