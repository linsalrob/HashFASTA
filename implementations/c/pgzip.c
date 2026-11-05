/* Parallel gzip writer; see pgzip.h. */
#include "pgzip.h"

#include <errno.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <zlib.h>

#define BLOCK (128 * 1024) /* uncompressed bytes per job */
#define DICT 32768         /* deflate window */

enum slot_state { SLOT_FREE, SLOT_QUEUED, SLOT_DONE };

typedef struct {
    unsigned char *in, *out;
    size_t in_len, out_len, out_cap;
    unsigned char dict[DICT];
    size_t dict_len;
    unsigned long crc;
    int last, failed;
    enum slot_state state;
} slot;

struct pgzip {
    int fd, level, threads, nslots, started, stop, failed;
    slot *slots;
    unsigned long long submitted, taken, written; /* job counters */
    unsigned char *cur;                           /* block being filled */
    size_t cur_len;
    unsigned char tail[DICT]; /* last DICT bytes submitted: next job's dictionary */
    size_t tail_len;
    unsigned long crc;
    unsigned long long total;
    pthread_mutex_t lock;
    pthread_cond_t work, done;
    pthread_t *tids;
};

static int write_all(int fd, const unsigned char *data, size_t len) {
    while (len) {
        ssize_t n = write(fd, data, len);
        if (n < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        data += n;
        len -= (size_t)n;
    }
    return 0;
}

/* Compress one job with a worker's deflate stream; return 0 on success. */
static int compress_slot(z_stream *z, slot *s) {
    int flush = s->last ? Z_FINISH : Z_SYNC_FLUSH;
    size_t need = deflateBound(z, s->in_len) + 64;

    if (deflateReset(z) != Z_OK) return -1;
    if (s->dict_len && deflateSetDictionary(z, s->dict, (uInt)s->dict_len) != Z_OK) return -1;
    if (s->out_cap < need) {
        unsigned char *out = realloc(s->out, need);
        if (!out) return -1;
        s->out = out;
        s->out_cap = need;
    }
    z->next_in = s->in;
    z->avail_in = (uInt)s->in_len;
    s->out_len = 0;
    for (;;) {
        int rc;
        z->next_out = s->out + s->out_len;
        z->avail_out = (uInt)(s->out_cap - s->out_len);
        rc = deflate(z, flush);
        s->out_len = s->out_cap - z->avail_out;
        if (rc == Z_STREAM_ERROR) return -1;
        if (s->last ? rc == Z_STREAM_END : (z->avail_in == 0 && z->avail_out > 0)) break;
        if (z->avail_out == 0) { /* deflateBound was too small: grow and continue */
            unsigned char *out = realloc(s->out, s->out_cap * 2);
            if (!out) return -1;
            s->out = out;
            s->out_cap *= 2;
        }
    }
    s->crc = crc32(0L, s->in, (uInt)s->in_len);
    return 0;
}

static void *worker(void *arg) {
    pgzip *p = arg;
    z_stream z;
    int ok;

    memset(&z, 0, sizeof z);
    ok = deflateInit2(&z, p->level, Z_DEFLATED, -15, 8, Z_DEFAULT_STRATEGY) == Z_OK;
    pthread_mutex_lock(&p->lock);
    for (;;) {
        slot *s;
        while (!p->stop && p->taken == p->submitted) pthread_cond_wait(&p->work, &p->lock);
        if (p->taken == p->submitted) break; /* stopping, nothing queued */
        s = &p->slots[p->taken++ % (unsigned)p->nslots];
        pthread_mutex_unlock(&p->lock);
        s->failed = !ok || compress_slot(&z, s) != 0;
        pthread_mutex_lock(&p->lock);
        s->state = SLOT_DONE;
        pthread_cond_broadcast(&p->done);
    }
    pthread_mutex_unlock(&p->lock);
    if (ok) deflateEnd(&z);
    return NULL;
}

/* Wait for the oldest unwritten job and write it out. */
static int write_next(pgzip *p) {
    slot *s = &p->slots[p->written % (unsigned)p->nslots];
    pthread_mutex_lock(&p->lock);
    while (s->state != SLOT_DONE) pthread_cond_wait(&p->done, &p->lock);
    pthread_mutex_unlock(&p->lock);
    if (s->failed) {
        errno = EIO;
        return -1;
    }
    if (write_all(p->fd, s->out, s->out_len) != 0) return -1;
    p->crc = crc32_combine(p->crc, s->crc, (z_off_t)s->in_len);
    p->total += s->in_len;
    s->state = SLOT_FREE;
    p->written++;
    return 0;
}

/* Queue the filled block as the next job. */
static int submit(pgzip *p, int last) {
    slot *s;
    unsigned char *swap;

    while (p->written + (unsigned)p->nslots <= p->submitted)
        if (write_next(p) != 0) return -1;
    s = &p->slots[p->submitted % (unsigned)p->nslots];
    swap = s->in;
    s->in = p->cur;
    s->in_len = p->cur_len;
    p->cur = swap;
    p->cur_len = 0;
    memcpy(s->dict, p->tail, p->tail_len);
    s->dict_len = p->tail_len;
    s->last = last;
    if (s->in_len >= DICT) {
        memcpy(p->tail, s->in + s->in_len - DICT, DICT);
        p->tail_len = DICT;
    } else {
        size_t keep = p->tail_len < DICT - s->in_len ? p->tail_len : DICT - s->in_len;
        memmove(p->tail, p->tail + p->tail_len - keep, keep);
        memcpy(p->tail + keep, s->in, s->in_len);
        p->tail_len = keep + s->in_len;
    }
    pthread_mutex_lock(&p->lock);
    s->state = SLOT_QUEUED;
    p->submitted++;
    pthread_cond_signal(&p->work);
    pthread_mutex_unlock(&p->lock);
    return 0;
}

static void destroy(pgzip *p) {
    int i;
    pthread_mutex_lock(&p->lock);
    p->stop = 1;
    pthread_cond_broadcast(&p->work);
    pthread_mutex_unlock(&p->lock);
    for (i = 0; i < p->started; i++) pthread_join(p->tids[i], NULL);
    for (i = 0; i < p->nslots; i++) {
        free(p->slots[i].in);
        free(p->slots[i].out);
    }
    pthread_mutex_destroy(&p->lock);
    pthread_cond_destroy(&p->work);
    pthread_cond_destroy(&p->done);
    free(p->slots);
    free(p->tids);
    free(p->cur);
    free(p);
}

pgzip *pgzip_open(int fd, int level, int threads) {
    /* gzip header: deflate, no flags, mtime 0, no extra flags, OS = Unix */
    static const unsigned char header[10] = {0x1f, 0x8b, 8, 0, 0, 0, 0, 0, 0, 3};
    pgzip *p = calloc(1, sizeof *p);
    int i;

    if (!p) return NULL;
    p->fd = fd;
    p->level = level;
    p->threads = threads;
    p->nslots = 2 * threads;
    p->crc = crc32(0L, Z_NULL, 0);
    pthread_mutex_init(&p->lock, NULL);
    pthread_cond_init(&p->work, NULL);
    pthread_cond_init(&p->done, NULL);
    p->slots = calloc((size_t)p->nslots, sizeof *p->slots);
    p->tids = calloc((size_t)threads, sizeof *p->tids);
    p->cur = malloc(BLOCK);
    if (!p->slots || !p->tids || !p->cur) {
        destroy(p);
        return NULL;
    }
    for (i = 0; i < p->nslots; i++) {
        if (!(p->slots[i].in = malloc(BLOCK))) {
            destroy(p);
            return NULL;
        }
    }
    for (i = 0; i < threads; i++) {
        if (pthread_create(&p->tids[i], NULL, worker, p) != 0) {
            destroy(p);
            return NULL;
        }
        p->started++;
    }
    if (write_all(fd, header, sizeof header) != 0) p->failed = errno ? errno : EIO;
    return p;
}

int pgzip_write(pgzip *p, const void *data, size_t len) {
    const unsigned char *d = data;
    if (p->failed) {
        errno = p->failed;
        return -1;
    }
    while (len) {
        size_t n = BLOCK - p->cur_len < len ? BLOCK - p->cur_len : len;
        memcpy(p->cur + p->cur_len, d, n);
        p->cur_len += n;
        d += n;
        len -= n;
        if (p->cur_len == BLOCK && submit(p, 0) != 0) {
            p->failed = errno ? errno : EIO;
            return -1;
        }
    }
    return 0;
}

int pgzip_close(pgzip *p) {
    unsigned char trailer[8];
    int i, err = p->failed;

    if (!err && submit(p, 1) != 0) err = errno ? errno : EIO;
    while (!err && p->written < p->submitted)
        if (write_next(p) != 0) err = errno ? errno : EIO;
    if (!err) {
        for (i = 0; i < 4; i++) {
            trailer[i] = (unsigned char)(p->crc >> (8 * i));
            trailer[4 + i] = (unsigned char)(p->total >> (8 * i)); /* size mod 2^32 */
        }
        if (write_all(p->fd, trailer, sizeof trailer) != 0) err = errno ? errno : EIO;
    }
    destroy(p);
    if (err) {
        errno = err;
        return -1;
    }
    return 0;
}
