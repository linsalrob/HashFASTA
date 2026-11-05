/* Parallel gzip writer: one gzip member compressed on several threads.
 *
 * Output is split into fixed-size blocks. Worker threads compress each block
 * as raw deflate, primed with the previous 32 KiB as a dictionary and ended
 * with a sync flush (the last block with Z_FINISH), so the blocks join into a
 * single deflate stream (the pigz technique). The calling thread writes the
 * blocks in order behind one gzip header and combines their CRC-32s. */
#ifndef PGZIP_H
#define PGZIP_H

#include <stddef.h>

typedef struct pgzip pgzip;

/* Start a gzip stream on fd using `threads` compression threads. Returns NULL
 * if memory or threads are unavailable. */
pgzip *pgzip_open(int fd, int level, int threads);

/* Return 0, or -1 on failure with errno set (EIO for a zlib error). */
int pgzip_write(pgzip *p, const void *data, size_t len);

/* Finish the stream, stop the threads and free p. Does not close fd.
 * Return 0, or -1 on failure with errno set. */
int pgzip_close(pgzip *p);

#endif
