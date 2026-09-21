/*
 * menai_vm_hash.c — native cryptographic hash primitives.
 *
 * Implements SHA2-256, SHA2-512, SHA2-512/256 (FIPS Pub 180-4) and SHA3-256
 * (FIPS Pub 202) over a whole byte buffer.  These are pure computations with
 * no allocation and no VM state, so the entry points take no MenaiVMState *.
 *
 * The Menai language only ever hashes a complete bytes value, so the streaming
 * context used internally is not exposed: each entry point appends the whole
 * message and finalises in one call.
 */
#include <string.h>
#include <stdlib.h>

#include "menai_vm_c.h"

/*
 * Byte-swap a 32-bit unsigned int.
 *
 * Every platform the VM targets is little-endian, so the big-endian
 * conversions always swap and the little-endian conversions never do.  GCC and
 * Clang lower __builtin_bswap32 to a single instruction; MSVC does the same for
 * _byteswap_ulong.
 */
static inline uint32_t
hash_bswap32(uint32_t v)
{
#if defined(__GNUC__) || defined(__clang__)
    return __builtin_bswap32(v);
#else
    return _byteswap_ulong(v);
#endif
}

/*
 * Byte-swap a 64-bit unsigned int.
 */
static inline uint64_t
hash_bswap64(uint64_t v)
{
#if defined(__GNUC__) || defined(__clang__)
    return __builtin_bswap64(v);
#else
    return _byteswap_uint64(v);
#endif
}

/*
 * Convert a 32-bit unsigned int from big-endian to host form.
 */
static inline uint32_t
hash_u32_be_to_host(uint32_t v)
{
    return hash_bswap32(v);
}

/*
 * Convert a 64-bit unsigned int from big-endian to host form.
 */
static inline uint64_t
hash_u64_be_to_host(uint64_t v)
{
    return hash_bswap64(v);
}

/*
 * Convert a 64-bit unsigned int from little-endian to host form.
 */
static inline uint64_t
hash_u64_le_to_host(uint64_t v)
{
    return v;
}

/*
 * Convert a 64-bit unsigned int from host form to big-endian.
 */
static inline uint64_t
hash_u64_host_to_be(uint64_t v)
{
    return hash_bswap64(v);
}

/*
 * Copy len bytes from s to d.
 *
 * The length is decomposed into 1, 2, 4 and then 8 byte chunks so that the
 * bulk of the copy is performed with the widest alignment-safe accesses.
 */
static inline void
hash_copy_bytes(void *d, const void *s, size_t len)
{
    uint8_t *d1 = (uint8_t *)d;
    const uint8_t *s1 = (const uint8_t *)s;
    if (len & 0x1) {
        *d1++ = *s1++;
    }

    uint16_t *d2 = (uint16_t *)d1;
    const uint16_t *s2 = (const uint16_t *)s1;
    if (len & 0x2) {
        *d2++ = *s2++;
    }

    uint32_t *d4 = (uint32_t *)d2;
    const uint32_t *s4 = (const uint32_t *)s2;
    if (len & 0x4) {
        *d4++ = *s4++;
    }

    uint64_t *d8 = (uint64_t *)d4;
    const uint64_t *s8 = (const uint64_t *)s4;
    while (len & (~(size_t)0x07)) {
        *d8++ = *s8++;
        len -= 8;
    }
}

#define HASH_SHA2_256_SIZE 32
#define HASH_SHA2_256_WORDS 8
#define HASH_SHA2_256_BLOCK 64

#define HASH_SHA2_512_SIZE 64
#define HASH_SHA2_512_WORDS 8
#define HASH_SHA2_512_BLOCK 128

#define HASH_SHA2_512_256_SIZE 32

#define HASH_SHA3_256_SIZE 32
#define HASH_SHA3_SPONGE_WORDS 25
#define HASH_SHA3_256_BLOCK 136

static const uint32_t hash_sha2_256_h0[8] = {
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19
};

static const uint32_t hash_sha2_256_k[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
    0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
    0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
    0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
    0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5,
    0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
};

/*
 * Bitwise rotate right.
 */
static inline uint32_t
hash_sha2_256_rotr(uint32_t x, int n)
{
    return (x >> n) | (x << (32 - n));
}

/*
 * SHA2 Ch.
 */
static inline uint32_t
hash_sha2_256_ch(uint32_t x, uint32_t y, uint32_t z)
{
    return (x & y) ^ (~x & z);
}

