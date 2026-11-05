#!/bin/bash
#SBATCH --job-name=hashfasta-threads
#SBATCH --partition=work
#SBATCH --ntasks=1
#SBATCH --mem-per-cpu=1840M
#SBATCH --time=01:30:00
# Time one implementation (array index 0-3) with --threads $THREADS on the
# inputs listed in THREAD_LABELS. Submit with --cpus-per-task=$((2 * THREADS)),
# i.e. THREADS whole cores (Setonix counts both hardware threads of a core),
# so every thread count gets the same cores and memory per thread. Submitted
# by run_threads.sh, which sets BENCH, REPO, PYTHON and THREADS.
set -euo pipefail
IMPLS=(c cpp rust python)
IMPL=${IMPLS[$SLURM_ARRAY_TASK_ID]}
LABELS=${THREAD_LABELS:-dna-4-arabidopsis dna-5-human-chr1-4 protein-6-busco-vertebrata}
case $IMPL in
    python) CMD=("$PYTHON" "$REPO/implementations/python/run_hashfasta.py") ;;
    rust)   CMD=("$REPO/implementations/rust/target/release/hashfasta") ;;
    c)      CMD=("$REPO/implementations/c/hashfasta") ;;
    cpp)    CMD=("$REPO/implementations/cpp/hashfasta") ;;
esac
CMD+=(--threads "$THREADS")

OUT=$BENCH/threads
WORK=${TMPDIR:-/tmp}/hashfasta-threads-$SLURM_JOB_ID
mkdir -p "$WORK/inputs" "$OUT"
trap 'rm -rf "$WORK"' EXIT
head -1 "$BENCH/results/inputs.csv" > "$WORK/inputs.csv"
for label in $LABELS; do
    cp "$BENCH/inputs/$label.fasta.gz" "$WORK/inputs/"
    grep "^$label," "$BENCH/results/inputs.csv" >> "$WORK/inputs.csv"
done

{
    echo "impl=$IMPL threads=$THREADS"
    echo "commit=$(git -C "$REPO" rev-parse HEAD)"
    echo "job=$SLURM_JOB_ID node=$(hostname) partition=$SLURM_JOB_PARTITION"
    echo "allocation=cpus-per-task=$SLURM_CPUS_PER_TASK mem-per-cpu=$SLURM_MEM_PER_CPU MiB"
    echo "cpu=$(lscpu | sed -n 's/^Model name: *//p')"
    echo "storage=node-local $(df --output=fstype "$WORK" | tail -1)"
} > "$OUT/environment-$IMPL-t$THREADS.txt"

python3.11 "$REPO/benchmarks/bench.py" --impl "$IMPL" --inputs "$WORK/inputs.csv" \
    --inputdir "$WORK/inputs" --workdir "$WORK/out" --runstat "$BENCH/runstat" \
    --out "$OUT/timings-$IMPL-t$THREADS.csv" -- "${CMD[@]}"
