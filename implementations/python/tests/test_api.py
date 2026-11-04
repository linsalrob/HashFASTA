"""Unit tests for the hashfasta library API (stdlib unittest)."""

import bz2
import gzip
import io
import lzma
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import hashfasta  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tests", "data")
ACGT_KEY = "sha256_b28b7e7e6b70661dfee15d5290c4bca097ca145f721c4fbc4de73ad1d1660b8b"


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def path(self, name, data=None):
        p = os.path.join(self.tmp, name)
        if data is not None:
            with open(p, "wb") as fh:
                fh.write(data)
        return p

    def test_normalize_and_key(self):
        seq = hashfasta.normalize_sequence(b"ac gt\r\nACgt\t")
        self.assertEqual(seq, b"ACGTACGT")
        self.assertEqual(hashfasta.sequence_key(seq), ACGT_KEY)
        with self.assertRaises(hashfasta.HashFastaError):
            hashfasta.normalize_sequence(b"AC\x01GT")

    def test_escape_roundtrip(self):
        header = b"x [a] \\b\\ ]["
        self.assertEqual(hashfasta.escape_header(header), b"x \\[a\\] \\\\b\\\\ \\]\\[")
        self.assertEqual(hashfasta.unescape_header(hashfasta.escape_header(header)), header)

    def test_default_output_path(self):
        cases = {"x.fna.bz2": "x.hash.fasta.gz", "x.fa.xz": "x.hash.fasta.gz",
                 "d/x.fasta.bgz": "d/x.hash.fasta.gz", "x.txt": "x.txt.hash.fasta.gz",
                 "x.FASTA": "x.FASTA.hash.fasta.gz", "x.fasta.fa": "x.fasta.hash.fasta.gz"}
        for src, want in cases.items():
            self.assertEqual(hashfasta.default_output_path(src), want, src)

    def test_read_fasta_all_compressions(self):
        text = b">r1 one\r\nacgt\nACGT\n>r2\nTT\n"
        for name, data in (("p.fa", text), ("g.fa.gz", gzip.compress(text)),
                           ("b.fa.bz2", bz2.compress(text)), ("x.fa.xz", lzma.compress(text))):
            recs = list(hashfasta.read_fasta(self.path(name, data)))
            self.assertEqual([r.key for r in recs][0], ACGT_KEY, name)
            self.assertEqual(recs[0].header, b"r1 one", name)
            self.assertEqual(recs[1].sequence, b"TT", name)

    def test_read_fasta_file_object(self):
        recs = list(hashfasta.read_fasta(io.BytesIO(gzip.compress(b">a\nacgtacgt\n"))))
        self.assertEqual(recs[0].key, ACGT_KEY)

    def test_convert_and_read_back(self):
        src = self.path("in.fasta.bz2", bz2.compress(b">a [x] \\y\nacgtacgt\n>b\nACGTACGT\n"))
        out = hashfasta.convert(src)
        self.assertEqual(out, self.path("in.hash.fasta.gz"))
        recs = list(hashfasta.read_hashfasta(out))
        self.assertEqual([r.key for r in recs], [ACGT_KEY, ACGT_KEY])
        self.assertEqual(recs[0].header, b"a [x] \\y")
        self.assertEqual(recs[0].description, "a [x] \\y")

    def test_read_hashfasta_verifies_keys(self):
        bad = b">sha256_" + b"0" * 64 + b" [a]\nACGT\n"
        p = self.path("bad.hash.fasta.gz", gzip.compress(bad))
        with self.assertRaises(hashfasta.HashFastaError):
            list(hashfasta.read_hashfasta(p))
        self.assertEqual(len(list(hashfasta.read_hashfasta(p, verify=False))), 1)

    def test_convert_matches_golden_fixture(self):
        src = os.path.join(DATA, "duplicate-id.input.fasta.gz")
        out = hashfasta.convert(src, self.path("dup.hash.fasta.gz"))
        with gzip.open(out) as got, gzip.open(os.path.join(DATA, "duplicate-id.expected.hash.fasta.gz")) as exp:
            self.assertEqual(got.read(), exp.read())

    def test_convert_stream_plain_text(self):
        out = io.BytesIO()
        n = hashfasta.convert_stream(io.BytesIO(b">a\nACGTACGT\n"), out)
        self.assertEqual(n, 1)
        self.assertEqual(out.getvalue(), (">%s [a]\nACGTACGT\n" % ACGT_KEY).encode())

    def test_errors(self):
        for data in (b"", b">a\n", b"ACGT\n>a\nAC\n", b">a\nAC\xffGT\n", b"\x28\xb5\x2f\xfd....",
                     b"PK\x03\x04...."):
            with self.assertRaises(hashfasta.HashFastaError, msg=repr(data)):
                list(hashfasta.read_fasta(io.BytesIO(data)))
        src = self.path("same.fa", b">a\nAC\n")
        with self.assertRaises(hashfasta.HashFastaError):
            hashfasta.convert(src, src)
        with self.assertRaises(hashfasta.HashFastaError):
            hashfasta.convert(self.path("missing.fa"))
        trunc = lzma.compress(b">a\n" + b"ACGT" * 10000)
        p = self.path("t.fa.xz", trunc[: len(trunc) // 2])
        with self.assertRaises(hashfasta.HashFastaError):
            hashfasta.convert(p, self.path("t.out.gz"))
        self.assertFalse(os.path.exists(self.path("t.out.gz")))


if __name__ == "__main__":
    unittest.main()