/*
 * SHA2 Maj.
 */
static inline uint32_t
hash_sha2_256_maj(uint32_t x, uint32_t y, uint32_t z)
{
    return (x & y) ^ (x & z) ^ (y & z);
}

/*
 * SHA2 sum 0.
 */
static inline uint32_t
hash_sha2_256_sum0(uint32_t x)
{
    return hash_sha2_256_rotr(x, 2) ^ hash_sha2_256_rotr(x, 13) ^ hash_sha2_256_rotr(x, 22);
}

/*
 * SHA2 sum 1.
 */
static inline uint32_t
hash_sha2_256_sum1(uint32_t x)
{
    return hash_sha2_256_rotr(x, 6) ^ hash_sha2_256_rotr(x, 11) ^ hash_sha2_256_rotr(x, 25);
}

/*
 * SHA2 sigma 0.
 */
static inline uint32_t
hash_sha2_256_sigma0(uint32_t x)
{
    return hash_sha2_256_rotr(x, 7) ^ hash_sha2_256_rotr(x, 18) ^ (x >> 3);
}

/*
 * SHA2 sigma 1.
 */
static inline uint32_t
hash_sha2_256_sigma1(uint32_t x)
{
    return hash_sha2_256_rotr(x, 17) ^ hash_sha2_256_rotr(x, 19) ^ (x >> 10);
}

/*
 * Generate the schedule of W values for subscripts 16 though 63.
 */
static inline uint32_t
hash_sha2_256_gen_w(const uint32_t *w, int i)
{
    return hash_sha2_256_sigma1(w[(i - 2) & 15]) + w[(i - 7) & 15] + hash_sha2_256_sigma0(w[(i - 15) & 15]) + w[(i - 16) & 15];
}

/*
 * Expand for each round.
 */
static inline void
hash_sha2_256_expand(uint32_t *wv, int r, uint32_t kj, uint32_t wj)
{
    uint32_t t1 = wv[(r + 7) & 7] + hash_sha2_256_sum1(wv[(r + 4) & 7]) + hash_sha2_256_ch(wv[(r + 4) & 7], wv[(r + 5) & 7], wv[(r + 6) & 7]) + kj + wj;
    uint32_t t2 = hash_sha2_256_sum0(wv[r]) + hash_sha2_256_maj(wv[r], wv[(r + 1) & 7], wv[(r + 2) & 7]);
    wv[(r + 3) & 7] += t1;
    wv[(r + 7) & 7] = t1 + t2;
}

/*
 * Initialize the SHA2-256 working state.
 */
static void
hash_sha2_256_init(uint32_t *hash)
{
    for (int i = 0; i < HASH_SHA2_256_WORDS; i++) {
        hash[i] = hash_sha2_256_h0[i];
    }
}

/*
 * Core of the SHA2-256 transform.
 */
