/* HashFASTA v0.1 converter (C port of the Python reference).
 *
 * Usage: hashfasta INPUT [-o OUTPUT]   ('-' means stdin / stdout)
 *
 * Input format is detected from its magic bytes and decoded as a stream:
 * gzip (zlib inflate), bzip2 (libbz2) or xz (liblzma), each reading
 * concatenated members/streams as one input; anything else is plain text.
 * Output is written with zlib's gzwrite().
 */
#include <bzlib.h>
#include <errno.h>
#include <fcntl.h>
#include <lzma.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include <zlib.h>

#include "sha256.h"

#define PROG "hashfasta"
#define LINE_WIDTH 80
#define IO_BUFFER (1 << 17)
#define OUTPUT_SUFFIX ".hash.fasta.gz"
#define USAGE "usage: hashfasta INPUT [-o OUTPUT]   ('-' means stdin/stdout)\n"

static const char *COMPRESSION_SUFFIXES[] = {".gz", ".bgz", ".bz2", ".xz"};
static const char *FASTA_SUFFIXES[] = {".fa", ".fas", ".fasta", ".fna",
                                       ".ffn", ".faa", ".frn", ".fsa"};

static char *tmp_path = NULL; /* removed on error */

static void die(const char *fmt, ...) {
    va_list ap;
    fprintf(stderr, "%s: error: ", PROG);
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
    if (tmp_path) unlink(tmp_path);
    exit(1);
}

/* ---- streaming input decoder ---------------------------------------- */

enum format { FMT_PLAIN, FMT_GZIP, FMT_BZIP2, FMT_XZ };

typedef struct {
    int fd;
    enum format fmt;
    unsigned char *in;   /* raw (possibly compressed) bytes */
    size_t in_pos, in_len;
    int in_eof;
    int member_done;     /* gzip/bzip2: current member finished */
    int done;            /* no more decoded output */
    z_stream z;
    bz_stream bz;
    lzma_stream lz;
} reader;

static void fill(reader *r) {
    ssize_t n;
    do n = read(r->fd, r->in, IO_BUFFER);
    while (n < 0 && errno == EINTR);
    if (n < 0) die("read failed: %s", strerror(errno));
    r->in_pos = 0;
    r->in_len = (size_t)n;
    if (n == 0) r->in_eof = 1;
}

static void reader_open(reader *r, int fd) {
    static const unsigned char xz_magic[6] = {0xfd, '7', 'z', 'X', 'Z', 0x00};
    const unsigned char *h;
    memset(r, 0, sizeof *r);
    r->fd = fd;
    r->in = malloc(IO_BUFFER);
    if (!r->in) die("out of memory");
    /* Read at least 6 bytes (or to EOF) to see the magic number. */
    while (r->in_len < 6) {
        ssize_t n = read(fd, r->in + r->in_len, IO_BUFFER - r->in_len);
        if (n < 0 && errno == EINTR) continue;
        if (n < 0) die("read failed: %s", strerror(errno));
        if (n == 0) {
            r->in_eof = 1;
            break;
        }
        r->in_len += (size_t)n;
    }
    h = r->in;
    if (r->in_len >= 4 && memcmp(h, "\x28\xb5\x2f\xfd", 4) == 0)
        die("zstd-compressed input is not supported; decompress it first");
    if (r->in_len >= 4 && memcmp(h, "PK\x03\x04", 4) == 0)
        die("ZIP-compressed input is not supported; decompress it first");
    if (r->in_len >= 2 && h[0] == 0x1f && h[1] == 0x8b) {
        r->fmt = FMT_GZIP;
        if (inflateInit2(&r->z, 15 + 16) != Z_OK) die("zlib initialisation failed");
    } else if (r->in_len >= 3 && memcmp(h, "BZh", 3) == 0) {
        r->fmt = FMT_BZIP2;
        if (BZ2_bzDecompressInit(&r->bz, 0, 0) != BZ_OK) die("bzip2 initialisation failed");
    } else if (r->in_len >= 6 && memcmp(h, xz_magic, 6) == 0) {
        lzma_stream init = LZMA_STREAM_INIT;
        r->fmt = FMT_XZ;
        r->lz = init;
        if (lzma_stream_decoder(&r->lz, UINT64_MAX, LZMA_CONCATENATED) != LZMA_OK)
            die("xz initialisation failed");
    } else {
        r->fmt = FMT_PLAIN;
    }
}

