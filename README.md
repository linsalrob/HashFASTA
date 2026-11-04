# HashFASTA

FASTA files can reuse an ID for different sequences. HashFASTA converts each FASTA record to a sequence-keyed FASTA record, preserving the original full header as metadata. Agents should convert FASTA before analysis so they cannot silently select the wrong molecule by a reused name.

The draft v0.1 convention writes gzip-compressed files named `*.hash.fasta.gz`. It reads plain, gzip, bzip2 or xz FASTA and always streams gzip output.

- [Draft specification v0.1](spec/HASHFASTA-v0.1.md)
- [Implementation issue](https://github.com/linsalrob/HashFASTA/issues/1)
- [Compressed test fixtures and provenance](tests/data/README.md)

The specification and fixture set are open for comment.

## Converting a FASTA file

Five interchangeable implementations produce byte-identical FASTA text (after decompression). Use whichever one runs on your system; you do not need to know the language. Python and Perl need no build step.

```sh
# Python, installed with pip (Python >= 3.9): provides the `hashfasta` command and library
pip install "git+https://github.com/linsalrob/HashFASTA#subdirectory=implementations/python"
hashfasta sample.fasta.gz

# Python 3.6+ straight from a checkout, no install (standard library only)
python3 implementations/python/run_hashfasta.py sample.fasta.gz

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
| `hashfasta x.fna.bz2` | `x.fna.bz2` | `x.hash.fasta.gz` |
| `hashfasta x.faa.xz` | `x.faa.xz` | `x.hash.fasta.gz` |
| `hashfasta x.fa -o y.hash.fasta.gz` | `x.fa` | `y.hash.fasta.gz` |
| `hashfasta - < x.fa > y.hash.fasta.gz` | stdin | stdout |
| `hashfasta x.fa -o -` | `x.fa` | stdout |

- Input may be plain FASTA or FASTA compressed with gzip/BGZF, bzip2 or xz. The format is detected from the content, not the name, and concatenated members/streams are accepted. zstd and ZIP input are rejected with an error; decompress them first.
- The default output name drops one compression suffix (`.gz`, `.bgz`, `.bz2`, `.xz`), then one FASTA suffix (`.fa`, `.fas`, `.fasta`, `.fna`, `.ffn`, `.faa`, `.frn`, `.fsa`), and adds `.hash.fasta.gz`.
- Windows line endings are removed: output never contains a carriage return.
- Output is **always gzip**. Read it with `gzip -cd x.hash.fasta.gz` or `zcat`.
- The input file is never modified. Output is written to a temporary sibling (`OUTPUT.tmp.PID`) and renamed into place only on success, so a failed run leaves no partial file.
- Errors go to stderr with a non-zero exit status: `1` for invalid input or I/O failures, `2` for usage errors. Invalid input includes an empty sequence, sequence data before the first `>`, non-ASCII or non-printable sequence bytes, an empty file, or a corrupt or truncated compressed stream.

Each output record looks like:

```text
>sha256_<SHA-256 of the normalized sequence> [<original definition line, with \ [ ] escaped>]
<normalized sequence: whitespace removed, a-z uppercased, wrapped at 80 columns>
```

The first token is the record identity. Records with the same key have identical normalized sequences, and both are kept. See the [specification](spec/HASHFASTA-v0.1.md) for the exact rules.

The Python package is also a library (`hashfasta.convert`, `read_fasta`, `read_hashfasta`, ...); see [implementations/python/README.md](implementations/python/README.md).

## Building

| Language | Requirements | Build |
| --- | --- | --- |
| Python | Python ≥ 3.6 from a checkout; ≥ 3.9 to `pip install` | none, or `pip install implementations/python` |
| Perl | Perl 5 core modules (`IO::Compress::Gzip`, `IO::Uncompress::Gunzip`, `IO::Uncompress::Bunzip2`, `Digest::SHA`); for xz, `IO::Uncompress::UnXz` (CPAN `IO-Compress-Lzma`, Debian `libio-compress-lzma-perl`) or else the `xz` command | none |
| Rust | cargo (crates `flate2`, `bzip2`, `liblzma`, `sha2`) and a C compiler for the bundled liblzma | `cargo build --release --manifest-path implementations/rust/Cargo.toml` |
| C | C99 compiler, zlib, libbz2 and liblzma headers | `make -C implementations/c` |
| C++ | C++17 compiler, zlib, libbz2 and liblzma headers | `make -C implementations/cpp` |

On Debian/Ubuntu the C and C++ build needs `zlib1g-dev libbz2-dev liblzma-dev`.

`make build` builds all three compiled versions.

## Testing

```sh
make test                                  # build, then test every implementation
python3 tests/run_conformance.py           # test every implementation (already built)
python3 tests/run_conformance.py c perl    # test selected ones
python3 tests/run_conformance.py --cmd /path/to/tool   # test any other converter
```

The shared runner `tests/run_conformance.py` runs the same checks against each tool:

- every `tests/data/*.input.fasta.gz` / `*.expected.hash.fasta.gz` pair (real crAssphage data, duplicate cases, and the `edge-*` normalization, escaping, wrapping and CRLF cases), comparing the decompressed bytes exactly;
- each input as gzip, plain, bzip2 and xz, multi-member, misnamed and on stdin, plus default output naming for every suffix combination;
- every output checked with `gzip -t` and a full decompression, and for the absence of carriage returns;
- every `tests/data/invalid/` input, zstd/ZIP input and truncated gzip, bzip2 and xz must fail without leaving an output file;
- a cross-check that all implementations give byte-identical output on seeded random FASTA.

`python3 tests/make_edge_fixtures.py` regenerates the edge and invalid fixtures from the specification's rules. `python3 -m unittest discover -s implementations/python/tests` runs the Python library's unit tests.

The input files are checksummed before and after each run to confirm they were not modified. CI runs `make test` (`.github/workflows/conformance.yml`) and builds, checks and tests the Python package (`.github/workflows/python-package.yml`) on every push and pull request.