static void
hash_sha2_256_transform(uint32_t *hash, const uint8_t *block)
{
    /*
     * The SHA2 specification calls for using 8 variables, a through h, but at
     * the end of each round we have to copy them around in a ring.  Instead we
     * can use an array and add an offset instead of copying.
     */
    uint32_t wv[8];
    for (int i = 0; i < 8; i++) {
        wv[i] = hash[i];
    }

    /*
     * The algorithm can be broken down into sets of 16 32-bit words because
     * there are no interactions between each 16-word block.
     */
    uint32_t w[16];
    const uint32_t *nb = (const uint32_t *)block;
    for (int i = 0; i < 16; i++) {
        w[i] = hash_u32_be_to_host(nb[i]);
    }

    hash_sha2_256_expand(wv, 0, hash_sha2_256_k[0], w[0]);
    hash_sha2_256_expand(wv, 7, hash_sha2_256_k[1], w[1]);
    hash_sha2_256_expand(wv, 6, hash_sha2_256_k[2], w[2]);
    hash_sha2_256_expand(wv, 5, hash_sha2_256_k[3], w[3]);
    hash_sha2_256_expand(wv, 4, hash_sha2_256_k[4], w[4]);
    hash_sha2_256_expand(wv, 3, hash_sha2_256_k[5], w[5]);
    hash_sha2_256_expand(wv, 2, hash_sha2_256_k[6], w[6]);
    hash_sha2_256_expand(wv, 1, hash_sha2_256_k[7], w[7]);
    hash_sha2_256_expand(wv, 0, hash_sha2_256_k[8], w[8]);
    hash_sha2_256_expand(wv, 7, hash_sha2_256_k[9], w[9]);
    hash_sha2_256_expand(wv, 6, hash_sha2_256_k[10], w[10]);
    hash_sha2_256_expand(wv, 5, hash_sha2_256_k[11], w[11]);
    hash_sha2_256_expand(wv, 4, hash_sha2_256_k[12], w[12]);
    hash_sha2_256_expand(wv, 3, hash_sha2_256_k[13], w[13]);
    hash_sha2_256_expand(wv, 2, hash_sha2_256_k[14], w[14]);
    hash_sha2_256_expand(wv, 1, hash_sha2_256_k[15], w[15]);

    for (int i = 16; i < 64; i += 16) {
        for (int j = 0; j < 16; j++) {
            w[j] = hash_sha2_256_gen_w(w, j);
        }

        hash_sha2_256_expand(wv, 0, hash_sha2_256_k[i], w[0]);
        hash_sha2_256_expand(wv, 7, hash_sha2_256_k[i + 1], w[1]);
        hash_sha2_256_expand(wv, 6, hash_sha2_256_k[i + 2], w[2]);
        hash_sha2_256_expand(wv, 5, hash_sha2_256_k[i + 3], w[3]);
        hash_sha2_256_expand(wv, 4, hash_sha2_256_k[i + 4], w[4]);
        hash_sha2_256_expand(wv, 3, hash_sha2_256_k[i + 5], w[5]);
        hash_sha2_256_expand(wv, 2, hash_sha2_256_k[i + 6], w[6]);
        hash_sha2_256_expand(wv, 1, hash_sha2_256_k[i + 7], w[7]);
        hash_sha2_256_expand(wv, 0, hash_sha2_256_k[i + 8], w[8]);
        hash_sha2_256_expand(wv, 7, hash_sha2_256_k[i + 9], w[9]);
        hash_sha2_256_expand(wv, 6, hash_sha2_256_k[i + 10], w[10]);
        hash_sha2_256_expand(wv, 5, hash_sha2_256_k[i + 11], w[11]);
        hash_sha2_256_expand(wv, 4, hash_sha2_256_k[i + 12], w[12]);
        hash_sha2_256_expand(wv, 3, hash_sha2_256_k[i + 13], w[13]);
        hash_sha2_256_expand(wv, 2, hash_sha2_256_k[i + 14], w[14]);
        hash_sha2_256_expand(wv, 1, hash_sha2_256_k[i + 15], w[15]);
    }

    for (int i = 0; i < 8; i++) {
        hash[i] += wv[i];
    }
}

/*
 * Hash a whole buffer with SHA2-256, writing 32 bytes to out.
 */
void
menai_sha2_256(const uint8_t *data, size_t len, uint8_t *out)
{
    uint32_t h[HASH_SHA2_256_WORDS];
    hash_sha2_256_init(h);

    size_t offset = 0;
    while (len - offset >= HASH_SHA2_256_BLOCK) {
        hash_sha2_256_transform(h, data + offset);
        offset += HASH_SHA2_256_BLOCK;
    }

    uint8_t block[HASH_SHA2_256_BLOCK];
    size_t rem = len - offset;
    if (rem > 0) {
        hash_copy_bytes(block, data + offset, rem);
    }

    block[rem++] = 0x80;
    if (rem > HASH_SHA2_256_BLOCK - 8) {
        memset(block + rem, 0, HASH_SHA2_256_BLOCK - rem);
        hash_sha2_256_transform(h, block);
        rem = 0;
    }

    memset(block + rem, 0, HASH_SHA2_256_BLOCK - 8 - rem);
    uint64_t *bl = (uint64_t *)(block + HASH_SHA2_256_BLOCK - 8);
    *bl = hash_u64_host_to_be((uint64_t)len << 3);
    hash_sha2_256_transform(h, block);

    for (int i = 0; i < HASH_SHA2_256_WORDS; i++) {
        h[i] = hash_u32_be_to_host(h[i]);
    }

    memcpy(out, h, HASH_SHA2_256_SIZE);
}

static const uint64_t hash_sha2_512_h0[8] = {
    0x6a09e667f3bcc908ULL, 0xbb67ae8584caa73bULL,
    0x3c6ef372fe94f82bULL, 0xa54ff53a5f1d36f1ULL,
    0x510e527fade682d1ULL, 0x9b05688c2b3e6c1fULL,
    0x1f83d9abfb41bd6bULL, 0x5be0cd19137e2179ULL
};