static size_t read_gzip(reader *r, unsigned char *out, size_t cap) {
    for (;;) {
        size_t produced;
        int ret;
        if (r->in_pos == r->in_len && !r->in_eof) fill(r);
        if (r->member_done) {
            if (r->in_pos == r->in_len) return 0; /* end of the last member */
            inflateReset(&r->z);
            r->member_done = 0;
        }
        r->z.next_in = r->in + r->in_pos;
        r->z.avail_in = (uInt)(r->in_len - r->in_pos);
        r->z.next_out = out;
        r->z.avail_out = (uInt)cap;
        ret = inflate(&r->z, Z_NO_FLUSH);
        r->in_pos = r->in_len - r->z.avail_in;
        produced = cap - r->z.avail_out;
        if (ret == Z_STREAM_END) {
            r->member_done = 1;
        } else if (ret == Z_BUF_ERROR) {
            if (r->in_eof && r->in_pos == r->in_len) die("truncated gzip input");
        } else if (ret != Z_OK) {
            die("corrupt gzip input: %s", r->z.msg ? r->z.msg : zError(ret));
        }
        if (produced) return produced;
    }
}

static size_t read_bzip2(reader *r, unsigned char *out, size_t cap) {
    for (;;) {
        size_t produced;
        int ret;
        if (r->in_pos == r->in_len && !r->in_eof) fill(r);
        if (r->member_done) {
            if (r->in_pos == r->in_len) return 0;
            BZ2_bzDecompressEnd(&r->bz);
            memset(&r->bz, 0, sizeof r->bz);
            if (BZ2_bzDecompressInit(&r->bz, 0, 0) != BZ_OK) die("bzip2 initialisation failed");
            r->member_done = 0;
        }
        r->bz.next_in = (char *)r->in + r->in_pos;
        r->bz.avail_in = (unsigned)(r->in_len - r->in_pos);
        r->bz.next_out = (char *)out;
        r->bz.avail_out = (unsigned)cap;
        ret = BZ2_bzDecompress(&r->bz);
        r->in_pos = r->in_len - r->bz.avail_in;
        produced = cap - r->bz.avail_out;
        if (ret == BZ_STREAM_END) {
            r->member_done = 1;
        } else if (ret != BZ_OK) {
            die("corrupt bzip2 input (error %d)", ret);
        } else if (!produced && r->in_eof && r->in_pos == r->in_len) {
            die("truncated bzip2 input");
        }
        if (produced) return produced;
    }
}

static size_t read_xz(reader *r, unsigned char *out, size_t cap) {
    for (;;) {
        size_t produced;
        lzma_ret ret;
        if (r->done) return 0;
        if (r->in_pos == r->in_len && !r->in_eof) fill(r);
        r->lz.next_in = r->in + r->in_pos;
        r->lz.avail_in = r->in_len - r->in_pos;
        r->lz.next_out = out;
        r->lz.avail_out = cap;
        ret = lzma_code(&r->lz, r->in_eof ? LZMA_FINISH : LZMA_RUN);
        r->in_pos = r->in_len - r->lz.avail_in;
        produced = cap - r->lz.avail_out;
        if (ret == LZMA_STREAM_END) {
            r->done = 1;
        } else if (ret == LZMA_BUF_ERROR) {
            die("truncated xz input");
        } else if (ret != LZMA_OK) {
            die("corrupt xz input (liblzma error %d)", (int)ret);
        }
        if (produced) return produced;
    }
}

/* Decoded bytes into out (up to cap); 0 at end of input. Dies on error. */
static size_t reader_read(reader *r, unsigned char *out, size_t cap) {
    switch (r->fmt) {
    case FMT_GZIP: return read_gzip(r, out, cap);
    case FMT_BZIP2: return read_bzip2(r, out, cap);
    case FMT_XZ: return read_xz(r, out, cap);
    default: break;
    }
    if (r->in_pos == r->in_len && !r->in_eof) fill(r);
    {
        size_t n = r->in_len - r->in_pos < cap ? r->in_len - r->in_pos : cap;
        memcpy(out, r->in + r->in_pos, n);
        r->in_pos += n;
        return n;
    }
}

static void reader_close(reader *r) {
    if (r->fmt == FMT_GZIP) inflateEnd(&r->z);
    if (r->fmt == FMT_BZIP2) BZ2_bzDecompressEnd(&r->bz);
    if (r->fmt == FMT_XZ) lzma_end(&r->lz);
    free(r->in);
    if (r->fd != STDIN_FILENO) close(r->fd);
}

