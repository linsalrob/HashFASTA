"""HashFASTA: FASTA records keyed by the SHA-256 of their normalized sequence.

Command line::

    hashfasta input.fasta[.gz|.bz2|.xz] [-o output.hash.fasta.gz]

Library::

    import hashfasta
    hashfasta.convert("sample.fna.gz")              # -> "sample.hash.fasta.gz"
    for rec in hashfasta.read_hashfasta("sample.hash.fasta.gz"):
        print(rec.key, rec.description, len(rec.sequence))
"""

from .core import (
    HashFastaError,
    Record,
    convert,
    convert_stream,
    default_output_path,
    escape_header,
    format_record,
    iter_records,
    normalize_sequence,
    open_input,
    read_fasta,
    read_hashfasta,
    sequence_key,
    unescape_header,
)

__version__ = "0.1.1"
SPEC_VERSION = "0.1"

__all__ = [
    "HashFastaError",
    "Record",
    "SPEC_VERSION",
    "__version__",
    "convert",
    "convert_stream",
    "default_output_path",
    "escape_header",
    "format_record",
    "iter_records",
    "normalize_sequence",
    "open_input",
    "read_fasta",
    "read_hashfasta",
    "sequence_key",
    "unescape_header",
]
