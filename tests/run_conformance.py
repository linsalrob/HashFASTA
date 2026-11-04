#!/usr/bin/env python3
"""Shared HashFASTA conformance runner.

Runs the same checks against every implementation:

    python3 tests/run_conformance.py                 # all implementations
    python3 tests/run_conformance.py python c        # selected ones
    python3 tests/run_conformance.py --cmd ./mytool  # any executable

Each implementation is invoked as ``CMD INPUT [-o OUTPUT]``. Output is always
checked to be gzip (magic bytes, ``gzip -t`` and a full decompression), and the
decompressed bytes are compared exactly with the expected fixture. Build the
compiled implementations first (``make -C tests build`` or see README.md).
"""

import glob
import gzip
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "tests", "data")
IMPL = os.path.join(ROOT, "implementations")

IMPLEMENTATIONS = {
    "python": [sys.executable, os.path.join(IMPL, "python", "hashfasta.py")],
    "rust": [os.path.join(IMPL, "rust", "target", "release", "hashfasta")],
    "c": [os.path.join(IMPL, "c", "hashfasta")],
    "cpp": [os.path.join(IMPL, "cpp", "hashfasta")],
    "perl": ["perl", os.path.join(IMPL, "perl", "hashfasta.pl")],
}


def sha256_file(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def fixtures():
    pairs = []
    for inp in sorted(glob.glob(os.path.join(DATA, "*.input.fasta.gz"))):
        name = os.path.basename(inp)[:-len(".input.fasta.gz")]
        exp = os.path.join(DATA, name + ".expected.hash.fasta.gz")
        if not os.path.exists(exp):
            raise SystemExit("missing expected fixture for %s" % inp)
        with gzip.open(exp, "rb") as fh:
            pairs.append((name, inp, fh.read()))
    if not pairs:
        raise SystemExit("no fixtures found in %s" % DATA)
    return pairs


def record(seq_norm, header):
    """Expected HashFASTA text for one record (used by supplementary cases)."""
    esc = header.replace(b"\\", b"\\\\").replace(b"[", b"\\[").replace(b"]", b"\\]")
    out = b">sha256_" + hashlib.sha256(seq_norm).hexdigest().encode() + b" [" + esc + b"]\n"
    for i in range(0, len(seq_norm), 80):
        out += seq_norm[i:i + 80] + b"\n"
    return out


LONG = (b"ACGTN" * 50)  # 250 residues, wraps to 80/80/80/10
SUPPLEMENTARY = [
    # name, input bytes, expected decompressed output
    ("lowercase-whitespace-crlf",
     b">id1 desc\r\nac gt\tac\r\n\r\nGT\x0b\x0c\r\n>id2\r\nnnnn\r\n",
     record(b"ACGTACGT", b"id1 desc") + record(b"NNNN", b"id2")),
    ("header-escaping",
     b">x [a] \\b\\ [[c]]\nMKV*\n",
     record(b"MKV*", b"x [a] \\b\\ [[c]]")),
    ("empty-header",
     b">\nACGT\n",
     record(b"ACGT", b"")),
    ("symbols-retained",
     b">g\nac-gu.T*\nxbzj\n",
     record(b"AC-GU.T*XBZJ", b"g")),
    ("long-single-line-wrap",
     b">long\n" + LONG.lower() + b"\n",
     record(LONG, b"long")),
    ("exact-80-no-trailing-newline",
     b">e\n" + b"A" * 80,
     record(b"A" * 80, b"e")),
    ("leading-blank-lines",
     b"\n  \n>a\nAC\n\n>b\nGT\n",
     record(b"AC", b"a") + record(b"GT", b"b")),
    ("header-non-ascii-passthrough",
     b">caf\xc3\xa9 \xff\tx\nAC\n",
     record(b"AC", b"caf\xc3\xa9 \xff\tx")),
]

ERRORS = [
    ("empty-sequence-middle", b">a\n>b\nACGT\n"),
    ("empty-sequence-last", b">a\nACGT\n>b\n"),
    ("whitespace-only-sequence", b">a\n  \n\t\n"),
    ("data-before-header", b"ACGT\n>a\nACGT\n"),
    ("non-ascii-sequence", b">a\nAC\xc3\x84GT\n"),
    ("control-byte-sequence", b">a\nAC\x01GT\n"),
    ("nul-byte-sequence", b">a\nAC\x00GT\n"),
    ("del-byte-sequence", b">a\nAC\x7fGT\n"),
    ("empty-input", b""),
]


class Runner(object):
    def __init__(self, name, cmd):
        self.name = name
        self.cmd = cmd
        self.passed = 0
        self.failed = []
        self.tmp = tempfile.mkdtemp(prefix="hashfasta-%s-" % name)

    def run(self, args, stdin=None):
        return subprocess.run(self.cmd + args, input=stdin,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def check(self, label, ok, detail=""):
        if ok:
            self.passed += 1
        else:
            self.failed.append("%s %s" % (label, detail))
            print("  FAIL %s %s" % (label, detail))

    def path(self, *parts):
        return os.path.join(self.tmp, *parts)

    def check_gzip_output(self, label, data, expected):
        """data: raw output bytes. Must be valid gzip whose content == expected."""
        if data[:2] != b"\x1f\x8b":
            self.check(label, False, "output is not gzip (magic %r)" % data[:2])
            return
        p = self.path("gztest.gz")
        with open(p, "wb") as fh:
            fh.write(data)
        t = subprocess.run(["gzip", "-t", p], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.check(label + " [gzip -t]", t.returncode == 0, t.stderr.decode(errors="replace"))
        try:
            got = gzip.decompress(data)
        except Exception as e:  # noqa: BLE001 - report any reader failure
            self.check(label, False, "gzip decompression failed: %s" % e)
            return
        if got == expected:
            self.check(label + " [content]", True)
        else:
            n = next((i for i in range(min(len(got), len(expected)))
                      if got[i] != expected[i]), min(len(got), len(expected)))
            self.check(label + " [content]", False,
                       "differs at byte %d (got %d bytes, expected %d)" % (n, len(got), len(expected)))

    def convert_file(self, label, src, expected, out=None):
        """Run SRC -o OUT (or default naming when out is None) and check it."""
        before = sha256_file(src)
        args = [src] if out is None else [src, "-o", out]
        r = self.run(args)
        self.check(label + " [exit 0]", r.returncode == 0, r.stderr.decode(errors="replace"))
        self.check(label + " [input unchanged]", sha256_file(src) == before)
        target = out
        if target is None:
            return r
        if not os.path.exists(target):
            self.check(label, False, "no output written at %s" % target)
            return r
        with open(target, "rb") as fh:
            self.check_gzip_output(label, fh.read(), expected)
        leftovers = [f for f in os.listdir(os.path.dirname(target)) if ".tmp" in f]
        self.check(label + " [no temp files]", not leftovers, str(leftovers))
        return r

    def fixture_tests(self):
        for name, gz_in, expected in fixtures():
            plain = self.path(name + ".plain.fasta")
            with gzip.open(gz_in, "rb") as src, open(plain, "wb") as dst:
                shutil.copyfileobj(src, dst)
            with open(gz_in, "rb") as fh:
                gz_bytes = fh.read()

            self.convert_file("%s gz-input -o" % name, gz_in, expected,
                              self.path(name + ".gz-in.hash.fasta.gz"))
            self.convert_file("%s plain-input -o" % name, plain, expected,
                              self.path(name + ".plain-in.hash.fasta.gz"))

            r = self.run(["-"], stdin=gz_bytes)
            self.check("%s stdin gz -> stdout [exit 0]" % name, r.returncode == 0,
                       r.stderr.decode(errors="replace"))
            self.check_gzip_output("%s stdin gz -> stdout" % name, r.stdout, expected)

            with open(plain, "rb") as fh:
                r = self.run(["-", "-o", "-"], stdin=fh.read())
            self.check("%s stdin plain -> -o - [exit 0]" % name, r.returncode == 0,
                       r.stderr.decode(errors="replace"))
            self.check_gzip_output("%s stdin plain -> -o -" % name, r.stdout, expected)

            # Concatenated gzip members (split the plain text into two members).
            with open(plain, "rb") as fh:
                text = fh.read()
            half = len(text) // 2
            multi = self.path(name + ".multi.fasta.gz")
            with open(multi, "wb") as fh:
                fh.write(gzip.compress(text[:half]) + gzip.compress(text[half:]))
            self.convert_file("%s multi-member gzip" % name, multi, expected,
                              self.path(name + ".multi.hash.fasta.gz"))

            # Default output naming on a named input (input is gz, wrong-ish suffix).
            d = self.path("default-" + name)
            os.mkdir(d)
            src = os.path.join(d, "sample.fasta.gz")
            shutil.copy(gz_in, src)
            self.convert_file("%s default -o" % name, src, expected)
            out = os.path.join(d, "sample.hash.fasta.gz")
            if os.path.exists(out):
                with open(out, "rb") as fh:
                    self.check_gzip_output("%s default -o" % name, fh.read(), expected)
            else:
                self.check("%s default -o" % name, False, "missing %s" % out)

    def supplementary_tests(self):
        for name, data, expected in SUPPLEMENTARY:
            src = self.path(name + ".fa")
            with open(src, "wb") as fh:
                fh.write(data)
            self.convert_file("supp %s plain" % name, src, expected,
                              self.path(name + ".plain.hash.fasta.gz"))
            src_gz = self.path(name + ".fa.gz")
            with open(src_gz, "wb") as fh:
                fh.write(gzip.compress(data))
            self.convert_file("supp %s gz" % name, src_gz, expected,
                              self.path(name + ".gz.hash.fasta.gz"))

    def naming_tests(self):
        d = self.path("naming")
        os.mkdir(d)
        suffixes = [s + gz for s in (".fa", ".fas", ".fasta", ".fna", ".ffn",
                                     ".faa", ".frn", ".fsa") for gz in ("", ".gz")]
        suffixes += [".txt", ".seq.gz", "", ".fasta.fa"]
        payload = b">n\nACGT\n"
        expected = record(b"ACGT", b"n")
        for suffix in suffixes:
            src = os.path.join(d, "x" + suffix)
            with open(src, "wb") as fh:
                fh.write(gzip.compress(payload) if suffix.endswith(".gz") else payload)
            want = os.path.join(d, _strip_for_default("x" + suffix) + ".hash.fasta.gz")
            r = self.run([src])
            ok = r.returncode == 0 and os.path.exists(want)
            self.check("naming %r -> %s" % ("x" + suffix, os.path.basename(want)), ok,
                       r.stderr.decode(errors="replace"))
            if ok:
                with open(want, "rb") as fh:
                    self.check_gzip_output("naming %r" % suffix, fh.read(), expected)
                os.unlink(want)
            os.unlink(src)

    def error_tests(self):
        for name, data in ERRORS:
            for gz in (False, True):
                src = self.path("err-%s%s" % (name, ".fa.gz" if gz else ".fa"))
                with open(src, "wb") as fh:
                    fh.write(gzip.compress(data) if gz else data)
                out = self.path("err-%s-%d.hash.fasta.gz" % (name, gz))
                r = self.run([src, "-o", out])
                label = "error %s%s" % (name, " (gz)" if gz else "")
                self.check(label + " [non-zero exit]", r.returncode != 0)
                self.check(label + " [stderr message]", len(r.stderr.strip()) > 0)
                self.check(label + " [no output file]", not os.path.exists(out))
            r = self.run(["-"], stdin=data)
            self.check("error %s stdin [non-zero exit]" % name, r.returncode != 0)

        # Output path equal to the input must be refused, and the input kept.
        src = self.path("same.fasta")
        with open(src, "wb") as fh:
            fh.write(b">a\nACGT\n")
        before = sha256_file(src)
        r = self.run([src, "-o", src])
        self.check("error output==input [non-zero exit]", r.returncode != 0)
        self.check("error output==input [input unchanged]", sha256_file(src) == before)
        # Same file via a different spelling.
        r = self.run([src, "-o", os.path.join(self.tmp, ".", "same.fasta")])
        self.check("error output==input (alias) [non-zero exit]", r.returncode != 0)
        self.check("error output==input (alias) [input unchanged]", sha256_file(src) == before)

        # Missing input file.
        r = self.run([self.path("missing.fasta"), "-o", self.path("missing.out.gz")])
        self.check("error missing input [non-zero exit]", r.returncode != 0)
        self.check("error missing input [no output]", not os.path.exists(self.path("missing.out.gz")))

        # Truncated gzip input.
        good = gzip.compress(b">a\n" + b"ACGT" * 5000 + b"\n")
        bad = self.path("truncated.fa.gz")
        with open(bad, "wb") as fh:
            fh.write(good[: len(good) // 2])
        out = self.path("truncated.hash.fasta.gz")
        r = self.run([bad, "-o", out])
        self.check("error truncated gzip [non-zero exit]", r.returncode != 0)
        self.check("error truncated gzip [no output file]", not os.path.exists(out))

        # Output to an unwritable location.
        r = self.run([src, "-o", self.path("no-such-dir", "out.hash.fasta.gz")])
        self.check("error unwritable output [non-zero exit]", r.returncode != 0)

        # Usage errors.
        r = self.run([])
        self.check("error no arguments [non-zero exit]", r.returncode != 0)

    def run_all(self):
        print("== %s: %s" % (self.name, " ".join(self.cmd)))
        try:
            self.fixture_tests()
            self.supplementary_tests()
            self.naming_tests()
            self.error_tests()
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)
        status = "PASS" if not self.failed else "FAIL"
        print("== %s: %s (%d checks passed, %d failed)"
              % (self.name, status, self.passed, len(self.failed)))
        return not self.failed


def _strip_for_default(name):
    """Spec section 5 default-name stem (independent of any implementation)."""
    if name.endswith(".gz"):
        name = name[:-3]
    for s in (".fa", ".fas", ".fasta", ".fna", ".ffn", ".faa", ".frn", ".fsa"):
        if name.endswith(s):
            return name[:-len(s)]
    return name


def random_fasta(seed, records=300):
    """Seeded FASTA exercising wrapping, case, CRLF, blank lines and odd headers."""
    import random
    rng = random.Random(seed)
    alphabet = b"ACGTNacgtnRYKMSWBDHVrykmswbdhvU-.*EFILPQXZ"
    head_chars = [bytes([c]) for c in range(0x20, 0x7F)] + [b"\t", b"\xc3\xa9"]
    out = bytearray()
    for i in range(records):
        eol = b"\r\n" if rng.random() < 0.2 else b"\n"
        header = b"".join(rng.choice(head_chars) for _ in range(rng.randint(0, 40)))
        out += b">" + header + eol
        if rng.random() < 0.05 and i:
            # Repeat a previous sequence verbatim under a new header.
            seq = last
        else:
            seq = bytes(rng.choice(alphabet) for _ in range(rng.randint(1, 700)))
        last = seq
        width = rng.choice([1, 7, 60, 79, 80, 81, 1000])
        for j in range(0, len(seq), width):
            out += seq[j:j + width] + (b" \t" if rng.random() < 0.05 else b"") + eol
            if rng.random() < 0.03:
                out += eol
    return bytes(out)


def cross_check(targets):
    """All implementations must emit identical decompressed bytes."""
    tmp = tempfile.mkdtemp(prefix="hashfasta-cross-")
    ok = True
    try:
        for seed in (1, 2, 3):
            data = random_fasta(seed)
            src = os.path.join(tmp, "random-%d.fa.gz" % seed)
            with open(src, "wb") as fh:
                fh.write(gzip.compress(data))
            outputs = {}
            for name, cmd in targets:
                out = os.path.join(tmp, "%s-%d.hash.fasta.gz" % (name, seed))
                r = subprocess.run(cmd + [src, "-o", out], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
                outputs[name] = (gzip.open(out).read() if r.returncode == 0
                                 else "exit %d: %s" % (r.returncode, r.stderr.decode(errors="replace")))
            distinct = set(v if isinstance(v, bytes) else v.encode() for v in outputs.values())
            agree = len(distinct) == 1 and isinstance(next(iter(outputs.values())), bytes)
            print("cross-check seed %d (%d bytes in): %s" % (
                seed, len(data), "identical across " + ", ".join(outputs) if agree else "MISMATCH"))
            if not agree:
                ok = False
                for name, v in outputs.items():
                    desc = (hashlib.sha256(v).hexdigest()[:16] if isinstance(v, bytes) else v)
                    print("  %-8s %s" % (name, desc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return ok


def main(argv):
    if argv[:1] == ["--cmd"]:
        targets = [("custom", argv[1:])]
    else:
        names = argv or list(IMPLEMENTATIONS)
        unknown = [n for n in names if n not in IMPLEMENTATIONS]
        if unknown:
            raise SystemExit("unknown implementation(s): %s (choose from %s)"
                             % (", ".join(unknown), ", ".join(IMPLEMENTATIONS)))
        targets = [(n, IMPLEMENTATIONS[n]) for n in names]
    results = {}
    for name, cmd in targets:
        exe = cmd[0] if len(cmd) == 1 else cmd[-1]
        if not os.path.exists(exe) and shutil.which(cmd[0]) is None:
            print("== %s: MISSING (%s not found; build it first)" % (name, exe))
            results[name] = False
            continue
        results[name] = Runner(name, cmd).run_all()
    available = [(n, c) for n, c in targets if results[n]]
    if len(available) > 1:
        print()
        results["cross"] = cross_check(available)
    print()
    for name, ok in results.items():
        print("%-8s %s" % (name, "PASS" if ok else "FAIL"))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
