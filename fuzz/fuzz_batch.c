/* fuzz/fuzz_batch.c - libFuzzer harness for MDRAP batch evaluation engine */
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>

#include "../src/fastpath.c"

int LLVMFuzzerTestOneInput(const uint8_t *Data, size_t Size) {
    if (!Data || Size < sizeof(FastEvent)) return 0;

    int32_t count = (int32_t)(Size / sizeof(FastEvent));
    if (count > 256) count = 256;

    FastResult results[256];
    FastEngine *eng = fastpath_engine_create(1.0, 3.0, 20);
    if (!eng) return 0;

    fastpath_engine_evaluate_batch(eng, (const FastEvent *)Data, results, count);

    fastpath_engine_destroy(eng);
    return 0;
}
