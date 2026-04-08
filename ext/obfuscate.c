#include <Python.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "obfuscate.h"

// --- Base64 Encoding ---
// A simple Base64 implementation to avoid external dependencies.
static const char b64_table[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

char* b64_encode(const unsigned char* data, size_t input_length) {
    size_t output_length = 4 * ((input_length + 2) / 3);
    char* encoded_data = malloc(output_length + 1);
    if (encoded_data == NULL) return NULL;

    for (size_t i = 0, j = 0; i < input_length;) {
        uint32_t octet_a = i < input_length ? (unsigned char)data[i++] : 0;
        uint32_t octet_b = i < input_length ? (unsigned char)data[i++] : 0;
        uint32_t octet_c = i < input_length ? (unsigned char)data[i++] : 0;
        uint32_t triple = (octet_a << 16) + (octet_b << 8) + octet_c;

        encoded_data[j++] = b64_table[(triple >> 18) & 0x3F];
        encoded_data[j++] = b64_table[(triple >> 12) & 0x3F];
        encoded_data[j++] = b64_table[(triple >> 6) & 0x3F];
        encoded_data[j++] = b64_table[triple & 0x3F];
    }

    // Add padding
    int mod_table[] = {0, 2, 1};
    for (int i = 0; i < mod_table[input_length % 3]; i++) {
        encoded_data[output_length - 1 - i] = '=';
    }
    encoded_data[output_length] = '\0';
    return encoded_data;
}

// --- Python C Functions ---

/**
 * @brief Obfuscates a string using a random multi-byte XOR key and encodes the result in Base64.
 *
 * @param self The module object.
 * @param args A tuple containing the string to obfuscate.
 * @return A tuple containing (encoded_string, key_string) or NULL on failure.
 */
PyObject* obfuscate_string_b64(PyObject* self, PyObject* args) {
    const char* input_str;
    if (!PyArg_ParseTuple(args, "s", &input_str)) {
        return NULL;
    }

    size_t len = strlen(input_str);
    if (len == 0) {
        return Py_BuildValue("ss", "", "");
    }

    // --- Key Generation ---
    #define KEY_LENGTH 16
    unsigned char key[KEY_LENGTH];
    for (int i = 0; i < KEY_LENGTH; i++) {
        key[i] = rand() % 256;
    }
    
    // --- XOR Encryption ---
    unsigned char* xor_result = (unsigned char*)malloc(len);
    if (!xor_result) {
        return PyErr_NoMemory();
    }
    for (size_t i = 0; i < len; i++) {
        xor_result[i] = input_str[i] ^ key[i % KEY_LENGTH];
    }

    // --- Base64 Encoding ---
    char* b64_encoded = b64_encode(xor_result, len);
    free(xor_result);
    if (!b64_encoded) {
        return PyErr_NoMemory();
    }
    
    // Create a key string that is safe for Python's b'' literal
    char key_safe_str[KEY_LENGTH * 4 + 1]; 
    for(int i=0; i < KEY_LENGTH; i++){
        sprintf(key_safe_str + (i*4), "\\x%02x", key[i]);
    }

    PyObject* result = Py_BuildValue("ss", b64_encoded, key_safe_str);
    free(b64_encoded);

    return result;
}

PyMODINIT_FUNC PyInit_obfuscate(void) {
    // IMPORTANT: Seed the random number generator ONCE at module import.
    srand((unsigned int)time(NULL));
    return PyModule_Create(&obfuscatemodule);
}