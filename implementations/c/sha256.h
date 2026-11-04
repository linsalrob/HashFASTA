/* Minimal SHA-256 (FIPS 180-4), self-contained so the only dependency is zlib. */
#ifndef HASHFASTA_SHA256_H
#define HASHFASTA_SHA256_H

#include <stddef.h>
#include <stdint.h>

typedef struct {
    uint32_t state[8];
    uint64_t length;      /* total bytes processed */
    unsigned char block[64];
    size_t used;          /* bytes currently in block */
} sha256_ctx;

void sha256_init(sha256_ctx *ctx);
void sha256_update(sha256_ctx *ctx, const unsigned char *data, size_t len);
void sha256_final(sha256_ctx *ctx, unsigned char digest[32]);

#endif
