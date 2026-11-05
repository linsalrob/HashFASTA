#!/bin/bash
# Benchmark --threads 1, 2, 4 and 8 for the C, C++, Rust and Python
# implementations on inputs already prepared by run_benchmarks.sh, and wait.
# Each thread count is one Slurm array (one element per implementation) with
# that many whole cores.
#
#   BENCH=/scratch/$PAWSEY_PROJECT/$USER/hashfasta_bench benchmarks/run_threads.sh
#
# Results go to $BENCH/threads; summarise with analyze_threads.py.
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
: "${BENCH:?set BENCH to the benchmark working directory}"
PYTHON=${PYTHON:-python3.11}
ACCOUNT=${ACCOUNT:-$PAWSEY_PROJECT}
mkdir -p "$BENCH/threads" "$BENCH/logs"

make -C "$REPO" build
cc -O2 -o "$BENCH/runstat" "$REPO/benchmarks/runstat.c"
export BENCH REPO PYTHON

jobs=()
for threads in 1 2 4 8; do
    jobs+=("$(THREADS=$threads sbatch --parsable --account="$ACCOUNT" --export=ALL \
        --array=0-3 --cpus-per-task=$((2 * threads)) \
        -o "$BENCH/logs/%x-t$threads-%A_%a.out" "$REPO/benchmarks/slurm/threads.slurm")")
done
echo "thread arrays: ${jobs[*]}"
# Wait for all four arrays.
sbatch --wait --account="$ACCOUNT" --dependency=afterany:"$(IFS=:; echo "${jobs[*]}")" \
    --partition=work --ntasks=1 --cpus-per-task=1 --mem=1G --time=00:05:00 \
    -o /dev/null --wrap true
echo "thread benchmarks finished; results in $BENCH/threads"
