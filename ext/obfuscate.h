#ifndef COBFUSCATE_OBFUSCATE_H
#define COBFUSCATE_OBFUSCATE_H

#include <Python.h>

// Function Prototypes
PyObject* obfuscate_string_b64(PyObject* self, PyObject* args);

// Method Definitions
static PyMethodDef ObfuscateMethods[] = {
    {"obfuscate_string_b64", obfuscate_string_b64, METH_VARARGS, "Obfuscate a string with multi-byte XOR and return as Base64."},
    {NULL, NULL, 0, NULL} // Sentinel
};

// Module Definition
static struct PyModuleDef obfuscatemodule = {
    PyModuleDef_HEAD_INIT,
    "obfuscate",
    "CObfuscate C Backend for high-performance obfuscation tasks.",
    -1,
    ObfuscateMethods
};

// Module Initializer
PyMODINIT_FUNC PyInit_obfuscate(void);

#endif //COBFUSCATE_OBFUSCATE_H