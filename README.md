# HashFASTA

FASTA files can reuse an ID for different sequences. HashFASTA converts each FASTA record to a sequence-keyed FASTA record, preserving the original full header as metadata. Agents should convert FASTA before analysis so they cannot silently select the wrong molecule by a reused name.

The draft v0.1 convention writes gzip-compressed files named `*.hash.fasta.gz`. It reads compressed or uncompressed FASTA and streams gzip output by default.

- [Draft specification v0.1](spec/HASHFASTA-v0.1.md)
- [Implementation issue](https://github.com/linsalrob/HashFASTA/issues/1)
- [Compressed test fixtures and provenance](tests/data/README.md)

The specification and fixture set are open for comment.

## Converting a FASTA file

Five interchangeable implementations produce byte-identical FASTA text (after decompression). Use whichever one runs on your system; you do not need to know the language. Python and Perl need no build step.

```sh
# Python 3.6+ (standard library only)
python3 implementations/python/hashfasta.py sample.fasta.gz

# Perl 5 (core modules only)
perl implementations/perl/hashfasta.pl sample.fasta.gz

# Compiled versions (build first, see below)
implementations/rust/target/release/hashfasta sample.fasta.gz
implementations/c/hashfasta sample.fasta.gz
implementations/cpp/hashfasta sample.fasta.gz
```

Each of these writes `sample.hash.fasta.gz` beside the input. Every implementation has the same interface:

```text
hashfasta INPUT [-o OUTPUT]
```

| Invocation | Reads | Writes |
| --- | --- | --- |
| `hashfasta x.fasta` | `x.fasta` | `x.hash.fasta.gz` |
| `hashfasta x.fa.gz` | `x.fa.gz` | `x.hash.fasta.gz` |
| `hashfasta x.fa -o y.hash.fasta.gz` | `x.fa` | `y.hash.fasta.gz` |
| `hashfasta - < x.fa > y.hash.fasta.gz` | stdin | stdout |
| `hashfasta x.fa -o -` | `x.fa` | stdout |

- Input may be plain or gzip-compressed FASTA (detected from the content, not the name). Concatenated gzip members are accepted.
- Output is **always gzip**. Read it with `gzip -cd x.hash.fasta.gz` or `zcat`.
- The input file is never modified. Output is written to a temporary sibling (`OUTPUT.tmp.PID`) and renamed into place only on success, so a failed run leaves no partial file.
- Errors go to stderr with a non-zero exit status: `1` for invalid input or I/O failures, `2` for usage errors. Invalid input includes an empty sequence, sequence data before the first `>`, non-ASCII or non-printable sequence bytes, an empty file, or a corrupt or truncated gzip stream.

Each output record looks like:

```text
>sha256_<SHA-256 of the normalized sequence> [<original definition line, with \ [ ] escaped>]
<normalized sequence: whitespace removed, a-z uppercased, wrapped at 80 columns>
```

The first token is the record identity. Records with the same key have identical normalized sequences, and both are kept. See the [specification](spec/HASHFASTA-v0.1.md) for the exact rules.

## Building

| Language | Requirements | Build |
| --- | --- | --- |
| Python | Python ≥ 3.6 | none |
| Perl | Perl 5 with core `IO::Compress::Gzip`, `IO::Uncompress::Gunzip`, `Digest::SHA` | none |
| Rust | cargo (crates `flate2`, `sha2`) | `cargo build --release --manifest-path implementations/rust/Cargo.toml` |
| C | C99 compiler, zlib headers | `make -C implementations/c` |
| C++ | C++17 compiler, zlib headers | `make -C implementations/cpp` |

`make build` builds all three compiled versions.

## Testing

```sh
make test                                  # build, then test every implementation
python3 tests/run_conformance.py           # test every implementation (already built)
python3 tests/run_conformance.py c perl    # test selected ones
python3 tests/run_conformance.py --cmd /path/to/tool   # test any other converter
```

The shared runner `tests/run_conformance.py` runs the same checks against each tool:

- every `tests/data/*.input.fasta.gz` / `*.expected.hash.fasta.gz` pair, comparing the decompressed bytes exactly;
- gzip, plain, stdin and multi-member gzip inputs, and default output naming;
- every output checked with `gzip -t` and a full decompression;
- supplementary normalization and escaping cases, and error handling;
- a cross-check that all implementations give byte-identical output on seeded random FASTA.

The input files are checksummed before and after each run to confirm they were not modified. CI (`.github/workflows/conformance.yml`) runs `make test` on every push and pull request.
