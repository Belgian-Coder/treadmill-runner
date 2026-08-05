#!/usr/bin/env python3
"""Public entrypoint for local AI setup commands."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

if __name__ == "__main__":
    from local_ai_support.setup_impl import main

    raise SystemExit(main())


from local_ai_support.setup_impl import *
from local_ai_support.setup_impl import main
