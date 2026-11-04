# HashFASTA format specification

**Draft v0.1 — for comment (4 November 2026; revised 5 November 2026)**

This document proposes a deterministic, compressed FASTA representation in which sequence content, rather than an incoming FASTA identifier, supplies the record key. It is intended to prevent silent misjoins when source files reuse the same identifier for different sequences. This is a draft, not a final standard.

## 1. File naming and compression

A HashFASTA file uses the suffix `.hash.fasta.gz`, for example:

```text
sample.hash.fasta.gz
```

The file contains ordinary FASTA text compressed as a standard gzip stream (RFC 1952). Implementations **MUST** write gzip-compressed output, whether the input was compressed or not.

Implementations **MUST** read uncompressed FASTA and FASTA compressed with gzip (including BGZF), bzip2 or xz. The input format is detected from its leading magic bytes, never from its filename:

| Input | Leading bytes |
| --- | --- |
| gzip / BGZF | `1F 8B` |
| bzip2 | `42 5A 68` (`BZh`) |
| xz | `FD 37 7A 58 5A 00` |
| anything else | read as uncompressed FASTA |

Concatenated gzip members, bzip2 streams and xz streams **MUST** be read as one continuous input. Input beginning with the signature of an unsupported compression format, zstd (`28 B5 2F FD`) or ZIP (`50 4B 03 04`), **MUST** be rejected with an error naming that format; it must not be parsed as FASTA. A truncated or corrupt compressed stream is an error.

Gzip is chosen because it is streamable, available in the standard or widely used libraries of the target languages, and accepted by many downstream sequence tools. HashFASTA does not define a new compression codec. Output is one valid gzip stream; compressed bytes need not be identical between implementations.

The converter **MUST NOT** write a whole uncompressed intermediate file. It may retain one FASTA record in memory while calculating its key. Standard input and output are supported: `-` means stdin or stdout. Standard output is gzip-compressed too. A file-to-file conversion must not overwrite the input.

## 2. Record identifier and retained header

Output lines end with a line feed (LF, `0x0A`) only. HashFASTA output never contains a carriage return (CR, `0x0D`). Every CR byte is deleted from the original definition line before it is escaped, wherever it occurs; in sequence lines CR is whitespace (section 3). Windows (CRLF) and classic Mac line endings in the input therefore leave no trace in the output.

For each input FASTA record, replace the original definition line with:

```text
>sha256_<64 lowercase hexadecimal digits> [<escaped original definition line>]
```

The digest key is the first whitespace-delimited token. Records with different normalized sequences have different sequence-derived IDs; records with identical normalized sequences share the same key. The complete original definition line (the text after the original `>`, including its original ID and description) is retained in square brackets for provenance.

Within the bracketed original definition line, escape backslash as `\\`, `[` as `\[`, and `]` as `\]`. A conforming reader reverses these escapes to recover the original header (with CR bytes removed). No other header text is changed: tabs, `>` and non-ASCII bytes are retained.

Example:

```text
>sha256_9c56cc51b374c3ba189210d5b6d4bf57790d351c96c47c02190ecf1e430635ab [contig_7 hypothetical phage protein]
ACGT...
```

The example digest is illustrative only.

## 3. Sequence normalization and digest

For hashing, concatenate the sequence lines of one FASTA record, remove ASCII whitespace (space, horizontal tab, carriage return, line feed, vertical tab, and form feed), and convert ASCII lowercase letters `a`–`z` to uppercase. Hash the resulting ASCII bytes with the full SHA-256 digest.

Implementations **MUST NOT** otherwise alter sequence characters. In particular, ambiguity codes, `U`, `T`, `-`, `.`, and terminal `*` are retained as supplied. HashFASTA does not reverse-complement nucleotides, rotate circular sequences, translate sequences, remove gaps, or infer whether a file contains DNA, RNA, or protein. This keeps the conversion applicable to any FASTA alphabet and makes the key identify the normalized character string.

A sequence containing non-ASCII or non-printable, non-whitespace bytes is invalid and **MUST** cause a clear error; after whitespace removal only bytes `0x21`–`0x7E` are valid. Empty sequences (including whitespace-only sequences) are invalid. Lines before the first definition line may be blank or whitespace-only; any other content there is invalid. Input containing no records at all, including an empty file, is invalid. The converter **MUST NOT** silently skip malformed records.

The output sequence is the normalized sequence and is wrapped at 80 characters per line. Hashing is over the unwrapped normalized sequence, not over line breaks.

## 4. Duplicate records

Different normalized sequences produce different SHA-256 keys, even if their source IDs are identical. This is the main collision case HashFASTA is designed to prevent.

If two input records have identical normalized sequence strings, they receive the same key. Version 0.1 preserves both records, each with its own original header; it does not deduplicate records. Repeated keys therefore mean the normalized sequence is identical, while different keys mean sequence strings differ (subject to the standard cryptographic assumptions of SHA-256).

## 5. Command-line behavior

The reference interface is:

```sh
hashfasta input.fasta[.gz] [-o output.hash.fasta.gz]
```

- If `-o` is omitted for a named input, write a sibling file whose name ends in `.hash.fasta.gz`. First remove one trailing compression suffix (`.gz`, `.bgz`, `.bz2`, or `.xz`), then one recognized FASTA suffix (`.fa`, `.fas`, `.fasta`, `.fna`, `.ffn`, `.faa`, `.frn`, or `.fsa`), then append `.hash.fasta.gz`. Suffix matching is case-sensitive. For example, `x.fna.bz2`, `x.fna.xz` and `x.fna` all become `x.hash.fasta.gz`, and `x.txt` becomes `x.txt.hash.fasta.gz`.
- If input is `-`, output defaults to stdout. An explicit `-o -` also selects stdout.
- Output is always gzip-compressed in this draft. To read the resulting FASTA text, use any gzip reader (for example, `gzip -cd sample.hash.fasta.gz`).
- Errors go to stderr and produce a non-zero exit status (`1` for invalid input or I/O failure, `2` for usage errors). A failed file-to-file conversion must not leave a partial output file.

Language implementations may provide additional options, but the behavior above is the interoperable baseline.

## 6. Conformance tests

Implementations must produce decompressed FASTA text byte-for-byte identical to the expected fixtures in `tests/data/`, including record order, keys, escaped source headers, sequence case, wrapping, and line endings. Gzip container bytes are not compared. Each implementation must also validate output with a gzip reader.

Fixtures include:

- crAssphage nucleotide genome JQ995537.1 (97,065 bp);
- all 80 translated CDS records from the archived GenBank record (29,522 amino-acid residues total);
- two different sequences sharing the same original FASTA ID;
- identical sequences with different original headers, which intentionally retain the same hash key;
- edge cases of normalization, wrapping, header escaping and line endings (`tests/data/edge-*`), generated from this specification by `tests/make_edge_fixtures.py`;
- invalid inputs under `tests/data/invalid/`, each of which **MUST** be rejected with a non-zero exit status and no output file.

All test sequence files are gzip-compressed. Tests must additionally feed uncompressed, bzip2 and xz copies of each input fixture, including multi-stream copies, and confirm that output is still gzip-compressed and has identical decompressed content.

The source record and fixture provenance are documented in `tests/data/README.md`. The expected files are golden vectors for draft v0.1; any change to normalization or headers requires updating this specification and the vectors together.

## 7. Draft status and points for comment

Before v1.0, comments are especially welcome on header escaping, whether preserving duplicate identical records is the right default, the output suffix, and whether any additional FASTA alphabets or suffixes need explicit handling. Do not silently implement a different normalization rule: propose a specification change and add or revise conformance vectors.
