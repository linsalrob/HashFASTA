#!/usr/bin/env python3
"""Summarise benchmark timings, fit empirical scaling and draw the figure.

    python3 benchmarks/analyze.py benchmarks/results

Reads inputs.csv and timings-*.csv from the results directory and writes:
    summary.csv           median time, throughput and peak RSS per implementation/input
    scaling_fits.csv      log-log fits of median time against input size
    runtime_scaling.png   two-panel figure (DNA left, protein right)
    runtime_scaling.svg
It exits non-zero if any two runs of the same input produced different
decompressed output.

Needs pandas, scipy, seaborn and matplotlib.
"""

import glob
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import seaborn as sns  # noqa: E402
from scipy import stats  # noqa: E402

IMPLS = ["c", "cpp", "rust", "python", "perl"]
NAMES = {"c": "C", "cpp": "C++", "rust": "Rust", "python": "Python", "perl": "Perl"}
PANELS = [("dna", "DNA", "Input size (bases)"),
          ("protein", "Protein", "Input size (amino-acid residues)")]
LARGE = 1e6  # second fit uses only inputs at least this large
# Small inputs are dominated by fixed start-up cost (interpreter launch), so the
# figure draws the fit over inputs of at least LARGE residues.
PLOT_FIT = "size>=1e6"


def validate(timings):
    """Every run of an input, in every implementation, must give the same output."""
    digests = timings.groupby("label")["output_sha256"].nunique()
    bad = digests[digests != 1]
    if len(bad):
        sys.exit("outputs differ for: %s" % ", ".join(bad.index))
    return len(digests)


def summarise(timings):
    timed = timings[timings.phase == "timed"]
    summary = (timed.groupby(["panel", "label", "impl", "records", "residues"])
               .agg(runs=("wall_s", "size"), median_wall_s=("wall_s", "median"),
                    min_wall_s=("wall_s", "min"), max_wall_s=("wall_s", "max"),
                    median_cpu_s=("cpu_s", "median"), peak_rss_mib=("max_rss_mib", "max"))
               .reset_index())
    summary["residues_per_s"] = summary.residues / summary.median_wall_s
    return summary.sort_values(["panel", "residues", "impl"])


def fit(df):
    """Least-squares fit of log10(time) on log10(size), with a 95% CI on the slope."""
    x, y = np.log10(df.residues), np.log10(df.median_wall_s)
    res = stats.linregress(x, y)
    half = stats.t.ppf(0.975, len(df) - 2) * res.stderr if len(df) > 2 else float("nan")
    return {"points": len(df), "slope": res.slope, "slope_stderr": res.stderr,
            "slope_ci95_low": res.slope - half, "slope_ci95_high": res.slope + half,
            "intercept": res.intercept, "r_squared": res.rvalue ** 2}


def fits(summary):
    rows = []
    for (panel, impl), df in summary.groupby(["panel", "impl"]):
        for subset, part in (("all", df), ("size>=1e6", df[df.residues >= LARGE])):
            if len(part) >= 2:
                rows.append(dict(panel=panel, impl=impl, subset=subset, **fit(part)))
    return pd.DataFrame(rows)


def plot(summary, fitted, outdir):
    sns.set_theme(style="whitegrid", context="paper", font_scale=1.15)
    palette = dict(zip(IMPLS, sns.color_palette("colorblind", len(IMPLS))))
    markers = dict(zip(IMPLS, ["o", "s", "D", "^", "v"]))
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.8), constrained_layout=True)
    for ax, (panel, title, xlabel) in zip(axes, PANELS):
        data = summary[summary.panel == panel]
        for impl in IMPLS:
            df = data[data.impl == impl]
            if df.empty:
                continue
            f = fitted[(fitted.panel == panel) & (fitted.impl == impl) & (fitted.subset == PLOT_FIT)]
            label = NAMES[impl]
            if len(f):
                label += " (slope %.2f)" % f.slope.iloc[0]
            ax.errorbar(df.residues, df.median_wall_s,
                        yerr=[df.median_wall_s - df.min_wall_s, df.max_wall_s - df.median_wall_s],
                        fmt=markers[impl], color=palette[impl], ms=6, capsize=2, label=label)
            if len(f):
                xs = np.logspace(np.log10(df.residues[df.residues >= LARGE].min()),
                                 np.log10(df.residues.max()), 50)
                ax.plot(xs, 10 ** (f.intercept.iloc[0] + f.slope.iloc[0] * np.log10(xs)),
                        color=palette[impl], lw=1.2, alpha=0.8)
        ax.set(xscale="log", yscale="log", xlabel=xlabel, ylabel="Median wall-clock time (s)",
               title=title)
        ax.legend(title="Implementation (log-log slope, inputs \u2265 10\u2076)", fontsize=9, title_fontsize=9,
                  loc="upper left", frameon=True)
    fig.suptitle("HashFASTA conversion time vs input size (observed empirical scaling)")
    for ext in ("png", "svg"):
        fig.savefig(os.path.join(outdir, "runtime_scaling." + ext), dpi=200)
    # matplotlib leaves trailing spaces in SVG path data; drop them for clean diffs.
    svg = os.path.join(outdir, "runtime_scaling.svg")
    with open(svg) as fh:
        lines = [line.rstrip() for line in fh]
    with open(svg, "w") as fh:
        fh.write("\n".join(lines) + "\n")


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__)
    outdir = argv[1]
    timings = pd.concat(pd.read_csv(p) for p in sorted(glob.glob(os.path.join(outdir, "timings-*.csv"))))
    timings["cpu_s"] = timings.user_s + timings.sys_s
    n = validate(timings)
    summary = summarise(timings)
    fitted = fits(summary)
    summary.to_csv(os.path.join(outdir, "summary.csv"), index=False, float_format="%.6g")
    fitted.to_csv(os.path.join(outdir, "scaling_fits.csv"), index=False, float_format="%.4f")
    plot(summary, fitted, outdir)
    print("validated identical output across all runs for %d inputs" % n)
    print(fitted.to_string(index=False, float_format=lambda v: "%.3f" % v))


if __name__ == "__main__":
    main(sys.argv)
