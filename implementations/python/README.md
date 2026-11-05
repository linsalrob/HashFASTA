# hashfasta (Python)

Python reference implementation of [HashFASTA](https://github.com/linsalrob/HashFASTA), both a command-line tool and a library. HashFASTA rewrites each FASTA record so that its ID is the SHA-256 of its normalized sequence and keeps the original definition line in brackets:

```text
>sha256_<64 hex digits> [<original definition line>]
```

Two different sequences that share a source ID get different keys. Identical sequences get the same key, and both records are kept. Output is always gzip. See the [specification](https://github.com/linsalrob/HashFASTA/blob/main/spec/HASHFASTA-v0.1.md).

Standard library only. Installing needs Python ≥ 3.10; the code itself also runs on Python 3.6+ straight from a checkout through `run_hashfasta.py`.

## Install

```sh
pip install "git+https://github.com/linsalrob/HashFASTA#subdirectory=implementations/python"
# or, from a checkout
pip install implementations/python
```

To run from a checkout without installing:

```sh
python3 implementations/python/run_hashfasta.py sample.fasta.gz
```

## Command line

```sh
hashfasta sample.fna.gz                    # writes sample.hash.fasta.gz
hashfasta sample.fa.bz2 -o out.hash.fasta.gz
zcat x.fa.gz | hashfasta - > x.hash.fasta.gz
python -m hashfasta sample.fasta.xz
```

Input may be plain FASTA or FASTA compressed with gzip/BGZF, bzip2 or xz. The format is detected from the content, not the file name.

## Library

```python
import hashfasta

out = hashfasta.convert("sample.fna.gz")          # "sample.hash.fasta.gz"

for rec in hashfasta.read_hashfasta(out):         # keys are re-verified by default
    print(rec.key, rec.description, len(rec.sequence))

for rec in hashfasta.read_fasta("raw.fa.xz"):     # convert in memory, record by record
    print(rec.key, rec.header)                    # header is bytes, exactly as in the source

hashfasta.sequence_key(hashfasta.normalize_sequence(b"acgt\n"))
# 'sha256_...'
```

| Function | Purpose |
| --- | --- |
| `convert(input, output=None)` | File (or `"-"`) to gzip HashFASTA; returns the output path |
| `read_fasta(source)` | Yield `Record(key, header, sequence)` from plain/gz/bz2/xz FASTA |
| `read_hashfasta(source, verify=True)` | Read a HashFASTA file back, unescaping headers |
| `convert_stream(inp, out)` | Decompressed stream to uncompressed HashFASTA text |
| `normalize_sequence`, `sequence_key` | The normalization and key rules |
| `escape_header`, `unescape_header` | Bracket escaping for the provenance field |
| `format_record`, `iter_records`, `open_input`, `default_output_path` | Lower-level building blocks |

Every error is raised as `hashfasta.HashFastaError`.

## Development

```sh
python3 -m unittest discover -s implementations/python/tests   # library tests
python3 tests/run_conformance.py python                        # shared conformance suite
python3 -m pip wheel --no-deps -w dist implementations/python  # build a wheel
```

## Releasing to PyPI

`.github/workflows/python-package.yml` builds, checks and tests the sdist and wheel on every push. To publish:

1. One-time setup: on PyPI, add a trusted publisher for project `hashfasta`, repository `linsalrob/HashFASTA`, workflow `python-package.yml` and environment `pypi`. Then create a `pypi` environment in the GitHub repository settings.
2. Bump `__version__` in `src/hashfasta/__init__.py`, merge, then push a tag matching it: `git tag python-v0.1.0 && git push origin python-v0.1.0`.
