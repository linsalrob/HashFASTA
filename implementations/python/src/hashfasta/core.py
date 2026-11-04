"""HashFASTA v0.1 reference implementation (library core).

Converts plain, gzip, bzip2 or xz FASTA into gzip-compressed HashFASTA whose
record IDs are SHA-256 digests of the normalized sequence, and reads
HashFASTA back. See spec/HASHFASTA-v0.1.md. Standard library only.
"""

import bz2
import collections
import gzip
import hashlib
import io
import lzma
import os
import re
import sys
import zlib

LINE_WIDTH = 80
COMPRESSION_SUFFIXES = (".gz", ".bgz", ".bz2", ".xz")
FASTA_SUFFIXES = (".fa", ".fas", ".fasta", ".fna", ".ffn", ".faa", ".frn", ".fsa")
OUTPUT_SUFFIX = ".hash.fasta.gz"

GZIP_MAGIC = b"\x1f\x8b"
BZIP2_MAGIC = b"BZh"
XZ_MAGIC = b"\xfd7zXZ\x00"
UNSUPPORTED_MAGIC = ((b"\x28\xb5\x2f\xfd", "zstd"), (b"PK\x03\x04", "ZIP"))

# Whitespace removed before hashing: space, HT, LF, VT, FF, CR.
WHITESPACE = b" \t\n\x0b\x0c\r"
UPPER = bytes.maketrans(b"abcdefghijklmnopqrstuvwxyz", b"ABCDEFGHIJKLMNOPQRSTUVWXYZ")
# After whitespace removal, only printable non-space ASCII (0x21-0x7e) is valid.
VALID = bytes(range(0x21, 0x7F))

_DECODE_ERRORS = (OSError, EOFError, zlib.error, lzma.LZMAError)


class HashFastaError(Exception):
    """Invalid input, or a failure reading or writing a file."""


class Record(collections.namedtuple("Record", "key header sequence")):
    """One HashFASTA record.

    key      -- ``sha256_<64 hex>`` identity (str)
    header   -- original definition line without '>' (bytes, CR removed, unescaped)
    sequence -- normalized sequence (bytes, uppercase, no whitespace)
    """

    __slots__ = ()

    @property
    def description(self):
        """The original definition line decoded as UTF-8 (undecodable bytes replaced)."""
        return self.header.decode("utf-8", "replace")


def escape_header(header):
    """Escape ``\\``, ``[`` and ``]`` for the bracketed provenance field."""
    return (header.replace(b"\\", b"\\\\")
            .replace(b"[", b"\\[")
            .replace(b"]", b"\\]"))


_UNESCAPE = re.compile(rb"\\([\\\[\]])")


def unescape_header(escaped):
    """Reverse :func:`escape_header`."""
    return _UNESCAPE.sub(rb"\1", escaped)


def normalize_sequence(data):
    """Remove ASCII whitespace and uppercase a-z; raise on invalid bytes."""
    seq = data.translate(UPPER, WHITESPACE)
    bad = seq.translate(None, VALID)
    if bad:
        raise HashFastaError("invalid sequence byte 0x%02x (non-ASCII or non-printable)" % bad[0])
    return seq


def sequence_key(normalized):
    """``sha256_<hex>`` key for an already-normalized sequence."""
    return "sha256_" + hashlib.sha256(normalized).hexdigest()


def format_record(record):
    """HashFASTA text (bytes) for one :class:`Record`, sequence wrapped at 80."""
    seq = record.sequence
    return (b">" + record.key.encode("ascii") + b" [" + escape_header(record.header) + b"]\n"
            + b"".join(seq[i:i + LINE_WIDTH] + b"\n" for i in range(0, len(seq), LINE_WIDTH)))


def default_output_path(input_path):
    """Default sibling output name for a named input (spec section 5)."""
    name = input_path
    for suffix in COMPRESSION_SUFFIXES:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    for suffix in FASTA_SUFFIXES:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    return name + OUTPUT_SUFFIX


class _Prefixed(io.RawIOBase):
    """Raw stream that replays already-read leading bytes before the rest."""

    def __init__(self, prefix, raw):
        self._prefix = prefix
        self._raw = raw

    def readable(self):
        return True

    def readinto(self, b):
        if self._prefix:
            n = min(len(b), len(self._prefix))
            b[:n] = self._prefix[:n]
            self._prefix = self._prefix[n:]
            return n
        data = self._raw.read(len(b))
        b[:len(data)] = data
        return len(data)


def open_input(raw):
    """Wrap a binary stream, decompressing gzip, bzip2 or xz detected by magic bytes."""
    head = b""
    while len(head) < 6:
        chunk = raw.read(6 - len(head))
        if not chunk:
            break
        head += chunk
    for magic, name in UNSUPPORTED_MAGIC:
        if head.startswith(magic):
            raise HashFastaError("%s-compressed input is not supported; decompress it first" % name)
    stream = io.BufferedReader(_Prefixed(head, raw), 1 << 16)
    # All three decoders read concatenated members/streams as one input.
    if head.startswith(GZIP_MAGIC):
        return gzip.GzipFile(fileobj=stream, mode="rb")
    if head.startswith(BZIP2_MAGIC):
        return bz2.BZ2File(stream, mode="rb")
    if head.startswith(XZ_MAGIC):
        return lzma.LZMAFile(stream, mode="rb")
    return stream