static const uint64_t hash_sha2_512_256_h0[8] = {
    0x22312194fc2bf72cULL, 0x9f555fa3c84c64c2ULL,
    0x2393b86b6f53b151ULL, 0x963877195940eabdULL,
    0x96283ee2a88effe3ULL, 0xbe5e1e2553863992ULL,
    0x2b0199fc2c85b8aaULL, 0x0eb72ddc81c52ca2ULL
};

static const uint64_t hash_sha2_512_k[80] = {
    0x428a2f98d728ae22ULL, 0x7137449123ef65cdULL,
    0xb5c0fbcfec4d3b2fULL, 0xe9b5dba58189dbbcULL,
    0x3956c25bf348b538ULL, 0x59f111f1b605d019ULL,
    0x923f82a4af194f9bULL, 0xab1c5ed5da6d8118ULL,
    0xd807aa98a3030242ULL, 0x12835b0145706fbeULL,
    0x243185be4ee4b28cULL, 0x550c7dc3d5ffb4e2ULL,
    0x72be5d74f27b896fULL, 0x80deb1fe3b1696b1ULL,
    0x9bdc06a725c71235ULL, 0xc19bf174cf692694ULL,
    0xe49b69c19ef14ad2ULL, 0xefbe4786384f25e3ULL,
    0x0fc19dc68b8cd5b5ULL, 0x240ca1cc77ac9c65ULL,
    0x2de92c6f592b0275ULL, 0x4a7484aa6ea6e483ULL,
    0x5cb0a9dcbd41fbd4ULL, 0x76f988da831153b5ULL,
    0x983e5152ee66dfabULL, 0xa831c66d2db43210ULL,
    0xb00327c898fb213fULL, 0xbf597fc7beef0ee4ULL,
    0xc6e00bf33da88fc2ULL, 0xd5a79147930aa725ULL,
    0x06ca6351e003826fULL, 0x142929670a0e6e70ULL,
    0x27b70a8546d22ffcULL, 0x2e1b21385c26c926ULL,
    0x4d2c6dfc5ac42aedULL, 0x53380d139d95b3dfULL,
    0x650a73548baf63deULL, 0x766a0abb3c77b2a8ULL,
    0x81c2c92e47edaee6ULL, 0x92722c851482353bULL,
    0xa2bfe8a14cf10364ULL, 0xa81a664bbc423001ULL,
    0xc24b8b70d0f89791ULL, 0xc76c51a30654be30ULL,
    0xd192e819d6ef5218ULL, 0xd69906245565a910ULL,
    0xf40e35855771202aULL, 0x106aa07032bbd1b8ULL,
    0x19a4c116b8d2d0c8ULL, 0x1e376c085141ab53ULL,
    0x2748774cdf8eeb99ULL, 0x34b0bcb5e19b48a8ULL,
    0x391c0cb3c5c95a63ULL, 0x4ed8aa4ae3418acbULL,
    0x5b9cca4f7763e373ULL, 0x682e6ff3d6b2b8a3ULL,
    0x748f82ee5defb2fcULL, 0x78a5636f43172f60ULL,
    0x84c87814a1f0ab72ULL, 0x8cc702081a6439ecULL,
    0x90befffa23631e28ULL, 0xa4506cebde82bde9ULL,
    0xbef9a3f7b2c67915ULL, 0xc67178f2e372532bULL,
    0xca273eceea26619cULL, 0xd186b8c721c0c207ULL,
    0xeada7dd6cde0eb1eULL, 0xf57d4f7fee6ed178ULL,
    0x06f067aa72176fbaULL, 0x0a637dc5a2c898a6ULL,
    0x113f9804bef90daeULL, 0x1b710b35131c471bULL,
    0x28db77f523047d84ULL, 0x32caab7b40c72493ULL,
    0x3c9ebe0a15c9bebcULL, 0x431d67c49c100d4cULL,
    0x4cc5d4becb3e42b6ULL, 0x597f299cfc657e2aULL,
    0x5fcb6fab3ad6faecULL, 0x6c44198c4a475817ULL
};

/*
 * Bitwise rotate right.
 */
static inline uint64_t
hash_sha2_512_rotr(uint64_t x, int n)
{
    return (x >> n) | (x << (64 - n));
}

/*
 * SHA2 Ch.
 */
