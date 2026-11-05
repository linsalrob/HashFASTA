#!/bin/bash
#SBATCH --job-name=hashfasta-bench
#SBATCH --partition=work
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=24G
#SBATCH --time=06:00:00
# Time one implementation (array index 0-4) on every benchmark input. Every
# implementation gets the same allocation. Submitted by run_benchmarks.sh,
# which sets BENCH, REPO and PYTHON.
set -euo pipefail
IMPLS=(python rust c cpp perl)
IMPL=${IMPLS[$SLURM_ARRAY_TASK_ID]}
case $IMPL in
    python) CMD=("$PYTHON" "$REPO/implementations/python/run_hashfasta.py") ;;
    rust)   CMD=("$REPO/implementations/rust/target/release/hashfasta") ;;
    c)      CMD=("$REPO/implementations/c/hashfasta") ;;
    cpp)    CMD=("$REPO/implementations/cpp/hashfasta") ;;
    perl)   CMD=(perl "$REPO/implementations/perl/hashfasta.pl") ;;
esac

# Prefer node-local storage for inputs and outputs, so Lustre load does not
# enter the timings; fall back to scratch when it is too small.
need=$(( $(du -sb "$BENCH/inputs" | cut -f1) * 3 + (2 << 30) ))
local_root=${TMPDIR:-/tmp}
if [ "$(df --output=avail -B1 "$local_root" | tail -1)" -gt "$need" ]; then
    WORK=$local_root/hashfasta-bench-$SLURM_JOB_ID
    STORAGE="node-local $(df --output=fstype "$local_root" | tail -1)"
else
    WORK=$BENCH/work/$IMPL-$SLURM_JOB_ID
    STORAGE="scratch (Lustre)"
fi
mkdir -p "$WORK/inputs"
trap 'rm -rf "$WORK"' EXIT
cp "$BENCH"/inputs/*.fasta.gz "$WORK/inputs/"

{
    echo "impl=$IMPL"
    echo "command=${CMD[*]##*/}"
    echo "commit=$(git -C "$REPO" rev-parse HEAD)"
    echo "job=$SLURM_JOB_ID node=$(hostname) partition=$SLURM_JOB_PARTITION"
    echo "allocation=cpus-per-task=$SLURM_CPUS_PER_TASK mem=$SLURM_MEM_PER_NODE MiB"
    echo "cpu=$(lscpu | sed -n 's/^Model name: *//p')"
    echo "os=$(. /etc/os-release && echo "$PRETTY_NAME") kernel=$(uname -r)"
    echo "storage=$STORAGE"
    case $IMPL in
        python) echo "version=$("$PYTHON" --version 2>&1)" ;;
        rust)   echo "version=$(rustc --version 2>/dev/null || echo unknown)" ;;
        c)      echo "version=$(cc --version | head -1)" ;;
        cpp)    echo "version=$(c++ --version | head -1)" ;;
        perl)   echo "version=perl $(perl -e 'print $^V')" ;;
    esac
} > "$BENCH/results/environment-$IMPL.txt"

python3.11 "$REPO/benchmarks/bench.py" --impl "$IMPL" --inputs "$BENCH/results/inputs.csv" \
    --inputdir "$WORK/inputs" --workdir "$WORK/out" --runstat "$BENCH/runstat" \
    --out "$BENCH/results/timings-$IMPL.csv" -- "${CMD[@]}"
