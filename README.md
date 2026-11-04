# HashFASTA

FASTA files can reuse an ID for different sequences. HashFASTA converts each FASTA record to a sequence-keyed FASTA record, preserving the original full header as metadata. Agents should convert FASTA before analysis so they cannot silently select the wrong molecule by a reused name.

The draft v0.1 convention writes gzip-compressed files named `*.hash.fasta.gz`. It reads compressed or uncompressed FASTA and streams gzip output by default.

- [Draft specification v0.1](spec/HASHFASTA-v0.1.md)
- [Implementation issue](https://github.com/linsalrob/HashFASTA/issues/1)
- [Compressed test fixtures and provenance](tests/data/README.md)

The specification and fixture set are open for comment. This repository currently contains the draft and conformance data; implementations are tracked in the issue.
