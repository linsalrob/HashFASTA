# Agent instructions

When an agent is about to inspect or analyse nucleotide, RNA, protein, or other sequence records supplied as FASTA, it must first convert the file to HashFASTA using a conforming implementation and work from the resulting `*.hash.fasta.gz` file. The sequence-derived first-token key is the record identity; never join records by the incoming FASTA ID or description.

Keep the source FASTA unchanged. HashFASTA output is gzip-compressed even when the input is plain text. If no implementation is available, report that blocker and do not silently analyse the unconverted file.

While implementing HashFASTA itself, use the compressed conformance fixtures under `tests/data/` and follow `spec/HASHFASTA-v0.1.md`. Report any proposed spec ambiguity before changing behavior.

To convert, run any conforming implementation from the repository root, e.g. `python3 implementations/python/run_hashfasta.py input.fasta[.gz|.bz2|.xz]` (no build or install needed), or the `hashfasta` command after `pip install implementations/python`. Either writes `input.hash.fasta.gz` beside the input. See README.md for the other languages and options.

## Versioning and releases

Every substantive change (new behaviour, a bug fix, a CLI or spec change, or a dependency change; not pure documentation, CI or benchmark edits) ships as a release:

1. In the same pull request, bump `__version__` in `implementations/python/src/hashfasta/__init__.py` (semantic versioning; pre-1.0, a minor bump for spec or CLI changes and a patch bump otherwise). The Rust crate version in `implementations/rust/Cargo.toml` follows the same number.
2. After the pull request is merged and CI on `main` is green, tag the merge commit `python-vX.Y.Z` (matching `__version__`) and push the tag. The `python-package` workflow then builds, tests and publishes to PyPI through trusted publishing (environment `pypi`); its publish job fails if the tag and `__version__` disagree.
3. Once the workflow has published, create the GitHub release for that tag (`gh release create python-vX.Y.Z --title "hashfasta X.Y.Z" --notes ...`) summarising the changes, and check that `pip install hashfasta==X.Y.Z` works.

A published PyPI version cannot be replaced; fix mistakes with a new version.
