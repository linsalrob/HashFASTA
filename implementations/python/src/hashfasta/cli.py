"""``hashfasta INPUT [-o OUTPUT]`` command-line interface."""

import sys

from . import __version__
from .core import HashFastaError, convert

PROG = "hashfasta"
USAGE = "usage: %s INPUT [-o OUTPUT]   ('-' means stdin/stdout)\n" % PROG


def parse_args(argv):
    """Return (input, output) or None for a usage error. Exits for -h/--version."""
    input_path = None
    output_path = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            sys.stdout.write(USAGE)
            sys.exit(0)
        if arg == "--version":
            sys.stdout.write("%s %s\n" % (PROG, __version__))
            sys.exit(0)
        if arg == "-o":
            if i + 1 >= len(argv):
                return None
            output_path = argv[i + 1]
            i += 2
            continue
        if (arg != "-" and arg.startswith("-")) or input_path is not None:
            return None
        input_path = arg
        i += 1
    if input_path is None:
        return None
    return input_path, output_path


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args is None:
        sys.stderr.write(USAGE)
        return 2
    try:
        convert(*args)
    except HashFastaError as e:
        sys.stderr.write("%s: error: %s\n" % (PROG, e))
        return 1
    return 0