static inline uint64_t
hash_sha2_512_ch(uint64_t x, uint64_t y, uint64_t z)
{
    return (x & y) ^ (~x & z);
}

/*
 * SHA2 Maj.
 */
static inline uint64_t
hash_sha2_512_maj(uint64_t x, uint64_t y, uint64_t z)
{
    return (x & y) ^ (x & z) ^ (y & z);
}

/*
 * SHA2 sum 0.
 */
static inline uint64_t
hash_sha2_512_sum0(uint64_t x)
{
    return hash_sha2_512_rotr(x, 28) ^ hash_sha2_512_rotr(x, 34) ^ hash_sha2_512_rotr(x, 39);
}

/*
 * SHA2 sum 1.
 */
static inline uint64_t
hash_sha2_512_sum1(uint64_t x)
{
    return hash_sha2_512_rotr(x, 14) ^ hash_sha2_512_rotr(x, 18) ^ hash_sha2_512_rotr(x, 41);
}

/*
 * SHA2 sigma 0.
 */
static inline uint64_t
hash_sha2_512_sigma0(uint64_t x)
{
    return hash_sha2_512_rotr(x, 1) ^ hash_sha2_512_rotr(x, 8) ^ (x >> 7);
}

/*
 * SHA2 sigma 1.
 */
static inline uint64_t
hash_sha2_512_sigma1(uint64_t x)
{
    return hash_sha2_512_rotr(x, 19) ^ hash_sha2_512_rotr(x, 61) ^ (x >> 6);
}

/*
 * Generate the schedule of W values for subscripts 16 though 79.
 */
static inline uint64_t
hash_sha2_512_gen_w(const uint64_t *w, int i)
{
    return hash_sha2_512_sigma1(w[(i - 2) & 15]) + w[(i - 7) & 15] + hash_sha2_512_sigma0(w[(i - 15) & 15]) + w[(i - 16) & 15];
}

/*
 * Expand for each round.
 */
static inline void
hash_sha2_512_expand(uint64_t *wv, int r, uint64_t kj, uint64_t wj)
{
    uint64_t t1 = wv[(r + 7) & 7] + hash_sha2_512_sum1(wv[(r + 4) & 7]) + hash_sha2_512_ch(wv[(r + 4) & 7], wv[(r + 5) & 7], wv[(r + 6) & 7]) + kj + wj;
    uint64_t t2 = hash_sha2_512_sum0(wv[r]) + hash_sha2_512_maj(wv[r], wv[(r + 1) & 7], wv[(r + 2) & 7]);
    wv[(r + 3) & 7] += t1;
    wv[(r + 7) & 7] = t1 + t2;
}

/*
 * Core of the SHA2-512 transform.
 */
