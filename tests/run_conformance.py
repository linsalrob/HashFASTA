#!/usr/bin/env python3
"""Shared HashFASTA conformance runner.

Runs the same checks against every implementation:

    python3 tests/run_conformance.py                 # all implementations
    python3 tests/run_conformance.py python c        # selected ones
    python3 tests/run_conformance.py --cmd ./mytool  # any executable

Each implementation is invoked as ``CMD INPUT [-o OUTPUT]``. Output is always
checked to be gzip (magic bytes, ``gzip -t`` and a full decompression), and the
decompressed bytes are compared exactly with the expected fixture. Build the
compiled implementations first (``make build`` or see README.md).
"""

import bz2
import glob
import gzip
import hashlib
import lzma
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "tests", "data")
INVALID = os.path.join(DATA, "invalid")
IMPL = os.path.join(ROOT, "implementations")

IMPLEMENTATIONS = {
    "python": [sys.executable, os.path.join(IMPL, "python", "run_hashfasta.py")],
    "rust": [os.path.join(IMPL, "rust", "target", "release", "hashfasta")],
    "c": [os.path.join(IMPL, "c", "hashfasta")],
    "cpp": [os.path.join(IMPL, "cpp", "hashfasta")],
    "perl": ["perl", os.path.join(IMPL, "perl", "hashfasta.pl")],
}

COMPRESSION_SUFFIXES = (".gz", ".bgz", ".bz2", ".xz")
FASTA_SUFFIXES = (".fa", ".fas", ".fasta", ".fna", ".ffn", ".faa", ".frn", ".fsa")


