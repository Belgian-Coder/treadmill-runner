#!/usr/bin/env python3
"""CLI wrapper for Mermaid diagram validation.

The implementation keeps the explicit --no-auto-install-mmdc opt-out.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from mermaid_support.validation_impl import *
from mermaid_support.validation_impl import main


if __name__ == "__main__":
    raise SystemExit(main())