static void
hash_sha2_512_transform(uint64_t *hash, const uint8_t *block)
{
    uint64_t wv[8];
    for (int i = 0; i < 8; i++) {
        wv[i] = hash[i];
    }

    uint64_t w[16];
    const uint64_t *nb = (const uint64_t *)block;
    for (int i = 0; i < 16; i++) {
        w[i] = hash_u64_be_to_host(nb[i]);
    }

    hash_sha2_512_expand(wv, 0, hash_sha2_512_k[0], w[0]);
    hash_sha2_512_expand(wv, 7, hash_sha2_512_k[1], w[1]);
    hash_sha2_512_expand(wv, 6, hash_sha2_512_k[2], w[2]);
    hash_sha2_512_expand(wv, 5, hash_sha2_512_k[3], w[3]);
    hash_sha2_512_expand(wv, 4, hash_sha2_512_k[4], w[4]);
    hash_sha2_512_expand(wv, 3, hash_sha2_512_k[5], w[5]);
    hash_sha2_512_expand(wv, 2, hash_sha2_512_k[6], w[6]);
    hash_sha2_512_expand(wv, 1, hash_sha2_512_k[7], w[7]);
    hash_sha2_512_expand(wv, 0, hash_sha2_512_k[8], w[8]);
    hash_sha2_512_expand(wv, 7, hash_sha2_512_k[9], w[9]);
    hash_sha2_512_expand(wv, 6, hash_sha2_512_k[10], w[10]);
    hash_sha2_512_expand(wv, 5, hash_sha2_512_k[11], w[11]);
    hash_sha2_512_expand(wv, 4, hash_sha2_512_k[12], w[12]);
    hash_sha2_512_expand(wv, 3, hash_sha2_512_k[13], w[13]);
    hash_sha2_512_expand(wv, 2, hash_sha2_512_k[14], w[14]);
    hash_sha2_512_expand(wv, 1, hash_sha2_512_k[15], w[15]);

    for (int i = 16; i < 80; i += 16) {
        for (int j = 0; j < 16; j++) {
            w[j] = hash_sha2_512_gen_w(w, j);
        }

        hash_sha2_512_expand(wv, 0, hash_sha2_512_k[i], w[0]);
        hash_sha2_512_expand(wv, 7, hash_sha2_512_k[i + 1], w[1]);
        hash_sha2_512_expand(wv, 6, hash_sha2_512_k[i + 2], w[2]);
        hash_sha2_512_expand(wv, 5, hash_sha2_512_k[i + 3], w[3]);
        hash_sha2_512_expand(wv, 4, hash_sha2_512_k[i + 4], w[4]);
        hash_sha2_512_expand(wv, 3, hash_sha2_512_k[i + 5], w[5]);
        hash_sha2_512_expand(wv, 2, hash_sha2_512_k[i + 6], w[6]);
        hash_sha2_512_expand(wv, 1, hash_sha2_512_k[i + 7], w[7]);
        hash_sha2_512_expand(wv, 0, hash_sha2_512_k[i + 8], w[8]);
        hash_sha2_512_expand(wv, 7, hash_sha2_512_k[i + 9], w[9]);
        hash_sha2_512_expand(wv, 6, hash_sha2_512_k[i + 10], w[10]);
        hash_sha2_512_expand(wv, 5, hash_sha2_512_k[i + 11], w[11]);
        hash_sha2_512_expand(wv, 4, hash_sha2_512_k[i + 12], w[12]);
        hash_sha2_512_expand(wv, 3, hash_sha2_512_k[i + 13], w[13]);
        hash_sha2_512_expand(wv, 2, hash_sha2_512_k[i + 14], w[14]);
        hash_sha2_512_expand(wv, 1, hash_sha2_512_k[i + 15], w[15]);
    }

    for (int i = 0; i < 8; i++) {
        hash[i] += wv[i];
    }
}

/*
 * Hash a whole buffer with SHA2-512 or SHA2-512/256.
 *
 * The two algorithms share the same transform and block size; they differ only
 * in their initial working state and the number of output words.  out_words is
 * 8 for SHA2-512 and 4 for SHA2-512/256.
 */
static void
hash_sha2_512_common(const uint8_t *data, size_t len, uint8_t *out, const uint64_t *h0, int out_words)
{
    uint64_t h[HASH_SHA2_512_WORDS];
    for (int i = 0; i < HASH_SHA2_512_WORDS; i++) {
        h[i] = h0[i];
    }

    size_t offset = 0;
    while (len - offset >= HASH_SHA2_512_BLOCK) {
        hash_sha2_512_transform(h, data + offset);
        offset += HASH_SHA2_512_BLOCK;
    }

    uint8_t block[HASH_SHA2_512_BLOCK];
    size_t rem = len - offset;
    if (rem > 0) {
        hash_copy_bytes(block, data + offset, rem);
    }

    block[rem++] = 0x80;
    if (rem > HASH_SHA2_512_BLOCK - 16) {
        memset(block + rem, 0, HASH_SHA2_512_BLOCK - rem);
        hash_sha2_512_transform(h, block);
        rem = 0;
    }

    memset(block + rem, 0, HASH_SHA2_512_BLOCK - 8 - rem);
    uint64_t *bl = (uint64_t *)(block + HASH_SHA2_512_BLOCK - 8);
    *bl = hash_u64_host_to_be((uint64_t)len << 3);
    hash_sha2_512_transform(h, block);

    for (int i = 0; i < out_words; i++) {
        h[i] = hash_u64_be_to_host(h[i]);
    }

    memcpy(out, h, (size_t)out_words * sizeof(uint64_t));
}

/*
 * Hash a whole buffer with SHA2-512, writing 64 bytes to out.
 */
void
menai_sha2_512(const uint8_t *data, size_t len, uint8_t *out)
{
    hash_sha2_512_common(data, len, out, hash_sha2_512_h0, HASH_SHA2_512_WORDS);
}

/*
 * Hash a whole buffer with SHA2-512/256, writing 32 bytes to out.
 */
