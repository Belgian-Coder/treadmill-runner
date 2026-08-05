#!/usr/bin/env python3
"""Run dotnet-security-review scanner self-tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True


def main() -> int:
    script = Path(__file__).resolve().parent / "scanner" / "run_self_tests.py"
    completed = subprocess.run([sys.executable, "-B", str(script)], check=False)
    if completed.returncode:
        return completed.returncode
    print("dotnet-security-review self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