def _parse(inp):
    """Yield (header, header_line, normalized sequence) for each FASTA record."""
    header = None
    header_line = 0
    seq = bytearray()
    lineno = 0
    try:
        for line in inp:
            lineno += 1
            if line.startswith(b">"):
                if header is not None:
                    yield header, header_line, bytes(seq)
                header = line[1:].replace(b"\r", b"").rstrip(b"\n")
                header_line = lineno
                seq = bytearray()
                continue
            chunk = line.translate(UPPER, WHITESPACE)
            if not chunk:
                continue
            if header is None:
                raise HashFastaError(
                    "line %d: sequence data before the first '>' header" % lineno)
            bad = chunk.translate(None, VALID)
            if bad:
                raise HashFastaError(
                    "line %d: invalid sequence byte 0x%02x (non-ASCII or "
                    "non-printable) in record starting at line %d"
                    % (lineno, bad[0], header_line))
            seq += chunk
    except _DECODE_ERRORS as e:
        raise HashFastaError("cannot read input: %s" % e)
    if header is None:
        raise HashFastaError("input contains no FASTA records")
    yield header, header_line, bytes(seq)


def iter_records(inp):
    """Yield a :class:`Record` per FASTA record of a decompressed binary stream.

    Holds one record in memory. Raises :class:`HashFastaError` on invalid input.
    """
    for header, header_line, seq in _parse(inp):
        if not seq:
            raise HashFastaError("record at line %d has an empty sequence" % header_line)
        yield Record(sequence_key(seq), header, seq)


def _open_source(source):
    """Return (binary stream, should_close) for a path, '-', or binary file object."""
    if source == "-":
        return sys.stdin.buffer, False
    if isinstance(source, (str, bytes, os.PathLike)):
        try:
            return open(source, "rb"), True
        except OSError as e:
            raise HashFastaError("cannot open input %s: %s" % (os.fsdecode(source), e.strerror))
    return source, False


def read_fasta(source):
    """Yield :class:`Record` objects from FASTA at a path, '-' or binary file object.

    Plain, gzip, bzip2 and xz input are all accepted.
    """
    raw, close = _open_source(source)
    try:
        for rec in iter_records(open_input(raw)):
            yield rec
    finally:
        if close:
            raw.close()


_HASH_HEADER = re.compile(rb"^>(sha256_[0-9a-f]{64}) \[(.*)\]$", re.S)


def read_hashfasta(source, verify=True):
    """Yield :class:`Record` objects from an existing HashFASTA file.

    The header is unescaped back to the original definition line. With
    ``verify`` (the default) each key is recomputed from the sequence and a
    mismatch raises :class:`HashFastaError`.
    """
    raw, close = _open_source(source)
    try:
        key = header = None
        seq = bytearray()

        def finish():
            rec = Record(key, unescape_header(header), bytes(seq))
            if verify and sequence_key(rec.sequence) != key:
                raise HashFastaError("key %s does not match its sequence" % key)
            return rec

        try:
            for line in open_input(raw):
                line = line.rstrip(b"\n")
                if line.startswith(b">"):
                    if key is not None:
                        yield finish()
                    m = _HASH_HEADER.match(line)
                    if not m:
                        raise HashFastaError("not a HashFASTA header: %r" % line[:100])
                    key, header = m.group(1).decode("ascii"), m.group(2)
                    seq = bytearray()
                elif key is None:
                    raise HashFastaError("sequence data before the first header")
                else:
                    seq += line
        except _DECODE_ERRORS as e:
            raise HashFastaError("cannot read input: %s" % e)
        if key is not None:
            yield finish()
    finally:
        if close:
            raw.close()


def convert_stream(inp, out):
    """Convert decompressed FASTA ``inp`` to HashFASTA text on binary ``out``.

    ``out`` receives uncompressed text; wrap it in a gzip writer for a
    conforming file (see :func:`convert`). Returns the number of records.
    """
    n = 0
    for rec in iter_records(inp):
        out.write(format_record(rec))
        n += 1
    return n


def _gzip_writer(fileobj):
    return gzip.GzipFile(fileobj=fileobj, mode="wb", mtime=0, filename="", compresslevel=6)


def _same_file(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def convert(input_path, output_path=None):
    """Convert ``input_path`` to gzip HashFASTA; return the output path.

    ``'-'`` means stdin / stdout. Without ``output_path`` a named input is
    written beside itself as ``<stem>.hash.fasta.gz`` and stdin goes to
    stdout. A file output is written to a temporary sibling and renamed into
    place only on success. The input is never overwritten.
    """
    if output_path is None:
        output_path = "-" if input_path == "-" else default_output_path(os.fspath(input_path))
    if input_path != "-" and output_path != "-" and _same_file(input_path, output_path):
        raise HashFastaError("output path %s would overwrite the input" % output_path)

    raw, close = _open_source(input_path)
    tmp_path = None
    try:
        inp = open_input(raw)
        if output_path == "-":
            gz = _gzip_writer(sys.stdout.buffer)
            convert_stream(inp, gz)
            gz.close()
            sys.stdout.buffer.flush()
        else:
            tmp_path = "%s.tmp.%d" % (output_path, os.getpid())
            with open(tmp_path, "wb") as fh:
                gz = _gzip_writer(fh)
                convert_stream(inp, gz)
                gz.close()
            os.replace(tmp_path, output_path)
            tmp_path = None
    except OSError as e:
        raise HashFastaError("I/O error: %s" % e)
    finally:
        if tmp_path is not None and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        if close:
            raw.close()
    return output_path
