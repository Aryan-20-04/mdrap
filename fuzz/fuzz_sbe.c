/* fuzz/fuzz_sbe.c - libFuzzer harness for MDRAP SBE frame decoding and stream processing */
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>

#include "../src/fastpath.c"

int LLVMFuzzerTestOneInput(const uint8_t *Data, size_t Size) {
    if (!Data || Size == 0) return 0;

    /* 1. Fuzz single tick decoder */
    SbeTickPayload payload;
    fastpath_sbe_decode_tick(Data, (int32_t)Size, &payload);

    /* 2. Fuzz stream processing */
    int32_t count = (int32_t)(Size / sizeof(SbeTickFrame));
    if (count > 0) {
        if (count > 256) count = 256;
        FastResult results[256];
        fastpath_process_sbe_stream(Data, count, results, NULL, 0);
    }

    return 0;
}