void
menai_sha2_512_256(const uint8_t *data, size_t len, uint8_t *out)
{
    hash_sha2_512_common(data, len, out, hash_sha2_512_256_h0, HASH_SHA2_512_256_SIZE / sizeof(uint64_t));
}

static const uint64_t hash_sha3_iota_const[24] = {
    0x0000000000000001ULL,
    0x0000000000008082ULL,
    0x800000000000808aULL,
    0x8000000080008000ULL,
    0x000000000000808bULL,
    0x0000000080000001ULL,
    0x8000000080008081ULL,
    0x8000000000008009ULL,
    0x000000000000008aULL,
    0x0000000000000088ULL,
    0x0000000080008009ULL,
    0x000000008000000aULL,
    0x000000008000808bULL,
    0x800000000000008bULL,
    0x8000000000008089ULL,
    0x8000000000008003ULL,
    0x8000000000008002ULL,
    0x8000000000000080ULL,
    0x000000000000800aULL,
    0x800000008000000aULL,
    0x8000000080008081ULL,
    0x8000000000008080ULL,
    0x0000000080000001ULL,
    0x8000000080008008ULL
};

/*
 * Bitwise rotate left.
 */
static inline uint64_t
hash_sha3_rotl64(uint64_t x, int y)
{
    return (x << y) | (x >> (64 - y));
}

/*
 * Theta step 1.
 */
static inline uint64_t
hash_sha3_theta_step_1(const uint64_t *s, int x)
{
    return s[x] ^ s[5 + x] ^ s[10 + x] ^ s[15 + x] ^ s[20 + x];
}

/*
 * Theta steps 2 and 3.
 */
static inline void
hash_sha3_theta_step_2_3(uint64_t *s, uint64_t c_minus_1, uint64_t c_plus_1, int x)
{
    uint64_t t = c_minus_1 ^ hash_sha3_rotl64(c_plus_1, 1);
    s[x] ^= t;
    s[5 + x] ^= t;
    s[10 + x] ^= t;
    s[15 + x] ^= t;
    s[20 + x] ^= t;
}

/*
 * Full theta stage.
 */
static inline void
hash_sha3_theta(uint64_t *s)
{
    uint64_t c0 = hash_sha3_theta_step_1(s, 0);
    uint64_t c1 = hash_sha3_theta_step_1(s, 1);
    uint64_t c2 = hash_sha3_theta_step_1(s, 2);
    uint64_t c3 = hash_sha3_theta_step_1(s, 3);
    uint64_t c4 = hash_sha3_theta_step_1(s, 4);

    hash_sha3_theta_step_2_3(s, c4, c1, 0);
    hash_sha3_theta_step_2_3(s, c0, c2, 1);
    hash_sha3_theta_step_2_3(s, c1, c3, 2);
    hash_sha3_theta_step_2_3(s, c2, c4, 3);
    hash_sha3_theta_step_2_3(s, c3, c0, 4);
}

/*
 * Rho and pi manipulations.
 *
 * Rho rotates the lanes, while pi moves the lanes around.  The most efficient
 * way to do this is to do both at the same time.
 *
 * Note that we don't do anything for the lane x = 0, y = 0.
 */
static inline void
hash_sha3_rho_pi(uint64_t *s)
{
    uint64_t s1 = s[1];
    uint64_t s10 = s[10];
    s[10] = hash_sha3_rotl64(s1, 1);
    uint64_t s7 = s[7];
    s[7] = hash_sha3_rotl64(s10, 3);
    uint64_t s11 = s[11];
    s[11] = hash_sha3_rotl64(s7, 6);
    uint64_t s17 = s[17];
    s[17] = hash_sha3_rotl64(s11, 10);
    uint64_t s18 = s[18];
    s[18] = hash_sha3_rotl64(s17, 15);
    uint64_t s3 = s[3];
    s[3] = hash_sha3_rotl64(s18, 21);
    uint64_t s5 = s[5];
    s[5] = hash_sha3_rotl64(s3, 28);
    uint64_t s16 = s[16];
    s[16] = hash_sha3_rotl64(s5, 36);
    uint64_t s8 = s[8];
    s[8] = hash_sha3_rotl64(s16, 45);
    uint64_t s21 = s[21];
    s[21] = hash_sha3_rotl64(s8, 55);
    uint64_t s24 = s[24];
    s[24] = hash_sha3_rotl64(s21, 2);
    uint64_t s4 = s[4];
    s[4] = hash_sha3_rotl64(s24, 14);
    uint64_t s15 = s[15];
    s[15] = hash_sha3_rotl64(s4, 27);
    uint64_t s23 = s[23];
    s[23] = hash_sha3_rotl64(s15, 41);
    uint64_t s19 = s[19];
    s[19] = hash_sha3_rotl64(s23, 56);
    uint64_t s13 = s[13];
    s[13] = hash_sha3_rotl64(s19, 8);
    uint64_t s12 = s[12];
    s[12] = hash_sha3_rotl64(s13, 25);
    uint64_t s2 = s[2];
    s[2] = hash_sha3_rotl64(s12, 43);
    uint64_t s20 = s[20];
    s[20] = hash_sha3_rotl64(s2, 62);
    uint64_t s14 = s[14];
    s[14] = hash_sha3_rotl64(s20, 18);
    uint64_t s22 = s[22];
    s[22] = hash_sha3_rotl64(s14, 39);
    uint64_t s9 = s[9];
    s[9] = hash_sha3_rotl64(s22, 61);
    uint64_t s6 = s[6];
    s[6] = hash_sha3_rotl64(s9, 20);
    s[1] = hash_sha3_rotl64(s6, 44);
}

