#ifndef COBFUSCATE_OBFUSCATE_H
#define COBFUSCATE_OBFUSCATE_H

#include <Python.h>

PyObject* obfuscate_string(PyObject* self, PyObject* args);
PyObject* obfuscate_code(PyObject* self, PyObject* args);

static PyMethodDef ObfuscateMethods[] = {
    {"obfuscate_string", obfuscate_string, METH_VARARGS, "Obfuscate a string"},
    {"obfuscate_code", obfuscate_code, METH_VARARGS, "Obfuscate Python code"},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef obfuscatemodule = {
    PyModuleDef_HEAD_INIT,
    "obfuscate",
    "CObfuscate C Backend",
    -1,
    ObfuscateMethods
};

PyMODINIT_FUNC PyInit_obfuscate(void);

#endif