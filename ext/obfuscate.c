#include <Python.h>
#include "obfuscate.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

PyObject* obfuscate_string(PyObject* self, PyObject* args) {
    const char* input;
    if (!PyArg_ParseTuple(args, "s", &input)) {
        return NULL;
    }

    size_t len = strlen(input);
    if (len == 0) {
        // Empty string – return empty string
        return PyUnicode_FromString("");
    }

    char* output = (char*)malloc(len + 1);
    if (!output) {
        return PyErr_NoMemory();
    }

    srand(time(NULL));
    int key = rand() % 255;

    for (size_t i = 0; i < len; i++) {
        output[i] = input[i] ^ key;
    }
    output[len] = '\0';

    PyObject* result = PyUnicode_FromString(output);
    free(output);
    return result;
}

PyObject* obfuscate_code(PyObject* self, PyObject* args) {
    const char* input;
    if (!PyArg_ParseTuple(args, "s", &input)) {
        return NULL;
    }
    // Here you can implement more complex code obfuscation in C
    // For now just return the source code
    return PyUnicode_FromString(input);
}

PyMODINIT_FUNC PyInit_obfuscate(void) {
    return PyModule_Create(&obfuscatemodule);
}