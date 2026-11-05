# HashFASTA runtime benchmark

Wall-clock time, throughput and peak memory of the five implementations
converting DNA and protein FASTA of increasing size (issue #3). All numbers
come from [`results/`](results/); the scripts that produced them are listed in
[`README.md`](README.md).

![Median wall-clock time against input size, DNA left, protein right](results/runtime_scaling.png)

([SVG](results/runtime_scaling.svg))

## Method

- **Code:** commit `5e4b4ac` (main after PR #2), built with `make build`.
- **Inputs:** 6 DNA and 6 protein inputs, each a gzip FASTA (level 6) built by
  `prepare_inputs.py` ([`results/inputs.csv`](results/inputs.csv)).
  - DNA ranges from the 97 kbp crAssphage fixture, through E. coli K-12, a set
    of 8 bacterial genomes and Arabidopsis thaliana, to human GRCh38: chr1–4
    (880 Mbp) and the complete primary assembly (3.10 Gbp, 194 records).
  - Protein ranges from the 80 crAssphage CDS translations, through the
    P. aeruginosa PAO1 proteome, AMRFinderPlus reference proteins and the
    A. thaliana proteome, to the BUSCO vertebrata_odb10 RefSeq proteins: a
    75 M-residue prefix and the complete set (296 M residues, 510,639 records).
  - The human genome was already on the system, so nothing was downloaded.
    Source paths stay in an uncommitted local file;
    [`datasets.example.json`](datasets.example.json) shows the layout.
- **Allocation:** one Slurm array element per implementation (job 52209219), on
  the Setonix `work` partition (AMD EPYC 7763), with identical requests:
  - 24 GB memory, 1 task. Slurm raised the 2 requested CPUs to 14 cores to
    match the memory, so every implementation had the same 14 cores.
  - Every converter is single-threaded: for runs over 1 s, median CPU time is
    within 0.2% of wall time.
  - Inputs were copied to node-local storage first, and outputs were written
    there.
- **Runs:** for each input, 1 warm-up and then 3 timed runs.
  - A small C launcher (`runstat.c`) measures wall time with
    `CLOCK_MONOTONIC` around the converter process alone, and reads CPU time
    and peak RSS from `wait4`.
  - Each output was decompressed and hashed with SHA-256 after its run, outside
    the timed interval.
- **Validation:** every run of every input, across all 5 implementations,
  produced byte-identical decompressed output (`analyze.py` checks this before
  summarising).
- **Toolchains:**
  - C: gcc 14.3.0.
  - C++: the system `c++`, GCC 7.5.0. This is what the Makefile picks up, so
    C++ was not built with the newer compiler C used.
  - Rust: rustc 1.96.0.
  - Python: 3.11.17.
  - Perl: 5.26.1.

## Results at the largest inputs

Median of 3 timed runs. Throughput is input residues per second of wall time.

| Implementation | Human GRCh38 (3.10 Gbp) | Mbp/s | Peak RSS | BUSCO vertebrata (296 M aa) | M aa/s | Peak RSS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Rust | 390 s | 7.9 | 240 MiB | 15.1 s | 19.6 | ≤ 4 MiB |
| C | 504 s | 6.1 | 241 MiB | 11.3 s | 26.1 | ≤ 4 MiB |
| Python | 558 s | 5.6 | 1383 MiB | 14.3 s | 20.7 | 17 MiB |
| C++ | 571 s | 5.4 | 245 MiB | 13.1 s | 22.5 | ≤ 4 MiB |
| Perl | 815 s | 3.8 | 489 MiB | 31.0 s | 9.6 | 12 MiB |

Every implementation, input and run is in
[`results/summary.csv`](results/summary.csv). For runs over 1 s, the 3 timed
runs spanned under 2% of the median in 26 of 36 cells and at most 7% (Rust,
full human).

Observations:

- **DNA:** Rust is fastest, about 1.3× faster than C. C, Python and C++ fall
  within 13% of each other, and Perl is about 1.6× slower than C. Python keeps
  up because its hashing, decompression and compression run in CPython's C
  extensions.
- **Protein:** the order changes. C leads, and Rust is slowest of the compiled
  implementations. Protein inputs have many short records, so per-record
  overhead matters more than per-base speed. This benchmark does not isolate
  which per-record step costs Rust the most.
- **Memory follows the longest record, not the file.** Every output header
  carries the hash of the whole sequence, so each implementation holds one
  record in memory.
  - Human chr1 has 249 Mbp. The compiled implementations peak near 1 byte per
    base of it (≈ 240 MiB), Perl near 2 bytes and Python near 5.6 bytes.
  - Python's extra copies come from joining lines, uppercasing and hashing.
  - Peak memory on the 3.10 Gbp genome equals that on chr1–4, and protein
    inputs stay at a few MiB however large the file is.

## Empirical scaling

These are least-squares fits of log10(median time) on log10(residues), with a
95% confidence interval on the slope from the t distribution
([`results/scaling_fits.csv`](results/scaling_fits.csv)). They describe the
inputs measured here. They are not a complexity proof.

| Panel | Implementation | Slope, inputs ≥ 10⁶ (95% CI), n = 5 | Slope, all inputs (95% CI), n = 6 |
| --- | --- | --- | --- |
| DNA | C | 0.98 (0.97–1.00) | 0.99 (0.98–1.00) |
| DNA | C++ | 0.98 (0.97–1.00) | 0.98 (0.98–0.99) |
| DNA | Rust | 0.98 (0.96–1.00) | 0.98 (0.97–0.99) |
| DNA | Python | 0.98 (0.97–0.99) | 0.91 (0.83–0.99) |
| DNA | Perl | 0.98 (0.97–1.00) | 0.91 (0.83–0.99) |
| Protein | C | 0.92 (0.78–1.05) | 0.88 (0.81–0.95) |
| Protein | C++ | 0.91 (0.78–1.04) | 0.86 (0.78–0.94) |
| Protein | Rust | 0.92 (0.82–1.03) | 0.89 (0.84–0.95) |
| Protein | Python | 0.83 (0.66–1.00) | 0.66 (0.47–0.84) |
| Protein | Perl | 0.81 (0.64–0.97) | 0.69 (0.55–0.83) |

- **DNA:** time grows linearly with size for every implementation.
- **The smallest inputs pull the all-input fits down.** Start-up cost
  dominates there: interpreter launch is about 35–70 ms for Python and Perl,
  against under 25 ms for the compiled programs. That is why the figure draws
  the fit over inputs ≥ 10⁶ only.
- **Protein slopes sit below 1** because the protein inputs differ in record
  length as well as count. Average throughput rises from the bacterial sets to
  the vertebrate proteins, and the intervals are wide with only 5 points. The
  confidence intervals include 1 for C, C++ and Rust. For Python and Perl the
  upper bounds are 0.995 and 0.97, which reflects their larger fixed cost per
  run and the throughput change between datasets, not a sub-linear algorithm.

## Caveats

- **Peak RSS for small runs is unreliable.** On this kernel (6.4) `ru_maxrss`
  comes from approximate per-CPU counters, so it can read up to a few MiB low.
  It reads 0 for some short C and Rust runs, which is why the table says
  "≤ 4 MiB". The large-input values are well above this error.
- **One node per implementation.** Rust and Python ran on the same node,
  while C, C++ and Perl each had a different node. All were the same CPU model,
  partition and allocation, and spread between repetitions was small, but
  node-to-node variation is not measured separately.
- **Different compiler generations.** C used gcc 14 and C++ used GCC 7.5. The
  C++ result reflects the default build, not the best possible C++ build.

## Parallel compression (`--threads`, issue #5)

The single-threaded results above are for version 0.1. Version 0.2 adds
`--threads N`, which compresses the output on N worker threads while the main
thread keeps parsing and hashing. Perl accepts the option but stays
single-threaded, so it is not benchmarked here.

![Speedup against compression threads for three inputs](results/threads/threads_scaling.png)

([SVG](results/threads/threads_scaling.svg) · raw data and summary in
[`results/threads/`](results/threads/))

**Why compression.** Before implementing anything, I profiled each converter
with `perf` (`slurm/profile.slurm`, `profile_summary.py`,
[`results/profile_stages.csv`](results/profile_stages.csv)). Output gzip
compression took 83–90% of CPU time on human chr1–4 for C, C++, Rust and
Python, and 62% for Perl. On the BUSCO proteins it took 50–64%, and 27% for
Perl. Input decompression and SHA-256 were small by comparison.

**Method.**
- **Code:** commit `d792e03`. Inputs: A. thaliana (120 Mbp), human chr1–4
  (880 Mbp) and BUSCO vertebrata (296 M aa).
- **Runs:** for each implementation and N = 1, 2, 4 and 8, the same 1 warm-up
  and 3 timed runs as above.
- **Allocation:** one Slurm array per N (jobs 52228378–52228381), each
  requesting `--cpus-per-task=2N` and 1840 MB per requested CPU. Slurm
  allocated 2N cores and 3.68 GB per compression thread, the same per thread
  at every N.
- **Compression level:** unchanged at 6.
- **Validation:** every run, at every thread count and in every
  implementation, produced decompressed output identical to the
  single-threaded benchmark.

Median wall time, with speedup over 1 thread in brackets
([`threads_summary.csv`](results/threads/threads_summary.csv)):

| Implementation | Human chr1–4, 1 thread | 2 | 4 | 8 | BUSCO vertebrata, 1 thread | 2 | 4 | 8 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| C | 168 s | 87 s (1.9×) | 47 s (3.6×) | 29 s (5.9×) | 13.2 s | 5.4 s (2.4×) | 2.8 s (4.7×) | 4.1 s (3.2×)* |
| C++ | 169 s | 88 s (1.9×) | 48 s (3.5×) | 31 s (5.5×) | 13.6 s | 5.0 s (2.7×) | 3.1 s (4.4×) | 4.2 s (3.2×)* |
| Rust | 119 s | 59 s (2.0×) | 32 s (3.7×) | 19 s (6.1×) | 14.3 s | 4.9 s (2.9×) | 2.6 s (5.5×) | 2.6 s (5.6×) |
| Python | 189 s | 109 s (1.7×) | 66 s (2.9×) | 40 s (4.7×) | 16.9 s | 6.9 s (2.4×) | 5.4 s (3.1×) | 5.2 s (3.2×) |

\* Not reproducible; see the protein observations below.

Observations:

- **DNA scales close to linearly up to 8 threads.** Parallel efficiency at 8
  threads was 76% (Rust), 73% (C), 69% (C++) and 59% (Python) on human chr1–4.
  A. thaliana gave the same picture. That leaves about 30 s instead of 170 s
  for 880 Mbp in C, and about 20 s in Rust.
- **Protein levels off after 4 threads, because the main thread becomes the
  limit.**
  - Parsing, normalising and hashing hundreds of thousands of short records
    stays serial. For C, total CPU time at 4 threads (12.7 s) equals the
    single-threaded run, but wall time is 2.8 s, so the serial part is about
    2.8 s.
  - Speedups at 2 threads exceed 2× because the main thread's own work now
    overlaps with compression: N workers plus the main thread are N + 1 busy
    threads.
- **The C and C++ protein slowdown at 8 threads (\*) is node variation, not
  an effect of the code.**
  - Those runs varied from 3.8 to 6.0 s.
  - Repeating 4, 8 and 16 threads alternately on a single node gave the same
    time at all three counts: about 3.0 s for C, 3.3 s for C++ and 2.8–3.8 s
    for Rust.
- **Memory is essentially unchanged.** The parallel writer holds 2N blocks of
  128 KiB in flight.
- **Compressed size.**
  - C, C++ and Python prime each block with the previous 32 KiB, so their
    output is 0.1% larger than single-threaded.
  - Rust's flate2 backend (miniz_oxide) cannot set a preset dictionary, so its
    blocks are independent: 0.7% larger for DNA and 3.3% larger for protein.
- **Single-threaded Rust is faster on protein in 0.2.** A buffer in front of
  flate2's encoder avoids zero-filling its output buffer on every 80-byte write
  (rust-lang/flate2-rs#395). Rust's single-threaded protein time fell from
  15.1 s (version 0.1, above) to 14.3 s here. DNA is unchanged within
  node-to-node variation (114 s above, 119 s here).
