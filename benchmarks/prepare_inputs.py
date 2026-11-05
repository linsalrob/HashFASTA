#!/usr/bin/env python3
"""Build the benchmark inputs described by a dataset JSON file.

Each dataset concatenates one or more source FASTA files (plain or gzip),
optionally keeping only the first records up to a residue budget, and is
written as <outdir>/<label>.fasta.gz (gzip level 6, mtime 0). A metadata CSV
records sequence counts and total residues per dataset. Source paths are read
from the (private, uncommitted) dataset file and are never written to the
metadata, so the CSV can be published.

    python3 benchmarks/prepare_inputs.py datasets.local.json OUTDIR inputs.csv

Dataset file: a JSON list of objects with keys
    label         short public name, e.g. "dna-03-bacterial-set"
    panel         "dna" or "protein"
    description   public description (no paths)
    sources       list of FASTA paths, read in order
    max_residues  optional: keep whole records, in order, until this many
                  residues have been written (deterministic subset)
"""

import csv
import gzip
import json
import os
import sys

WHITESPACE = b" \t\n\x0b\x0c\r"


def open_fasta(path):
    with open(path, "rb") as fh:
        magic = fh.read(2)
    return gzip.open(path, "rb") if magic == b"\x1f\x8b" else open(path, "rb")


def build(dataset, outdir):
    """Write one dataset; return its public metadata row."""
    path = os.path.join(outdir, dataset["label"] + ".fasta.gz")
    budget = dataset.get("max_residues")
    records = residues = plain_bytes = 0
    stop = False
    tmp = path + ".partial"
    with gzip.GzipFile(tmp, "wb", compresslevel=6, mtime=0) as out:
        for src in dataset["sources"]:
            with open_fasta(src) as fh:
                for line in fh:
                    if line.startswith(b">"):
                        if budget is not None and residues >= budget:
                            stop = True
                            break
                        records += 1
                    elif not line.startswith(b";"):
                        residues += len(line.translate(None, WHITESPACE))
                    out.write(line)
                    plain_bytes += len(line)
            if stop:
                break
    os.replace(tmp, path)
    return {
        "label": dataset["label"],
        "panel": dataset["panel"],
        "description": dataset["description"],
        "records": records,
        "residues": residues,
        "uncompressed_bytes": plain_bytes,
        "gzip_bytes": os.path.getsize(path),
    }


def main(argv):
    if len(argv) != 4:
        sys.exit(__doc__)
    with open(argv[1]) as fh:
        datasets = json.load(fh)
    os.makedirs(argv[2], exist_ok=True)
    rows = []
    for dataset in datasets:
        row = build(dataset, argv[2])
        print("%(label)s: %(records)d records, %(residues)d residues" % row, flush=True)
        rows.append(row)
    with open(argv[3], "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main(sys.argv)
