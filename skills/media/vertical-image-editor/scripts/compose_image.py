#!/usr/bin/env python3
"""Thin entry point for the repository's canonical image compositor.

Keeping this path as a wrapper prevents the skill and the standalone CLI from
drifting apart while preserving the command documented by the skill.
"""
from pathlib import Path
import runpy


ROOT = Path(__file__).resolve().parents[4]
CANONICAL = ROOT / "bin" / "compose_image.py"

if not CANONICAL.is_file():
    raise SystemExit(f"no se encontró el compositor canónico: {CANONICAL}")

runpy.run_path(str(CANONICAL), run_name="__main__")
