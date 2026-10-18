/*
 * Load the supplied ONNX Runtime library directly and exercise C API 17.
 * Usage: smoke LIBRARY MODEL [EXPECTED_VERSION]
 * EXPECTED_VERSION defaults to the release version, 1.29.0.
 * No ONNX Runtime import library or Python package is used.
 */
#ifndef _WIN32
#define _XOPEN_SOURCE 700
#endif

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wchar.h>
#else
#include <dlfcn.h>
#include <errno.h>
#endif

#include "onnxruntime_c_api.h"

#if ORT_API_VERSION < 17
#error "The smoke test requires an ONNX Runtime C header supporting API 17"
#endif

#define SMOKE_API_VERSION 17u

typedef const OrtApiBase *(ORT_API_CALL *GetApiBaseFn)(void);

static int status_ok(const OrtApi *api, OrtStatus *status, const char *operation) {
  if (status == NULL) {
    return 1;
  }
  fprintf(stderr, "%s failed (ORT error %d): %s\n", operation,
          (int)api->GetErrorCode(status), api->GetErrorMessage(status));
  api->ReleaseStatus(status);
  return 0;
}

#define CHECK_ORT(call)                         \
  do {                                        \
    if (!status_ok(api, (call), #call)) {      \
      goto cleanup;                           \
    }                                         \
  } while (0)

#ifdef _WIN32
int wmain(int argc, wchar_t **argv) {
#else
int main(int argc, char **argv) {
#endif
  int result = EXIT_FAILURE;
  const OrtApiBase *base = NULL;
  const OrtApi *api = NULL;
  GetApiBaseFn get_api_base = NULL;
  const char *runtime_version = NULL;
  const char *expected_version = "1.29.0";
  OrtEnv *env = NULL;
  OrtSessionOptions *options = NULL;
  OrtSession *session = NULL;
  OrtMemoryInfo *memory_info = NULL;
  OrtValue *input = NULL;
  OrtValue *output = NULL;
  OrtTensorTypeAndShapeInfo *output_info = NULL;
  float input_values[3] = {1.0f, 2.0f, 3.0f};
  const int64_t input_shape[1] = {3};
  const char *input_names[1] = {"input"};
  const char *output_names[1] = {"output"};
  const OrtValue *inputs[1] = {NULL};
  void *output_data = NULL;
  ONNXTensorElementDataType output_type = ONNX_TENSOR_ELEMENT_DATA_TYPE_UNDEFINED;
  int64_t output_shape[1] = {0};
  size_t input_count = 0;
  size_t output_count = 0;
  size_t rank = 0;
  size_t element_count = 0;
  size_t i;
  int is_tensor = 0;
#ifdef _WIN32
  HMODULE library = NULL;
  FARPROC symbol = NULL;
  wchar_t *library_path = NULL;
  DWORD path_capacity;
  DWORD path_length;
  char expected_version_buffer[128];
#else
  void *library = NULL;
  void *symbol = NULL;
  char *library_path = NULL;
  const char *loader_error;
#endif

  if (argc != 3 && argc != 4) {
    fprintf(stderr, "Usage: smoke LIBRARY MODEL [EXPECTED_VERSION]\n");
    goto cleanup;
  }

#ifdef _WIN32
  if (argc == 4) {
    if (WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, argv[3], -1,
                            expected_version_buffer,
                            (int)sizeof(expected_version_buffer), NULL, NULL) == 0) {
      fprintf(stderr, "Invalid expected runtime version (Windows error %lu)\n",
              (unsigned long)GetLastError());
      goto cleanup;
    }
    expected_version = expected_version_buffer;
  }
  /* An absolute path prevents loading a same-named library from PATH. */
  path_capacity = GetFullPathNameW(argv[1], 0, NULL, NULL);
  if (path_capacity == 0) {
    fprintf(stderr, "Cannot resolve library path (Windows error %lu)\n",
            (unsigned long)GetLastError());
    goto cleanup;
  }
  library_path = (wchar_t *)calloc((size_t)path_capacity, sizeof(wchar_t));
  if (library_path == NULL) {
    fprintf(stderr, "Cannot allocate library path\n");
    goto cleanup;
  }
  path_length = GetFullPathNameW(argv[1], path_capacity, library_path, NULL);
  if (path_length == 0 || path_length >= path_capacity) {
    fprintf(stderr, "Cannot resolve library path (Windows error %lu)\n",
            (unsigned long)GetLastError());
    goto cleanup;
  }
  /* Match Mado's loader: never satisfy DLL dependencies through PATH. */
  library = LoadLibraryExW(library_path, NULL,
                         LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
  if (library == NULL) {
    fprintf(stderr, "LoadLibraryExW failed (Windows error %lu)\n",
            (unsigned long)GetLastError());
    goto cleanup;
  }
  symbol = GetProcAddress(library, "OrtGetApiBase");
  if (symbol == NULL) {
    fprintf(stderr, "OrtGetApiBase was not exported (Windows error %lu)\n",
            (unsigned long)GetLastError());
    goto cleanup;
  }
#else
  if (argc == 4) {
    expected_version = argv[3];
  }
  /* A bare filename must refer to this file, rather than a loader search. */
  library_path = realpath(argv[1], NULL);
  if (library_path == NULL) {
    fprintf(stderr, "Cannot resolve library path '%s': %s\n", argv[1], strerror(errno));
    goto cleanup;
  }
  library = dlopen(library_path, RTLD_NOW | RTLD_LOCAL);
  if (library == NULL) {
    fprintf(stderr, "dlopen failed for '%s': %s\n", library_path, dlerror());
    goto cleanup;
  }
  (void)dlerror();
  symbol = dlsym(library, "OrtGetApiBase");
  loader_error = dlerror();
  if (loader_error != NULL || symbol == NULL) {
    fprintf(stderr, "OrtGetApiBase was not exported: %s\n",
            loader_error != NULL ? loader_error : "null symbol");
    goto cleanup;
  }
#endif

  /* Avoid non-portable object-pointer/function-pointer casts and warnings. */
  if (sizeof(get_api_base) != sizeof(symbol)) {
    fprintf(stderr, "Unsupported loader function pointer representation\n");
    goto cleanup;
  }
  memcpy(&get_api_base, &symbol, sizeof(get_api_base));
  base = get_api_base();
  if (base == NULL || base->GetVersionString == NULL || base->GetApi == NULL) {
    fprintf(stderr, "OrtGetApiBase returned an invalid API base\n");
    goto cleanup;
  }
  runtime_version = base->GetVersionString();
  if (runtime_version == NULL || strcmp(runtime_version, expected_version) != 0) {
    fprintf(stderr, "Runtime version mismatch: expected '%s', got '%s'\n",
            expected_version, runtime_version != NULL ? runtime_version : "(null)");
    goto cleanup;
  }
  /* Do not replace this with ORT_API_VERSION: compatibility with 17 is tested. */
  api = base->GetApi(SMOKE_API_VERSION);
  if (api == NULL) {
    fprintf(stderr, "Runtime does not provide C API 17\n");
    goto cleanup;
  }

  CHECK_ORT(api->CreateEnv(ORT_LOGGING_LEVEL_WARNING, "mado-runtime-smoke", &env));
  CHECK_ORT(api->CreateSessionOptions(&options));
  CHECK_ORT(api->SetIntraOpNumThreads(options, 1));
  CHECK_ORT(api->SetInterOpNumThreads(options, 1));
  /* Keep the Identity node so the test executes a kernel, not just an alias. */
  CHECK_ORT(api->SetSessionGraphOptimizationLevel(options, ORT_DISABLE_ALL));
  CHECK_ORT(api->CreateSession(env, argv[2], options, &session));
  CHECK_ORT(api->SessionGetInputCount(session, &input_count));
  CHECK_ORT(api->SessionGetOutputCount(session, &output_count));
  if (input_count != 1 || output_count != 1) {
    fprintf(stderr, "Expected exactly one model input and output\n");
    goto cleanup;
  }
  CHECK_ORT(api->CreateCpuMemoryInfo(OrtArenaAllocator, OrtMemTypeDefault, &memory_info));
  CHECK_ORT(api->CreateTensorWithDataAsOrtValue(
      memory_info, input_values, sizeof(input_values), input_shape, 1,
      ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, &input));
  inputs[0] = input;
  CHECK_ORT(api->Run(session, NULL, input_names, inputs, 1, output_names, 1, &output));
  CHECK_ORT(api->IsTensor(output, &is_tensor));
  if (!is_tensor) {
    fprintf(stderr, "Model output is not a tensor\n");
    goto cleanup;
  }
  CHECK_ORT(api->GetTensorTypeAndShape(output, &output_info));
  CHECK_ORT(api->GetTensorElementType(output_info, &output_type));
  CHECK_ORT(api->GetDimensionsCount(output_info, &rank));
  CHECK_ORT(api->GetTensorShapeElementCount(output_info, &element_count));
  if (output_type != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT || rank != 1 || element_count != 3) {
    fprintf(stderr, "Output must have type float32 and shape [3]\n");
    goto cleanup;
  }
  CHECK_ORT(api->GetDimensions(output_info, output_shape, 1));
  if (output_shape[0] != 3) {
    fprintf(stderr, "Output must have shape [3]\n");
    goto cleanup;
  }
  CHECK_ORT(api->GetTensorMutableData(output, &output_data));
  if (output_data == NULL) {
    fprintf(stderr, "Output tensor has no data\n");
    goto cleanup;
  }
  for (i = 0; i < 3; ++i) {
    float actual = ((const float *)output_data)[i];
    if (actual != input_values[i]) {
      fprintf(stderr, "Identity output mismatch at index %u: expected %g, got %g\n",
              (unsigned int)i, (double)input_values[i], (double)actual);
      goto cleanup;
    }
  }
  printf("PASS: ONNX Runtime %s, C API 17, identity float32[3] -> [1, 2, 3]\n",
         runtime_version);
  result = EXIT_SUCCESS;

cleanup:
  if (api != NULL) {
    if (output_info != NULL) api->ReleaseTensorTypeAndShapeInfo(output_info);
    if (output != NULL) api->ReleaseValue(output);
    if (input != NULL) api->ReleaseValue(input);
    if (memory_info != NULL) api->ReleaseMemoryInfo(memory_info);
    if (session != NULL) api->ReleaseSession(session);
    if (options != NULL) api->ReleaseSessionOptions(options);
    if (env != NULL) api->ReleaseEnv(env);
  }
  if (library != NULL) {
#ifdef _WIN32
    if (!FreeLibrary(library)) {
      fprintf(stderr, "FreeLibrary failed (Windows error %lu)\n",
              (unsigned long)GetLastError());
      result = EXIT_FAILURE;
    }
#else
    if (dlclose(library) != 0) {
      fprintf(stderr, "dlclose failed: %s\n", dlerror());
      result = EXIT_FAILURE;
    }
#endif
  }
  free(library_path);
  return result;
}
