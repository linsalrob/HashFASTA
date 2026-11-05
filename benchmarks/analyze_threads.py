#!/usr/bin/env python3
"""Summarise the --threads benchmark and draw the speedup figure.

    python3 benchmarks/analyze_threads.py THREADS_DIR [REFERENCE_RESULTS_DIR]

Reads timings-<impl>-t<N>.csv from THREADS_DIR (written by run_threads.sh)
and writes there:
    threads_summary.csv   median time, speedup over 1 thread, efficiency,
                          peak RSS and output size per implementation/input/N
    threads_scaling.png   speedup against threads, one panel per input
    threads_scaling.svg
It exits non-zero if any run of an input produced different decompressed
output, or output different from REFERENCE_RESULTS_DIR's timings (the
single-threaded benchmark), when given.

Needs pandas, seaborn and matplotlib.
"""

import glob
import os
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
import seaborn as sns  # noqa: E402

IMPLS = ["c", "cpp", "rust", "python"]
NAMES = {"c": "C", "cpp": "C++", "rust": "Rust", "python": "Python"}
TITLES = {"dna-4-arabidopsis": "A. thaliana genome (120 Mbp)",
          "dna-5-human-chr1-4": "Human chr1–4 (880 Mbp)",
          "protein-6-busco-vertebrata": "BUSCO vertebrata (296 M aa)"}


def load(outdir):
    frames = []
    for path in sorted(glob.glob(os.path.join(outdir, "timings-*-t*.csv"))):
        m = re.search(r"timings-(\w+)-t(\d+)\.csv$", path)
        df = pd.read_csv(path)
        df["threads"] = int(m.group(2))
        frames.append(df)
    return pd.concat(frames)


def validate(timings, reference_dir):
    digests = timings.groupby("label")["output_sha256"].unique()
    bad = [label for label, d in digests.items() if len(d) != 1]
    if reference_dir:
        ref = pd.concat(pd.read_csv(p) for p in glob.glob(os.path.join(reference_dir, "timings-*.csv")))
        ref = ref.groupby("label")["output_sha256"].first()
        bad += [label for label, d in digests.items() if label in ref and d[0] != ref[label]]
    if bad:
        sys.exit("outputs differ for: %s" % ", ".join(sorted(set(bad))))
    return len(digests)


def summarise(timings):
    timed = timings[timings.phase == "timed"]
    s = (timed.groupby(["panel", "label", "residues", "impl", "threads"])
         .agg(runs=("wall_s", "size"), median_wall_s=("wall_s", "median"),
              min_wall_s=("wall_s", "min"), max_wall_s=("wall_s", "max"),
              peak_rss_mib=("max_rss_mib", "max"), output_bytes=("output_bytes", "median"))
         .reset_index())
    base = s[s.threads == 1].set_index(["label", "impl"])
    key = list(zip(s.label, s.impl))
    s["speedup"] = base.loc[key, "median_wall_s"].values / s.median_wall_s
    s["efficiency"] = s.speedup / s.threads
    s["output_vs_1_thread"] = s.output_bytes / base.loc[key, "output_bytes"].values
    return s.sort_values(["panel", "residues", "impl", "threads"])


def plot(summary, outdir):
    sns.set_theme(style="whitegrid", context="paper", font_scale=1.15)
    palette = dict(zip(IMPLS, sns.color_palette("colorblind", len(IMPLS))))
    markers = dict(zip(IMPLS, ["o", "s", "D", "^"]))
    labels = [l for l in TITLES if l in set(summary.label)]
    fig, axes = plt.subplots(1, len(labels), figsize=(4.2 * len(labels), 4.3),
                             constrained_layout=True, sharey=True)
    threads = sorted(summary.threads.unique())
    for ax, label in zip(axes, labels):
        ax.plot(threads, threads, ls="--", color="0.6", lw=1, label="Ideal (linear)")
        for impl in IMPLS:
            df = summary[(summary.label == label) & (summary.impl == impl)]
            ax.plot(df.threads, df.speedup, marker=markers[impl], color=palette[impl],
                    ms=6, lw=1.4, label=NAMES[impl])
        ax.set(xscale="log", yscale="log", title=TITLES[label], xlabel="Compression threads (--threads)")
        ax.set_xticks(threads, [str(t) for t in threads])
        ax.set_yticks(threads, [str(t) for t in threads])
        ax.minorticks_off()
    axes[0].set_ylabel("Speedup over 1 thread (median wall time)")
    axes[0].legend(loc="upper left", fontsize=9)
    fig.suptitle("HashFASTA --threads speedup (same cores and memory per thread at every N)")
    for ext in ("png", "svg"):
        fig.savefig(os.path.join(outdir, "threads_scaling." + ext), dpi=200)
    # matplotlib leaves trailing spaces in SVG path data; drop them for clean diffs.
    svg = os.path.join(outdir, "threads_scaling.svg")
    with open(svg) as fh:
        lines = [line.rstrip() for line in fh]
    with open(svg, "w") as fh:
        fh.write("\n".join(lines) + "\n")


def main(argv):
    if len(argv) not in (2, 3):
        sys.exit(__doc__)
    outdir = argv[1]
    timings = load(outdir)
    n = validate(timings, argv[2] if len(argv) == 3 else None)
    summary = summarise(timings)
    summary.to_csv(os.path.join(outdir, "threads_summary.csv"), index=False, float_format="%.4g")
    plot(summary, outdir)
    print("validated identical output across all runs and thread counts for %d inputs" % n)
    cols = ["label", "impl", "threads", "median_wall_s", "speedup", "efficiency", "peak_rss_mib",
            "output_vs_1_thread"]
    print(summary[cols].to_string(index=False, float_format=lambda v: "%.3f" % v))


if __name__ == "__main__":
    main(sys.argv)
