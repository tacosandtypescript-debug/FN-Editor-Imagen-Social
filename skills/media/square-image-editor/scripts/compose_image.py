#!/usr/bin/env python3
"""Entrada cuadrada compatible con el compositor canónico de EditImg."""

from pathlib import Path
import runpy


ROOT = Path(__file__).resolve().parents[4]
CANONICAL = ROOT / "bin" / "compose_image.py"

if not CANONICAL.is_file():
    raise SystemExit(f"no se encontró el compositor canónico: {CANONICAL}")

runpy.run_path(str(CANONICAL), run_name="__main__")