/* ---- conversion ------------------------------------------------------ */

typedef struct {
    unsigned char *data;
    size_t len, cap;
} buffer;

static void buf_push(buffer *b, unsigned char c) {
    if (b->len == b->cap) {
        b->cap = b->cap ? b->cap * 2 : 4096;
        b->data = realloc(b->data, b->cap);
        if (!b->data) die("out of memory");
    }
    b->data[b->len++] = c;
}

static int is_space(int c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\v' || c == '\f' || c == '\r';
}

static void gz_put(gzFile out, const void *data, size_t len) {
    if (len && gzwrite(out, data, (unsigned)len) != (int)len) {
        int err;
        const char *msg = gzerror(out, &err);
        die("write failed: %s", err == Z_ERRNO ? strerror(errno) : msg);
    }
}

static void write_record(gzFile out, const buffer *header, const buffer *seq,
                         size_t header_line) {
    static const char hex[] = "0123456789abcdef";
    unsigned char digest[32];
    char key[8 + 64 + 2];
    sha256_ctx ctx;
    buffer line = {0};
    size_t i;

    if (seq->len == 0) die("record at line %zu has an empty sequence", header_line);
    sha256_init(&ctx);
    sha256_update(&ctx, seq->data, seq->len);
    sha256_final(&ctx, digest);
    memcpy(key, ">sha256_", 8);
    for (i = 0; i < 32; i++) {
        key[8 + 2 * i] = hex[digest[i] >> 4];
        key[9 + 2 * i] = hex[digest[i] & 15];
    }
    key[72] = ' ';
    key[73] = '[';
    gz_put(out, key, sizeof key);
    for (i = 0; i < header->len; i++) {
        unsigned char c = header->data[i];
        if (c == '\\' || c == '[' || c == ']') buf_push(&line, '\\');
        buf_push(&line, c);
    }
    buf_push(&line, ']');
    buf_push(&line, '\n');
    gz_put(out, line.data, line.len);
    free(line.data);
    for (i = 0; i < seq->len; i += LINE_WIDTH) {
        size_t n = seq->len - i < LINE_WIDTH ? seq->len - i : LINE_WIDTH;
        gz_put(out, seq->data + i, n);
        gz_put(out, "\n", 1);
    }
}

enum state { LINE_START, IN_HEADER, IN_COMMENT, IN_SEQ };

/* Stream FASTA records from in to out, holding one record in memory. */
static void convert(reader *in, gzFile out) {
    unsigned char *chunk = malloc(IO_BUFFER);
    buffer header = {0}, seq = {0};
    enum state st = LINE_START;
    int have_header = 0;
    size_t lineno = 0, header_line = 0;
    size_t n;

    if (!chunk) die("out of memory");
    while ((n = reader_read(in, chunk, IO_BUFFER)) > 0) {
        size_t i;
        for (i = 0; i < n; i++) {
            unsigned char c = chunk[i];
            if (st == LINE_START) {
                lineno++;
                if (c == '>') {
                    if (have_header) write_record(out, &header, &seq, header_line);
                    have_header = 1;
                    header.len = 0;
                    seq.len = 0;
                    header_line = lineno;
                    st = IN_HEADER;
                    continue;
                }
                /* A line starting with ';' is a comment (spec section 3). */
                if (c == ';') {
                    st = IN_COMMENT;
                    continue;
                }
                st = IN_SEQ;
            }
            if (st == IN_HEADER) {
                /* Every CR is removed from the definition line (spec section 2). */
                if (c == '\n') st = LINE_START;
                else if (c != '\r') buf_push(&header, c);
                continue;
            }
            if (st == IN_COMMENT) {
                if (c == '\n') st = LINE_START;
                continue;
            }
            /* IN_SEQ */
            if (c == '\n') {
                st = LINE_START;
            } else if (is_space(c)) {
                continue;
            } else if (!have_header) {
                die("line %zu: sequence data before the first '>' header", lineno);
            } else if (c == ';') {
                die("line %zu: invalid sequence byte ';' (allowed only at the start of a comment "
                    "line) in record starting at line %zu", lineno, header_line);
            } else if (c < 0x21 || c > 0x7e) {
                die("line %zu: invalid sequence byte 0x%02x (non-ASCII or non-printable) "
                    "in record starting at line %zu", lineno, c, header_line);
            } else {
                buf_push(&seq, (c >= 'a' && c <= 'z') ? (unsigned char)(c - 32) : c);
            }
        }
    }
    if (!have_header) die("input contains no FASTA records");
    write_record(out, &header, &seq, header_line);
    free(chunk);
    free(header.data);
    free(seq.data);
}

