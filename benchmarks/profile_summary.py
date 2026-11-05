#!/usr/bin/env python3
"""Group perf samples from profile.slurm into conversion stages.

    python3 benchmarks/profile_summary.py PROFILE_DIR > profile.csv

Reads every <impl>-<label>.txt written by `perf report --sort dso,sym` and
assigns each symbol's share of user-space samples to a stage by its name:
output compression (deflate), input decompression (inflate), CRC-32,
SHA-256, or everything else (parsing, normalisation, interpreter, I/O).

System zlib is usually stripped, so its static functions (longest_match,
deflate_slow, inflate_fast, ...) appear only as addresses. Each is assigned
to the source file of the next exported symbol in the library, because a
zlib object file's static functions precede its exported ones: deflate.c and
trees.c are compression, infback.c, inffast.c and inflate.c decompression.
"""

import bisect
import csv
import glob
import os
import re
import subprocess
import sys

STAGES = [
    ("compress", re.compile(r"deflate|longest_match|compress_block|send_bits|_tr_|fill_window"
                            r"|build_tree|pqdownheap|gen_bitlen|scan_tree|send_tree|bi_flush"
                            r"|slide_hash|insert_string|miniz_oxide::deflate", re.I)),
    ("decompress", re.compile(r"inflate|miniz_oxide::inflate", re.I)),
    ("crc32", re.compile(r"crc32|crc_fold|crc_32", re.I)),
    ("sha256", re.compile(r"sha256|sha2::|sha_block|SHA256", re.I)),
]
LINE = re.compile(r"^\s*([\d.]+)%\s+(\S+)\s+\[[.k]\]\s+(.*\S)\s*$")
LIB_DIRS = ["/usr/lib64", "/lib64", "/usr/lib/x86_64-linux-gnu", "/usr/lib"]
_exports = {}


def exported(dso):
    """Sorted (addresses, names) of a shared library's exported functions."""
    if dso not in _exports:
        pairs = []
        for d in LIB_DIRS:
            path = os.path.join(d, dso)
            if os.path.exists(path):
                out = subprocess.run(["nm", "-D", "--defined-only", path],
                                     capture_output=True, text=True).stdout
                pairs = sorted((int(f[0], 16), f[2]) for f in map(str.split, out.splitlines())
                               if len(f) == 3 and f[1] in "Tt")
                break
        _exports[dso] = ([a for a, _ in pairs], [n for _, n in pairs])
    return _exports[dso]


def zlib_static(dso, address):
    """Name the stage of an unnamed address inside a stripped zlib."""
    addrs, names = exported(dso)
    i = bisect.bisect_right(addrs, address)
    following = names[i] if i < len(names) else ""
    if following.startswith(("deflate", "zlibVersion")):  # zlibVersion follows trees.c
        return "compress"
    if following.startswith("inflate"):
        return "decompress"
    if following.startswith(("crc32", "get_crc_table")):
        return "crc32"
    return "other"


def stage_of(dso, symbol):
    if symbol.startswith("0x") and dso.startswith("libz.so"):
        return zlib_static(dso, int(symbol, 16))
    if symbol == "compress" and not dso.startswith("libz.so"):
        return "sha256"  # the C implementation's SHA-256 block function
    for name, pattern in STAGES:
        if pattern.search(symbol):
            return name
    return "other"


def summarise(path):
    shares = dict.fromkeys([s for s, _ in STAGES] + ["other"], 0.0)
    total = 0.0
    with open(path) as fh:
        for line in fh:
            m = LINE.match(line)
            if m:
                pct = float(m.group(1))
                shares[stage_of(m.group(2), m.group(3))] += pct
                total += pct
    # Symbols below perf's --percent-limit are counted as "other".
    shares["other"] += max(0.0, 100.0 - total)
    return shares


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__)
    writer = csv.writer(sys.stdout, lineterminator="\n")
    header = None
    for path in sorted(glob.glob(os.path.join(argv[1], "*.txt"))):
        impl, label = os.path.basename(path)[:-4].split("-", 1)
        shares = summarise(path)
        if header is None:
            header = list(shares)
            writer.writerow(["impl", "label"] + ["%s_pct" % s for s in header])
        writer.writerow([impl, label] + ["%.1f" % shares[s] for s in header])


if __name__ == "__main__":
    main(sys.argv)
