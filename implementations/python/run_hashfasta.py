#!/usr/bin/env python3
"""Run the HashFASTA CLI from a source checkout without installing it.

    python3 implementations/python/run_hashfasta.py INPUT [-o OUTPUT]

Equivalent to the ``hashfasta`` command installed by ``pip install``.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from hashfasta.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
