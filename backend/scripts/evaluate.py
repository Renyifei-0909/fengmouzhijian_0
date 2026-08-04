#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
