# Benchmarks

Runtime and memory of the five HashFASTA implementations on DNA and protein
inputs of increasing size. Results and the report are in
[`REPORT.md`](REPORT.md); raw data are in [`results/`](results/).

## Files

| File | Purpose |
| --- | --- |
| `datasets.example.json` | The datasets used, with source paths replaced by placeholders |
| `prepare_inputs.py` | Builds each benchmark input as `<label>.fasta.gz` and writes `inputs.csv` (records, residues) |
| `runstat.c` | Tiny launcher that times one run and reads its peak RSS from `wait4` |
| `bench.py` | Times one implementation on every input: 1 warm-up + 3 timed runs, then checks the output |
| `slurm/prepare.slurm`, `slurm/bench.slurm` | Slurm jobs: input preparation, and one array element per implementation |
| `run_benchmarks.sh` | Builds everything and submits the jobs, waiting for them to finish |
| `analyze.py` | Validates outputs, summarises, fits log-log scaling and draws `runtime_scaling.{png,svg}` |
| `slurm/profile.slurm`, `profile_summary.py` | Samples each implementation with `perf` and groups the samples into stages (`results/profile_stages.csv`) |
| `slurm/threads.slurm`, `run_threads.sh` | Times `--threads 1, 2, 4, 8` (one array per thread count, cores scaled with threads) |
| `analyze_threads.py` | Validates outputs, writes `threads_summary.csv` and draws `threads_scaling.{png,svg}` |

## Rerunning on Setonix

Biological inputs are not committed. Copy `datasets.example.json` to
`$BENCH/datasets.local.json` and replace each placeholder with a local path
(plain or gzip FASTA). Then:

```sh
export BENCH=/scratch/$PAWSEY_PROJECT/$USER/hashfasta_bench
# Rust needs a toolchain on PATH; see the repository's agent notes for Setonix.
benchmarks/run_benchmarks.sh                 # builds, submits, waits
python3 -m venv $BENCH/venv && $BENCH/venv/bin/pip install seaborn scipy
$BENCH/venv/bin/python benchmarks/analyze.py $BENCH/results
cp $BENCH/results/*.{csv,txt,png,svg} benchmarks/results/

# --threads benchmark, on the inputs prepared above
benchmarks/run_threads.sh
$BENCH/venv/bin/python benchmarks/analyze_threads.py $BENCH/threads $BENCH/results
cp $BENCH/threads/*.{csv,txt,png,svg} benchmarks/results/threads/
```

Inputs, outputs and logs stay under `$BENCH`. Each array element copies the
inputs to node-local storage when there is room and records the node, CPU,
storage and tool version in `results/environment-<impl>.txt`.
