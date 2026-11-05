#!/bin/bash
#SBATCH --job-name=hashfasta-bench-prep
#SBATCH --partition=work
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=02:00:00
# Build the benchmark inputs. Submitted by benchmarks/run_benchmarks.sh, which
# sets BENCH (scratch working root) and REPO (HashFASTA checkout).
set -euo pipefail
python3.11 "$REPO/benchmarks/prepare_inputs.py" "$BENCH/datasets.local.json" \
    "$BENCH/inputs" "$BENCH/results/inputs.csv"
