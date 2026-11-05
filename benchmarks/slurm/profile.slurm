#!/bin/bash
#SBATCH --job-name=hashfasta-profile
#SBATCH --partition=work
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=14G
#SBATCH --time=01:30:00
# Sample where each implementation spends its CPU time (perf, user space only)
# on one large DNA input and one large protein input. Needs BENCH, REPO and
# PYTHON; writes $BENCH/profile/<impl>-<label>.txt (perf report by DSO and
# symbol). Summarise with benchmarks/profile_summary.py.
set -euo pipefail
LABELS=(dna-5-human-chr1-4 protein-6-busco-vertebrata)
WORK=${TMPDIR:-/tmp}/hashfasta-profile-$SLURM_JOB_ID
mkdir -p "$WORK" "$BENCH/profile"
trap 'rm -rf "$WORK"' EXIT
for label in "${LABELS[@]}"; do cp "$BENCH/inputs/$label.fasta.gz" "$WORK/"; done

for impl in c cpp rust python perl; do
    case $impl in
        python) CMD=("$PYTHON" "$REPO/implementations/python/run_hashfasta.py") ;;
        rust)   CMD=("$REPO/implementations/rust/target/release/hashfasta") ;;
        c)      CMD=("$REPO/implementations/c/hashfasta") ;;
        cpp)    CMD=("$REPO/implementations/cpp/hashfasta") ;;
        perl)   CMD=(perl "$REPO/implementations/perl/hashfasta.pl") ;;
    esac
    for label in "${LABELS[@]}"; do
        data=$WORK/$impl-$label.perf.data
        start=$SECONDS
        perf record -q -F 499 -e cpu-clock:u -o "$data" -- \
            "${CMD[@]}" "$WORK/$label.fasta.gz" -o "$WORK/out.hash.fasta.gz"
        echo "$impl $label $((SECONDS - start)) s"
        perf report -i "$data" --stdio --no-children --sort dso,sym --percent-limit 0.2 \
            > "$BENCH/profile/$impl-$label.txt" 2>/dev/null
        rm -f "$data" "$WORK/out.hash.fasta.gz"
    done
done
