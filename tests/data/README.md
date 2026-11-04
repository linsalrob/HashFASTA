# Conformance fixtures

All FASTA input and expected-output sequence files in this directory are gzip-compressed. Expected outputs should be compared after decompression; gzip container bytes may differ across libraries.

## crAssphage reference

The nucleotide input is accession JQ995537.1, the 97,065 bp crAssphage genome. The protein input contains all 80 `/translation` qualifiers for annotated CDS features in the archived GenBank record (29,522 residues total). The GenBank record was preserved at [linsalrob/crAssphage/JQ995537.gbk](https://github.com/linsalrob/crAssphage/blob/master/JQ995537.gbk), after the original record became unavailable at NCBI. The original publication is Dutilh et al., *Nature Communications* 5, 4498 (2014), [doi:10.1038/ncomms5498](https://doi.org/10.1038/ncomms5498).

For both molecule sets, `*.input.fasta.gz` is the source FASTA and `*.expected.hash.fasta.gz` is the HashFASTA v0.1 output.

## Synthetic regression cases

- `duplicate-id.*`: two different sequences share the original first-token ID; expected output must give them different SHA-256 keys.
- `duplicate-sequence.*`: identical sequences have different original headers; expected output preserves both records and gives them the same key.

Implementations must also generate an uncompressed copy of an input fixture at runtime and verify the converter still emits gzip-compressed output with the same decompressed bytes.