def sha256_file(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def gunzip_file(path):
    with gzip.open(path, "rb") as fh:
        return fh.read()


def fixtures():
    pairs = []
    for inp in sorted(glob.glob(os.path.join(DATA, "*.input.fasta.gz"))):
        name = os.path.basename(inp)[:-len(".input.fasta.gz")]
        exp = os.path.join(DATA, name + ".expected.hash.fasta.gz")
        if not os.path.exists(exp):
            raise SystemExit("missing expected fixture for %s" % inp)
        pairs.append((name, inp, gunzip_file(exp)))
    if not pairs:
        raise SystemExit("no fixtures found in %s" % DATA)
    return pairs


def invalid_fixtures():
    found = [(os.path.basename(p)[:-len(".input.fasta.gz")], gunzip_file(p))
             for p in sorted(glob.glob(os.path.join(INVALID, "*.input.fasta.gz")))]
    if not found:
        raise SystemExit("no invalid fixtures found in %s" % INVALID)
    return found


def hash_record(header, seq):
    """HashFASTA text for a short record (no escaping or wrapping needed)."""
    return b">sha256_" + hashlib.sha256(seq).hexdigest().encode() + b" [" + header + b"]\n" + seq + b"\n"


def default_name(name):
    """Spec section 5 default output name (independent of any implementation)."""
    for s in COMPRESSION_SUFFIXES:
        if name.endswith(s):
            name = name[:-len(s)]
            break
    for s in FASTA_SUFFIXES:
        if name.endswith(s):
            name = name[:-len(s)]
            break
    return name + ".hash.fasta.gz"


def split_compress(text, compress):
    """Two concatenated members/streams of the given compressor."""
    half = len(text) // 2
    return compress(text[:half]) + compress(text[half:])


def variants(text, gz_bytes):
    """Input variants of one fixture: (label, filename suffix, bytes)."""
    return [
        ("gz", ".fasta.gz", gz_bytes),
        ("plain", ".fasta", text),
        ("bz2", ".fasta.bz2", bz2.compress(text)),
        ("xz", ".fasta.xz", lzma.compress(text)),
        ("multi-gz", ".fasta.gz", split_compress(text, gzip.compress)),
        ("multi-bz2", ".fasta.bz2", split_compress(text, bz2.compress)),
        ("multi-xz", ".fasta.xz", split_compress(text, lzma.compress)),
        ("plain-named-gz", ".fasta.gz", text),   # detection must use content, not name
        ("bz2-named-fa", ".fa", bz2.compress(text)),
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

    def write(self, name, data):
        p = self.path(name)
        with open(p, "wb") as fh:
            fh.write(data)
        return p

    def check_gzip_output(self, label, data, expected):
        """data: raw output bytes. Must be valid gzip whose content == expected."""
        if data[:2] != b"\x1f\x8b":
            self.check(label, False, "output is not gzip (magic %r)" % data[:2])
            return
        p = self.write("gztest.gz", data)
        t = subprocess.run(["gzip", "-t", p], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.check(label + " [gzip -t]", t.returncode == 0, t.stderr.decode(errors="replace"))
        try:
            got = gzip.decompress(data)
        except Exception as e:  # noqa: BLE001 - report any reader failure
            self.check(label, False, "gzip decompression failed: %s" % e)
            return
        self.check(label + " [no CR in output]", b"\r" not in got)
        if got == expected:
            self.check(label + " [content]", True)
        else:
            n = next((i for i in range(min(len(got), len(expected)))
                      if got[i] != expected[i]), min(len(got), len(expected)))
            self.check(label + " [content]", False,
                       "differs at byte %d (got %d bytes, expected %d): got %r expected %r"
                       % (n, len(got), len(expected), got[max(0, n - 20):n + 20],
                          expected[max(0, n - 20):n + 20]))

    def convert_file(self, label, src, expected, out=None):
        """Run SRC -o OUT (or default naming when out is None) and check it."""
        before = sha256_file(src)
        r = self.run([src] if out is None else [src, "-o", out])
        self.check(label + " [exit 0]", r.returncode == 0, r.stderr.decode(errors="replace"))
        self.check(label + " [input unchanged]", sha256_file(src) == before)
        target = default_name(src) if out is None else out
        if not os.path.exists(target):
            self.check(label, False, "no output written at %s" % target)
            return
        with open(target, "rb") as fh:
            self.check_gzip_output(label, fh.read(), expected)
        leftovers = [f for f in os.listdir(os.path.dirname(target)) if ".tmp." in f]
        self.check(label + " [no temp files]", not leftovers, str(leftovers))
        os.unlink(target)

    def fixture_tests(self):
        for name, gz_in, expected in fixtures():
            text = gunzip_file(gz_in)
            with open(gz_in, "rb") as fh:
                gz_bytes = fh.read()

            # The committed fixture itself, read in place.
            self.convert_file("%s fixture" % name, gz_in, expected,
                              self.path(name + ".fixture.hash.fasta.gz"))
            for label, suffix, data in variants(text, gz_bytes):
                src = self.write("%s.%s%s" % (name, label, suffix), data)
                self.convert_file("%s %s" % (name, label), src, expected,
                                  self.path("%s.%s.hash.fasta.gz" % (name, label)))
                os.unlink(src)

            for label, data, args in (("stdin gz -> stdout", gz_bytes, ["-"]),
                                      ("stdin plain -> -o -", text, ["-", "-o", "-"]),
                                      ("stdin bz2 -> stdout", bz2.compress(text), ["-"]),
                                      ("stdin xz -> stdout", lzma.compress(text), ["-"])):
                r = self.run(args, stdin=data)
                self.check("%s %s [exit 0]" % (name, label), r.returncode == 0,
                           r.stderr.decode(errors="replace"))
                self.check_gzip_output("%s %s" % (name, label), r.stdout, expected)

            # Default output naming beside a named input.
            d = self.path("default-" + name)
            os.mkdir(d)
            src = os.path.join(d, "sample.fasta.gz")
            shutil.copy(gz_in, src)
            self.convert_file("%s default -o" % name, src, expected)

    def naming_tests(self):
        d = self.path("naming")
        os.mkdir(d)
        payload = b">n\nACGT\n"
        expected = (b">sha256_" + hashlib.sha256(b"ACGT").hexdigest().encode()
                    + b" [n]\nACGT\n")
        compressors = {"": lambda b: b, ".gz": gzip.compress, ".bgz": gzip.compress,
                       ".bz2": bz2.compress, ".xz": lzma.compress}
        names = ["x" + s + c for s in FASTA_SUFFIXES for c in compressors]
        names += ["x.txt", "x.seq.gz", "x.seq.bz2", "x", "x.fasta.fa", "x.FASTA",
                  "x.fa.GZ", "x.fna.xz.gz"]
        for fname in names:
            comp = next((c for c in (".bgz", ".bz2", ".xz", ".gz") if fname.endswith(c)), "")
            src = os.path.join(d, fname)
            with open(src, "wb") as fh:
                fh.write(compressors[comp](payload))
            want = default_name(src)
            r = self.run([src])
            ok = r.returncode == 0 and os.path.exists(want)
            self.check("naming %s -> %s" % (fname, os.path.basename(want)), ok,
                       r.stderr.decode(errors="replace") + " " + str(sorted(os.listdir(d))))
            if ok:
                with open(want, "rb") as fh:
                    self.check_gzip_output("naming %s" % fname, fh.read(), expected)
            for f in os.listdir(d):
                os.unlink(os.path.join(d, f))

    def expect_failure(self, label, src, out=None):
        out = out or self.path("failed.hash.fasta.gz")
        r = self.run([src, "-o", out])
        self.check(label + " [non-zero exit]", r.returncode != 0)
        self.check(label + " [stderr message]", len(r.stderr.strip()) > 0)
        self.check(label + " [no output file]", not os.path.exists(out))
        leftovers = [f for f in os.listdir(os.path.dirname(out)) if ".tmp." in f]
        self.check(label + " [no temp files]", not leftovers, str(leftovers))
        return r

    def error_tests(self):
        for name, data in invalid_fixtures():
            self.expect_failure("invalid %s (fixture)" % name,
                                os.path.join(INVALID, name + ".input.fasta.gz"))
            for label, suffix, blob in (("plain", ".fa", data), ("bz2", ".fa.bz2", bz2.compress(data)),
                                        ("xz", ".fa.xz", lzma.compress(data))):
                src = self.write("invalid-%s%s" % (name, suffix), blob)
                self.expect_failure("invalid %s (%s)" % (name, label), src)
            r = self.run(["-"], stdin=data)
            self.check("invalid %s stdin [non-zero exit]" % name, r.returncode != 0)

        # Unsupported compression formats must be named in the error.
        for fmt, blob in (("zstd", b"\x28\xb5\x2f\xfd" + b"\x00" * 32),
                          ("zip", b"PK\x03\x04" + b"\x00" * 32)):
            src = self.write("unsupported." + fmt, blob)
            r = self.expect_failure("unsupported %s" % fmt, src)
            self.check("unsupported %s [format named]" % fmt,
                       fmt in r.stderr.decode(errors="replace").lower(),
                       r.stderr.decode(errors="replace"))

        # Truncated compressed streams.
        text = b">a\n" + b"ACGT" * 20000 + b"\n"
        for fmt, comp in (("gzip", gzip.compress), ("bzip2", bz2.compress), ("xz", lzma.compress)):
            good = comp(text)
            src = self.write("truncated." + fmt, good[: len(good) // 2])
            self.expect_failure("truncated %s" % fmt, src)

        # After the last complete stream only another valid stream of the same
        # format may follow (plus xz stream padding: NUL bytes in fours).
        first, second = b">a\nACGT\n", b">b\nGGGG\n"
        for fmt, comp in (("gzip", gzip.compress), ("bzip2", bz2.compress), ("xz", lzma.compress)):
            s1, s2 = comp(first), comp(second)
            bad = [("corrupt second stream", s1 + bytes([s2[0] ^ 0xFF]) + s2[1:]),
                   ("corrupt second stream header", s1 + s2[:3] + bytes([s2[3] ^ 0xFF]) + s2[4:]),
                   ("trailing junk", s1 + b"garbage"),
                   ("truncated second stream", s1 + s2[: len(s2) // 2])]
            if fmt == "xz":
                bad += [("padding not a multiple of 4", s1 + b"\0" * 3),
                        ("padding of 5 between streams", s1 + b"\0" * 5 + s2)]
            else:
                bad += [("trailing zero bytes", s1 + b"\0" * 16)]
            for label, blob in bad:
                src = self.write("trailing.%s" % fmt, blob)
                self.expect_failure("%s %s" % (fmt, label), src)
            if fmt == "xz":
                both = hash_record(b"a", b"ACGT") + hash_record(b"b", b"GGGG")
                for label, blob, expected in (
                        ("padding of 16 at end", s1 + b"\0" * 16, hash_record(b"a", b"ACGT")),
                        ("padding of 4 between streams", s1 + b"\0" * 4 + s2, both)):
                    self.convert_file("xz %s" % label, self.write("padded.xz", blob), expected,
                                      self.path("padded.hash.fasta.gz"))

        # Output path equal to the input must be refused, and the input kept.
        src = self.write("same.fasta", b">a\nACGT\n")
        before = sha256_file(src)
        for label, out in (("", src), (" (alias)", os.path.join(self.tmp, ".", "same.fasta"))):
            r = self.run([src, "-o", out])
            self.check("error output==input%s [non-zero exit]" % label, r.returncode != 0)
            self.check("error output==input%s [input unchanged]" % label,
                       sha256_file(src) == before)

        # Planted temporary names (a symlink and a hard link to the input) must not
        # be written through. `exec` keeps the shell's PID for the converter.
        out = self.path("planted.hash.fasta.gz")
        plant = ('ln -s "$1" "$2.tmp.$$"; ln -s "$1" "$2.tmp.$$.0"; ln "$1" "$2.tmp.$$.1"; '
                 'shift 2; exec "$@"')
        r = subprocess.run(["sh", "-c", plant, "sh", src, out] + self.cmd + [src, "-o", out],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.check("planted temp links [exit 0]", r.returncode == 0, r.stderr.decode(errors="replace"))
        self.check("planted temp links [input unchanged]", sha256_file(src) == before)
        if os.path.exists(out):
            with open(out, "rb") as fh:
                self.check_gzip_output("planted temp links", fh.read(), hash_record(b"a", b"ACGT"))
        else:
            self.check("planted temp links", False, "no output written")
        for f in os.listdir(self.tmp):
            if f.startswith("planted."):
                os.unlink(self.path(f))

        r = self.run([self.path("missing.fasta"), "-o", self.path("missing.out.gz")])
        self.check("error missing input [non-zero exit]", r.returncode != 0)
        self.check("error missing input [no output]", not os.path.exists(self.path("missing.out.gz")))

        r = self.run([src, "-o", self.path("no-such-dir", "out.hash.fasta.gz")])
        self.check("error unwritable output [non-zero exit]", r.returncode != 0)

        r = self.run([])
        self.check("error no arguments [exit 2]", r.returncode == 2)
        r = self.run([src, "--bogus"])
        self.check("error unknown option [exit 2]", r.returncode == 2)

    def run_all(self):
        print("== %s: %s" % (self.name, " ".join(self.cmd)))
        try:
            self.fixture_tests()
            self.naming_tests()
            self.error_tests()
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)
        status = "PASS" if not self.failed else "FAIL"
        print("== %s: %s (%d checks passed, %d failed)"
              % (self.name, status, self.passed, len(self.failed)))
        return not self.failed


def random_fasta(seed, records=300):
    """Seeded FASTA exercising wrapping, case, CR/CRLF, blank lines and odd headers."""
    import random
    rng = random.Random(seed)
    alphabet = b"ACGTNacgtnRYKMSWBDHVrykmswbdhvU-.*EFILPQXZ"
    head_chars = [bytes([c]) for c in range(0x20, 0x7F)] + [b"\t", b"\xc3\xa9", b"\r"]
    out = bytearray()
    last = b""
    for i in range(records):
        eol = b"\r\n" if rng.random() < 0.2 else b"\n"
        header = b"".join(rng.choice(head_chars) for _ in range(rng.randint(0, 40)))
        out += b">" + header + eol
        if rng.random() < 0.05 and i:
            seq = last  # repeat a previous sequence under a new header
        else:
            seq = bytes(rng.choice(alphabet) for _ in range(rng.randint(1, 700)))
        last = seq
        width = rng.choice([1, 7, 60, 79, 80, 81, 1000])
        for j in range(0, len(seq), width):
            out += seq[j:j + width] + (b" \t\r" if rng.random() < 0.05 else b"") + eol
            if rng.random() < 0.03:
                out += eol
            if rng.random() < 0.03:
                out += b";" + header + eol  # comment line (spec section 3)
    return bytes(out)


def cross_check(targets):
    """All implementations must emit identical decompressed bytes."""
    tmp = tempfile.mkdtemp(prefix="hashfasta-cross-")
    ok = True
    try:
        for seed, comp, ext in ((1, gzip.compress, "gz"), (2, bz2.compress, "bz2"),
                                (3, lzma.compress, "xz")):
            data = random_fasta(seed)
            src = os.path.join(tmp, "random-%d.fa.%s" % (seed, ext))
            with open(src, "wb") as fh:
                fh.write(comp(data))
            outputs = {}
            for name, cmd in targets:
                out = os.path.join(tmp, "%s-%d.hash.fasta.gz" % (name, seed))
                r = subprocess.run(cmd + [src, "-o", out], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
                outputs[name] = (gunzip_file(out) if r.returncode == 0
                                 else "exit %d: %s" % (r.returncode, r.stderr.decode(errors="replace")))
            values = list(outputs.values())
            agree = isinstance(values[0], bytes) and all(v == values[0] for v in values)
            print("cross-check seed %d (%s, %d bytes in): %s" % (
                seed, ext, len(data),
                "identical across " + ", ".join(outputs) if agree else "MISMATCH"))
            if not agree:
                ok = False
                for name, v in outputs.items():
                    desc = hashlib.sha256(v).hexdigest()[:16] if isinstance(v, bytes) else v
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
        exe = cmd[-1]
        if not os.path.exists(exe) and shutil.which(exe) is None:
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
