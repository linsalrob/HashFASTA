#!/usr/bin/env python3
"""HashFASTA v0.1 reference converter.

Converts plain or gzip-compressed FASTA into a gzip-compressed HashFASTA
file whose record IDs are SHA-256 digests of the normalized sequence.
See spec/HASHFASTA-v0.1.md. Standard library only (hashlib, gzip).

Usage: hashfasta.py INPUT [-o OUTPUT]   ('-' means stdin / stdout)
"""

import gzip
import hashlib
import io
import os
import sys
import zlib

PROG = "hashfasta"
LINE_WIDTH = 80
FASTA_SUFFIXES = (".fa", ".fas", ".fasta", ".fna", ".ffn", ".faa", ".frn", ".fsa")
OUTPUT_SUFFIX = ".hash.fasta.gz"
GZIP_MAGIC = b"\x1f\x8b"

# Whitespace removed before hashing: space, HT, LF, VT, FF, CR.
WHITESPACE = b" \t\n\x0b\x0c\r"
# Map a-z to A-Z, every other byte to itself.
UPPER = bytes.maketrans(b"abcdefghijklmnopqrstuvwxyz", b"ABCDEFGHIJKLMNOPQRSTUVWXYZ")
# After whitespace removal, only printable non-space ASCII (0x21-0x7e) is valid.
VALID = bytes(range(0x21, 0x7F))


class HashFastaError(Exception):
    pass


def escape_header(header):
    return (header.replace(b"\\", b"\\\\")
            .replace(b"[", b"\\[")
            .replace(b"]", b"\\]"))


def default_output_path(input_path):
    name = input_path
    if name.endswith(".gz"):
        name = name[:-3]
    for suffix in FASTA_SUFFIXES:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    return name + OUTPUT_SUFFIX


def open_input(raw):
    """Wrap a binary stream, transparently decompressing gzip (by magic)."""
    buffered = raw if hasattr(raw, "peek") else io.BufferedReader(raw)
    if buffered.peek(2)[:2] == GZIP_MAGIC:
        # GzipFile reads concatenated members as one stream.
        return gzip.GzipFile(fileobj=buffered, mode="rb")
    return buffered


def write_record(out, header, seq):
    out.write(b">sha256_")
    out.write(hashlib.sha256(seq).hexdigest().encode("ascii"))
    out.write(b" [")
    out.write(escape_header(header))
    out.write(b"]\n")
    for i in range(0, len(seq), LINE_WIDTH):
        out.write(seq[i:i + LINE_WIDTH])
        out.write(b"\n")


def convert(inp, out):
    """Stream FASTA records from inp to out. Holds one record in memory."""
    header = None
    header_line = 0
    seq = bytearray()
    records = 0
    lineno = 0

    def finish():
        if not seq:
            raise HashFastaError(
                "record at line %d has an empty sequence" % header_line)
        write_record(out, header, bytes(seq))

    for line in inp:
        lineno += 1
        if line.startswith(b">"):
            if header is not None:
                finish()
                records += 1
            header = line[1:].rstrip(b"\n")
            if header.endswith(b"\r"):
                header = header[:-1]
            header_line = lineno
            seq = bytearray()
            continue
        chunk = line.translate(UPPER, WHITESPACE)
        if not chunk:
            continue
        if header is None:
            raise HashFastaError(
                "line %d: sequence data before the first '>' header" % lineno)
        if chunk.translate(None, VALID):
            bad = chunk.translate(None, VALID)[0]
            raise HashFastaError(
                "line %d: invalid sequence byte 0x%02x (non-ASCII or "
                "non-printable) in record starting at line %d"
                % (lineno, bad, header_line))
        seq += chunk
    if header is None:
        raise HashFastaError("input contains no FASTA records")
    finish()
    return records + 1


def same_file(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.realpath(a) == os.path.realpath(b)


def run(input_path, output_path):
    if output_path is None:
        output_path = "-" if input_path == "-" else default_output_path(input_path)
    if input_path != "-" and output_path != "-" and same_file(input_path, output_path):
        raise HashFastaError("output path %s would overwrite the input" % output_path)

    if input_path == "-":
        raw = sys.stdin.buffer
    else:
        try:
            raw = open(input_path, "rb")
        except OSError as e:
            raise HashFastaError("cannot open input %s: %s" % (input_path, e.strerror))

    tmp_path = None
    try:
        inp = open_input(raw)
        if output_path == "-":
            gz = gzip.GzipFile(fileobj=sys.stdout.buffer, mode="wb", mtime=0)
            convert(inp, gz)
            gz.close()
            sys.stdout.buffer.flush()
        else:
            # Write a temporary sibling, renamed into place only on success.
            tmp_path = "%s.tmp.%d" % (output_path, os.getpid())
            with open(tmp_path, "wb") as fh:
                gz = gzip.GzipFile(fileobj=fh, mode="wb", mtime=0,
                                   filename="")
                convert(inp, gz)
                gz.close()
            os.replace(tmp_path, output_path)
            tmp_path = None
    except (OSError, EOFError, zlib.error) as e:
        raise HashFastaError("I/O error: %s" % e)
    finally:
        if tmp_path is not None and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        if raw is not sys.stdin.buffer:
            raw.close()


USAGE = "usage: %s INPUT [-o OUTPUT]   ('-' means stdin/stdout)\n" % PROG


def parse_args(argv):
    input_path = None
    output_path = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            sys.stdout.write(USAGE)
            sys.exit(0)
        if arg == "-o":
            if i + 1 >= len(argv):
                raise SystemExit(2)
            output_path = argv[i + 1]
            i += 2
            continue
        if arg != "-" and arg.startswith("-"):
            raise SystemExit(2)
        if input_path is not None:
            raise SystemExit(2)
        input_path = arg
        i += 1
    if input_path is None:
        raise SystemExit(2)
    return input_path, output_path


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        input_path, output_path = parse_args(argv)
    except SystemExit as e:
        if e.code == 0:
            raise
        sys.stderr.write(USAGE)
        return 2
    try:
        run(input_path, output_path)
    except HashFastaError as e:
        sys.stderr.write("%s: error: %s\n" % (PROG, e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