/* Exclusively create a temporary sibling of output, recorded in tmp_path.
 * O_EXCL refuses any existing name, including a symlink or hard link to the
 * input, so a planted temporary path can never redirect the write. */
static int create_temp(const char *output) {
    size_t len = strlen(output) + 48;
    int n, fd;
    char *path = malloc(len);
    if (!path) die("out of memory");
    for (n = 0; n < 1000; n++) {
        snprintf(path, len, "%s.tmp.%ld.%d", output, (long)getpid(), n);
        fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0666);
        if (fd >= 0) {
            tmp_path = path;
            return fd;
        }
        if (errno != EEXIST) {
            free(path);
            die("cannot create %s: %s", output, strerror(errno));
        }
    }
    free(path);
    die("cannot create a temporary file beside %s", output);
    return -1;
}

/* Shorten *len by the first of suffixes that name[0..*len) ends with. */
static void strip_suffix(const char *name, size_t *len, const char **suffixes, size_t count) {
    size_t i;
    for (i = 0; i < count; i++) {
        size_t s = strlen(suffixes[i]);
        if (*len >= s && strncmp(name + *len - s, suffixes[i], s) == 0) {
            *len -= s;
            return;
        }
    }
}

static char *default_output_path(const char *input) {
    size_t len = strlen(input);
    char *out = malloc(len + sizeof OUTPUT_SUFFIX);
    if (!out) die("out of memory");
    strip_suffix(input, &len, COMPRESSION_SUFFIXES,
                 sizeof COMPRESSION_SUFFIXES / sizeof *COMPRESSION_SUFFIXES);
    strip_suffix(input, &len, FASTA_SUFFIXES, sizeof FASTA_SUFFIXES / sizeof *FASTA_SUFFIXES);
    memcpy(out, input, len);
    strcpy(out + len, OUTPUT_SUFFIX);
    return out;
}

static int same_file(const char *a, const char *b) {
    struct stat sa, sb;
    if (stat(a, &sa) != 0 || stat(b, &sb) != 0) return 0;
    return sa.st_dev == sb.st_dev && sa.st_ino == sb.st_ino;
}

static void close_output(gzFile out) {
    int rc = gzclose(out);
    if (rc != Z_OK) die("closing output failed (%s)", rc == Z_ERRNO ? strerror(errno) : zError(rc));
}

int main(int argc, char **argv) {
    const char *input = NULL, *output = NULL;
    char *default_output = NULL;
    reader in;
    gzFile out;
    int i, fd;

    for (i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            fputs(USAGE, stdout);
            return 0;
        } else if (strcmp(argv[i], "-o") == 0 && i + 1 < argc) {
            output = argv[++i];
        } else if ((argv[i][0] == '-' && argv[i][1] != '\0') || input) {
            fputs(USAGE, stderr);
            return 2;
        } else {
            input = argv[i];
        }
    }
    if (!input) {
        fputs(USAGE, stderr);
        return 2;
    }
    if (!output) output = strcmp(input, "-") == 0 ? "-" : (default_output = default_output_path(input));
    if (strcmp(input, "-") != 0 && strcmp(output, "-") != 0 && same_file(input, output))
        die("output path %s would overwrite the input", output);

    fd = strcmp(input, "-") == 0 ? STDIN_FILENO : open(input, O_RDONLY);
    if (fd < 0) die("cannot open input %s: %s", input, strerror(errno));
    reader_open(&in, fd);

    if (strcmp(output, "-") == 0) {
        out = gzdopen(STDOUT_FILENO, "wb");
        if (!out) die("cannot open stdout");
    } else {
        /* Write a temporary sibling, renamed into place only on success. */
        int tmp_fd = create_temp(output);
        out = gzdopen(tmp_fd, "wb");
        if (!out) die("cannot create %s: %s", output, strerror(errno));
    }
    gzbuffer(out, IO_BUFFER);

    convert(&in, out);
    reader_close(&in);
    close_output(out);
    if (tmp_path) {
        if (rename(tmp_path, output) != 0) die("cannot write %s: %s", output, strerror(errno));
        free(tmp_path);
        tmp_path = NULL;
    }
    free(default_output);
    return 0;
}
