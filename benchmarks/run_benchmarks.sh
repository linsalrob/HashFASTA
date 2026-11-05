#!/bin/bash
# Build the implementations, then submit input preparation and the benchmark
# array (one element per implementation) to Slurm, and wait for it to finish.
#
#   BENCH=/scratch/$PAWSEY_PROJECT/$USER/hashfasta_bench benchmarks/run_benchmarks.sh
#
# $BENCH must contain datasets.local.json (see datasets.example.json). Inputs,
# outputs and results stay under $BENCH; copy results/ into the repository to
# publish them.
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
: "${BENCH:?set BENCH to the benchmark working directory}"
PYTHON=${PYTHON:-python3.11}
ACCOUNT=${ACCOUNT:-$PAWSEY_PROJECT}
mkdir -p "$BENCH/results" "$BENCH/logs"

make -C "$REPO" build
cc -O2 -o "$BENCH/runstat" "$REPO/benchmarks/runstat.c"
export BENCH REPO PYTHON

prep=$(sbatch --parsable --account="$ACCOUNT" --export=ALL \
    -o "$BENCH/logs/%x-%j.out" "$REPO/benchmarks/slurm/prepare.slurm")
echo "prepare: job $prep"
sbatch --wait --account="$ACCOUNT" --export=ALL --array=0-4 \
    --dependency=afterok:"$prep" \
    -o "$BENCH/logs/%x-%A_%a.out" "$REPO/benchmarks/slurm/bench.slurm"
echo "benchmarks finished; results in $BENCH/results"
