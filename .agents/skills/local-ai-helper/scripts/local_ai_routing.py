#!/usr/bin/env python3
"""Public entrypoint for repo-local AI routing helpers."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from local_ai_support.routing_impl import *
from local_ai_support.routing_impl import main


if __name__ == "__main__":
    raise SystemExit(main())
