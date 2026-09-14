#!/usr/bin/env python3
"""Validate a rendered image before it is delivered by Hermes."""

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, UnidentifiedImageError


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("debe ser un entero positivo") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("debe ser un entero positivo")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Valida formato, modo y dimensiones de una imagen renderizada."
    )
    parser.add_argument("image", type=Path)
    parser.add_argument("--format", dest="expected_format", choices=("PNG", "JPEG"))
    parser.add_argument("--mode", dest="expected_mode", choices=("RGB", "RGBA"))
    parser.add_argument("--width", type=positive_int)
    parser.add_argument("--height", type=positive_int)
    args = parser.parse_args()

    try:
        with Image.open(args.image) as image:
            image.load()
            actual = {
                "path": str(args.image),
                "format": str(image.format or "").upper(),
                "mode": image.mode,
                "width": image.width,
                "height": image.height,
            }
    except (FileNotFoundError, OSError, UnidentifiedImageError) as exc:
        print(f"no se pudo validar la imagen: {exc}", file=sys.stderr)
        return 1

    mismatches = []
    if args.expected_format and actual["format"] != args.expected_format:
        mismatches.append(f"formato {actual['format']} (esperado {args.expected_format})")
    if args.expected_mode and actual["mode"] != args.expected_mode:
        mismatches.append(f"modo {actual['mode']} (esperado {args.expected_mode})")
    if args.width and actual["width"] != args.width:
        mismatches.append(f"ancho {actual['width']} (esperado {args.width})")
    if args.height and actual["height"] != args.height:
        mismatches.append(f"alto {actual['height']} (esperado {args.height})")

    actual["ok"] = not mismatches
    print(json.dumps(actual, ensure_ascii=False))
    if mismatches:
        print("validación fallida: " + "; ".join(mismatches), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
