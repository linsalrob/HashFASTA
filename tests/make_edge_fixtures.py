#!/usr/bin/env python3
"""Generate the edge-case and invalid-input conformance fixtures in tests/data/.

Expected outputs are built here directly from the specification's rules
(section 2 header, section 3 normalization) rather than by running any
implementation, so they are independent golden vectors. Re-running this
script reproduces the committed files byte-for-byte (gzip mtime is 0).

    python3 tests/make_edge_fixtures.py           # (re)write the fixtures
    python3 tests/make_edge_fixtures.py --check   # verify committed fixtures' content
"""

import gzip
import hashlib
import io
import os
import sys

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
INVALID = os.path.join(DATA, "invalid")


def record(seq_norm, header):
    """One HashFASTA record from an already-normalized sequence and raw header."""
    header = header.replace(b"\r", b"")
    esc = header.replace(b"\\", b"\\\\").replace(b"[", b"\\[").replace(b"]", b"\\]")
    out = b">sha256_" + hashlib.sha256(seq_norm).hexdigest().encode() + b" [" + esc + b"]\n"
    for i in range(0, len(seq_norm), 80):
        out += seq_norm[i:i + 80] + b"\n"
    return out


LONG = b"ACGTN" * 50  # 250 residues
SEQ_79, SEQ_80, SEQ_81 = b"A" * 79, b"C" * 80, b"G" * 81

# name: (input bytes, expected decompressed output)
VALID = {
    "edge-lowercase-whitespace": (
        b">id1 mixed case\nac gt\tac\n\nGT\x0b\x0c\n>id2\nnnnn\n",
        record(b"ACGTACGT", b"id1 mixed case") + record(b"NNNN", b"id2")),
    "edge-crlf-line-endings": (
        b">crlf one\r\nACGT\r\nacgt\r\n\r\n>crlf two\r\r\nTT\r\r\n>stray\rcr in header\r\nGG\rCC\r\n",
        record(b"ACGTACGT", b"crlf one") + record(b"TT", b"crlf two")
        + record(b"GGCC", b"straycr in header")),
    "edge-header-escaping": (
        b">x [a] \\b\\ [[c]] \\[already\\]\nMKV*\n",
        record(b"MKV*", b"x [a] \\b\\ [[c]] \\[already\\]")),
    "edge-header-special": (
        b">\nACGT\n>a>b >c\tTAB\nGG\n>caf\xc3\xa9 \xff end\nAC\n",
        record(b"ACGT", b"") + record(b"GG", b"a>b >c\tTAB")
        + record(b"AC", b"caf\xc3\xa9 \xff end")),
    "edge-symbols-retained": (
        b">nt\nac-gu.T*\nRYKMSWBDHVN\n>aa\nmkvxbzjou*\n",
        record(b"AC-GU.T*RYKMSWBDHVN", b"nt") + record(b"MKVXBZJOU*", b"aa")),
    "edge-wrap-boundaries": (
        b">w79\n" + SEQ_79 + b"\n>w80\n" + SEQ_80 + b"\n>w81\n" + SEQ_81
        + b"\n>w250 one line\n" + LONG.lower() + b"\n>w1\nT\n",
        record(SEQ_79, b"w79") + record(SEQ_80, b"w80") + record(SEQ_81, b"w81")
        + record(LONG, b"w250 one line") + record(b"T", b"w1")),
    "edge-rewrapped-equivalent": (
        b">a original\nACGTACGTAC\n>b lower\nacgtacgtac\n>c wrapped\nACG\nTAC\nGTA\nC\n"
        b">d spaced\nA C G T A C G T A C\n>e different\nACGTACGTAG\n",
        record(b"ACGTACGTAC", b"a original") + record(b"ACGTACGTAC", b"b lower")
        + record(b"ACGTACGTAC", b"c wrapped") + record(b"ACGTACGTAC", b"d spaced")
        + record(b"ACGTACGTAG", b"e different")),
    "edge-comment-lines": (
        b";file comment before the first header\n;\n>a\n;comment after header\nAC\n"
        b";comment mid-sequence; with [brackets]\r\nGT\n;\n>b\nTT\n;trailing comment no newline",
        record(b"ACGT", b"a") + record(b"TT", b"b")),
    "edge-blank-lines-no-final-newline": (
        b"\n  \n\t\n>a\n\nAC\n\n\n>b\nGT",
        record(b"AC", b"a") + record(b"GT", b"b")),
}

# name: input bytes; every implementation must reject these.
INVALID_CASES = {
    "empty-sequence-middle": b">a\n>b\nACGT\n",
    "empty-sequence-last": b">a\nACGT\n>b\n",
    "whitespace-only-sequence": b">a\n  \n\t\r\n>b\nAC\n",
    "data-before-header": b"ACGT\n>a\nACGT\n",
    "non-ascii-sequence": b">a\nAC\xc3\x84GT\n",
    "control-byte-sequence": b">a\nAC\x01GT\n",
    "nul-byte-sequence": b">a\nAC\x00GT\n",
    "del-byte-sequence": b">a\nAC\x7fGT\n",
    "semicolon-in-sequence": b">a\nAC;GT\n",
    "indented-semicolon-line": b">a\nACGT\n  ;not a comment\n",
    "comment-only-sequence": b">a\n;just a comment\n>b\nAC\n",
    "comments-only": b";one\n;two\n",
    "empty-input": b"",
    "blank-lines-only": b"\n\r\n  \n",
}


def gzip_bytes(data):
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, filename="") as gz:
        gz.write(data)
    return buf.getvalue()


def write(path, data):
    with open(path, "wb") as fh:
        fh.write(gzip_bytes(data))


def check():
    """Compare committed fixtures' decompressed content with the generated cases."""
    expected = {}
    for name, (inp, exp) in VALID.items():
        expected[os.path.join(DATA, name + ".input.fasta.gz")] = inp
        expected[os.path.join(DATA, name + ".expected.hash.fasta.gz")] = exp
    for name, inp in INVALID_CASES.items():
        expected[os.path.join(INVALID, name + ".input.fasta.gz")] = inp
    bad = 0
    for path, data in sorted(expected.items()):
        try:
            with gzip.open(path, "rb") as fh:
                ok = fh.read() == data
        except OSError:
            ok = False
        if not ok:
            bad += 1
            print("out of date or missing: %s" % os.path.relpath(path))
    print("%d fixtures checked, %d out of date" % (len(expected), bad))
    return 1 if bad else 0


def main():
    if sys.argv[1:] == ["--check"]:
        return check()
    os.makedirs(INVALID, exist_ok=True)
    for name, (inp, expected) in sorted(VALID.items()):
        write(os.path.join(DATA, name + ".input.fasta.gz"), inp)
        write(os.path.join(DATA, name + ".expected.hash.fasta.gz"), expected)
    for name, inp in sorted(INVALID_CASES.items()):
        write(os.path.join(INVALID, name + ".input.fasta.gz"), inp)
    print("wrote %d edge fixtures and %d invalid inputs" % (len(VALID), len(INVALID_CASES)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
