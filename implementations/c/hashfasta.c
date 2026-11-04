/* HashFASTA v0.1 converter (C port of the Python reference).
 *
 * Usage: hashfasta INPUT [-o OUTPUT]   ('-' means stdin / stdout)
 *
 * zlib's gzread() detects gzip by its magic bytes, reads plain input
 * transparently and reads concatenated gzip members as one stream.
 */
#include <errno.h>
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

enum state { LINE_START, IN_HEADER, IN_SEQ };

/* Stream FASTA records from in to out, holding one record in memory. */
static void convert(gzFile in, gzFile out) {
    unsigned char *chunk = malloc(IO_BUFFER);
    buffer header = {0}, seq = {0};
    enum state st = LINE_START;
    int have_header = 0;
    size_t lineno = 0, header_line = 0;
    int n;

    if (!chunk) die("out of memory");
    while ((n = gzread(in, chunk, IO_BUFFER)) > 0) {
        int i;
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
                st = IN_SEQ;
            }
            if (st == IN_HEADER) {
                if (c == '\n') {
                    if (header.len && header.data[header.len - 1] == '\r') header.len--;
                    st = LINE_START;
                } else {
                    buf_push(&header, c);
                }
                continue;
            }
            /* IN_SEQ */
            if (c == '\n') {
                st = LINE_START;
            } else if (is_space(c)) {
                continue;
            } else if (!have_header) {
                die("line %zu: sequence data before the first '>' header", lineno);
            } else if (c < 0x21 || c > 0x7e) {
                die("line %zu: invalid sequence byte 0x%02x (non-ASCII or non-printable) "
                    "in record starting at line %zu", lineno, c, header_line);
            } else {
                buf_push(&seq, (c >= 'a' && c <= 'z') ? (unsigned char)(c - 32) : c);
            }
        }
    }
    if (n < 0) {
        int err;
        const char *msg = gzerror(in, &err);
        die("read failed: %s", err == Z_ERRNO ? strerror(errno) : msg);
    }
    if (st == IN_HEADER && header.len && header.data[header.len - 1] == '\r') header.len--;
    if (!have_header) die("input contains no FASTA records");
    write_record(out, &header, &seq, header_line);
    free(chunk);
    free(header.data);
    free(seq.data);
}

static char *default_output_path(const char *input) {
    size_t len = strlen(input), i;
    char *out = malloc(len + sizeof OUTPUT_SUFFIX);
    if (!out) die("out of memory");
    if (len >= 3 && strcmp(input + len - 3, ".gz") == 0) len -= 3;
    for (i = 0; i < sizeof FASTA_SUFFIXES / sizeof *FASTA_SUFFIXES; i++) {
        size_t s = strlen(FASTA_SUFFIXES[i]);
        if (len >= s && strncmp(input + len - s, FASTA_SUFFIXES[i], s) == 0) {
            len -= s;
            break;
        }
    }
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
    gzFile in, out;
    int i;

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

    in = strcmp(input, "-") == 0 ? gzdopen(STDIN_FILENO, "rb") : gzopen(input, "rb");
    if (!in) die("cannot open input %s: %s", input, strerror(errno));
    gzbuffer(in, IO_BUFFER);

    if (strcmp(output, "-") == 0) {
        out = gzdopen(STDOUT_FILENO, "wb");
        if (!out) die("cannot open stdout");
    } else {
        /* Write a temporary sibling, renamed into place only on success. */
        size_t len = strlen(output) + 32;
        tmp_path = malloc(len);
        if (!tmp_path) die("out of memory");
        snprintf(tmp_path, len, "%s.tmp.%ld", output, (long)getpid());
        out = gzopen(tmp_path, "wb");
        if (!out) {
            free(tmp_path);
            tmp_path = NULL;
            die("cannot create %s: %s", output, strerror(errno));
        }
    }
    gzbuffer(out, IO_BUFFER);

    convert(in, out);
    gzclose(in);
    close_output(out);
    if (tmp_path) {
        if (rename(tmp_path, output) != 0) die("cannot write %s: %s", output, strerror(errno));
        free(tmp_path);
        tmp_path = NULL;
    }
    free(default_output);
    return 0;
}
