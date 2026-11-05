#!/usr/bin/env python3
"""Time one HashFASTA implementation on every prepared benchmark input.

For each dataset in inputs.csv, runs the converter once as a warm-up and then
--reps timed repetitions, each converting <inputdir>/<label>.fasta.gz to a
fresh gzip output. The small runstat launcher (runstat.c) measures wall-clock
time around the converter process only, and its peak RSS, user and system CPU
time from wait4. After the timed
interval each output is decompressed and its SHA-256 recorded, so outputs can
be compared across implementations.

    python3 benchmarks/bench.py --impl rust --inputs inputs.csv \\
        --inputdir DIR --workdir DIR --runstat ./runstat --out timings-rust.csv -- CMD...

CMD is the implementation's normal command line; "INPUT -o OUTPUT" is
appended to it.
"""

import argparse
import csv
import gzip
import hashlib
import os
import subprocess
import sys


def sha256_gunzip(path):
    digest = hashlib.sha256()
    with gzip.open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run_once(runstat, cmd, src, out):
    """Run one conversion under runstat; return (wall, user, sys, peak RSS MiB)."""
    if os.path.exists(out):
        os.unlink(out)
    proc = subprocess.run([runstat] + cmd + [src, "-o", out], stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE)
    lines = proc.stderr.decode(errors="replace").strip().splitlines()
    if proc.returncode != 0 or not lines:
        sys.exit("%s failed on %s (exit %d): %s" % (cmd, src, proc.returncode, "\n".join(lines)))
    wall, user, sys_s, rss_kib = lines[-1].split()
    return float(wall), float(user), float(sys_s), int(rss_kib) / 1024.0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--impl", required=True)
    parser.add_argument("--inputs", required=True, help="inputs.csv from prepare_inputs.py")
    parser.add_argument("--inputdir", required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--runstat", required=True, help="compiled benchmarks/runstat.c")
    parser.add_argument("cmd", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
    if not cmd:
        parser.error("no implementation command given after --")

    with open(args.inputs) as fh:
        datasets = list(csv.DictReader(fh))
    os.makedirs(args.workdir, exist_ok=True)
    fields = ["impl", "label", "panel", "records", "residues", "phase", "rep",
              "wall_s", "user_s", "sys_s", "max_rss_mib", "output_bytes", "output_sha256"]
    with open(args.out, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for ds in datasets:
            src = os.path.join(args.inputdir, ds["label"] + ".fasta.gz")
            out = os.path.join(args.workdir, "%s-%s.hash.fasta.gz" % (args.impl, ds["label"]))
            runs = [("warmup", i) for i in range(1, args.warmup + 1)]
            runs += [("timed", i) for i in range(1, args.reps + 1)]
            for phase, rep in runs:
                wall, user, sys_s, rss = run_once(args.runstat, cmd, src, out)
                # Validation happens after the timed interval.
                row = {"impl": args.impl, "label": ds["label"], "panel": ds["panel"],
                       "records": ds["records"], "residues": ds["residues"],
                       "phase": phase, "rep": rep, "wall_s": "%.4f" % wall,
                       "user_s": "%.4f" % user, "sys_s": "%.4f" % sys_s,
                       "max_rss_mib": "%.1f" % rss, "output_bytes": os.path.getsize(out),
                       "output_sha256": sha256_gunzip(out)}
                writer.writerow(row)
                fh.flush()
                print("%s %s %s %d: %.3f s, %.1f MiB" % (args.impl, ds["label"], phase, rep,
                                                         wall, rss), flush=True)
            os.unlink(out)


if __name__ == "__main__":
    main()
