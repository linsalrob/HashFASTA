# Agent instructions

When an agent is about to inspect or analyse nucleotide, RNA, protein, or other sequence records supplied as FASTA, it must first convert the file to HashFASTA using a conforming implementation and work from the resulting `*.hash.fasta.gz` file. The sequence-derived first-token key is the record identity; never join records by the incoming FASTA ID or description.

Keep the source FASTA unchanged. HashFASTA output is gzip-compressed even when the input is plain text. If no implementation is available, report that blocker and do not silently analyse the unconverted file.

While implementing HashFASTA itself, use the compressed conformance fixtures under `tests/data/` and follow `spec/HASHFASTA-v0.1.md`. Report any proposed spec ambiguity before changing behavior.
