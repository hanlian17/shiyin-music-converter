/* QMC2 acceleration, port of ownlight6/qmc-decoder/src/qmc2.rs.
 * GPL-3.0. Full corresponding source is supplied with this application. */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>

static uint64_t segment_key(uint32_t hash, uint64_t id, uint8_t seed) {
    if (!seed) return 0;
    return (uint64_t)((double)hash / ((double)(id + 1) * seed) * 100.0);
}

__declspec(dllexport) int qmc_decrypt(uint8_t *data, size_t size, uint64_t offset,
                                     const uint8_t *key, size_t n) {
    if (!data || !key || n < 8 || n > 4096) return 1;
    if (n <= 300) {
        for (size_t i = 0; i < size; ++i) {
            uint64_t p = offset + i;
            if (p > 0x7FFF) p %= 0x7FFF;
            size_t ix = (p * p + 71214) % n;
            unsigned r = (ix + 4) & 7;
            data[i] ^= (uint8_t)((key[ix] << r) | (key[ix] >> r));
        }
        return 0;
    }
    uint32_t hash = 1;
    for (size_t i = 0; i < n; ++i) {
        if (!key[i]) continue;
        uint32_t next = hash * key[i];
        if (next <= hash) break;
        hash = next;
    }
    uint8_t initial[4096], s[4096];
    for (size_t i = 0; i < n; ++i) initial[i] = (uint8_t)i;
    size_t j = 0;
    for (size_t i = 0; i < n; ++i) {
        j = (j + initial[i] + key[i]) % n;
        uint8_t tmp = initial[i]; initial[i] = initial[j]; initial[j] = tmp;
    }
    size_t done = 0;
    while (done < size && offset + done < 128) {
        uint64_t pos = offset + done;
        data[done] ^= key[segment_key(hash, pos, key[pos % n]) % n];
        ++done;
    }
    while (done < size) {
        uint64_t pos = offset + done, seg = pos / 5120;
        size_t skip = (segment_key(hash, seg, key[seg % n]) & 511) + pos % 5120;
        size_t amount = 5120 - pos % 5120;
        if (amount > size - done) amount = size - done;
        memcpy(s, initial, n); j = 0; size_t k = 0;
        for (size_t t = 0; t < skip + amount; ++t) {
            j = (j + 1) % n; k = (k + s[j]) % n;
            uint8_t tmp = s[j]; s[j] = s[k]; s[k] = tmp;
            if (t >= skip) data[done + t - skip] ^= s[(s[j] + s[k]) % n];
        }
        done += amount;
    }
    return 0;
}