/*
 * Chi step 1.
 */
static inline void
hash_sha3_chi_step_1(uint64_t *s, int y)
{
    uint64_t sy0 = s[y];
    uint64_t sy1 = s[y + 1];
    uint64_t sy2 = s[y + 2];
    uint64_t sy3 = s[y + 3];
    uint64_t sy4 = s[y + 4];

    s[y] ^= (~sy1) & sy2;
    s[y + 1] ^= (~sy2) & sy3;
    s[y + 2] ^= (~sy3) & sy4;
    s[y + 3] ^= (~sy4) & sy0;
    s[y + 4] ^= (~sy0) & sy1;
}

/*
 * Full chi stage.
 */
static inline void
hash_sha3_chi(uint64_t *s)
{
    hash_sha3_chi_step_1(s, 0);
    hash_sha3_chi_step_1(s, 5);
    hash_sha3_chi_step_1(s, 10);
    hash_sha3_chi_step_1(s, 15);
    hash_sha3_chi_step_1(s, 20);
}

/*
 * Iota stage.
 */
static inline void
hash_sha3_iota(uint64_t *s, int round)
{
    s[0] ^= hash_sha3_iota_const[round];
}

/*
 * Core of the SHA3-256 Keccak operation.  block_size is the rate in bytes.
 */
static void
hash_sha3_keccak(uint64_t *s, const uint8_t *block, size_t block_size)
{
    /*
     * Fold the block into the sponge.
     */
    const uint64_t *b = (const uint64_t *)block;
    size_t num_words = block_size / sizeof(uint64_t);
    for (size_t i = 0; i < num_words; i++) {
        s[i] ^= hash_u64_le_to_host(b[i]);
    }

    /*
     * Run the Keccak algorithm.
     */
    for (int round = 0; round < 24; round++) {
        hash_sha3_theta(s);
        hash_sha3_rho_pi(s);
        hash_sha3_chi(s);
        hash_sha3_iota(s, round);
    }
}

/*
 * Hash a whole buffer with SHA3-256, writing 32 bytes to out.
 */
void
menai_sha3_256(const uint8_t *data, size_t len, uint8_t *out)
{
    uint64_t s[HASH_SHA3_SPONGE_WORDS];
    memset(s, 0, sizeof(s));

    size_t offset = 0;
    while (len - offset >= HASH_SHA3_256_BLOCK) {
        hash_sha3_keccak(s, data + offset, HASH_SHA3_256_BLOCK);
        offset += HASH_SHA3_256_BLOCK;
    }

    uint8_t block[HASH_SHA3_256_BLOCK];
    size_t rem = len - offset;
    if (rem > 0) {
        hash_copy_bytes(block, data + offset, rem);
    }

    block[rem] = 0x06;
    memset(block + rem + 1, 0, HASH_SHA3_256_BLOCK - rem - 1);
    block[HASH_SHA3_256_BLOCK - 1] ^= 0x80;
    hash_sha3_keccak(s, block, HASH_SHA3_256_BLOCK);

    for (size_t i = 0; i < HASH_SHA3_256_SIZE / sizeof(uint64_t); i++) {
        s[i] = hash_u64_le_to_host(s[i]);
    }

    memcpy(out, s, HASH_SHA3_256_SIZE);
}
