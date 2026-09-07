#!/usr/bin/env python3
"""Fetch all media from a link and compose them as one output card."""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from fetch_media import download_link


ROOT = Path(__file__).resolve().parents[1]
COMPOSER = ROOT / "bin" / "compose_image.py"
DEFAULT_PRESET = ROOT / "bin" / "preset.json"


def main():
    parser = argparse.ArgumentParser(
        description="Descarga todas las imágenes de un post y crea la composición final."
    )
    parser.add_argument("url", help="URL de X/Twitter o URL directa de imagen")
    parser.add_argument("output", type=Path, help="PNG/JPEG de salida")
    parser.add_argument("--top", required=True)
    parser.add_argument("--bottom", required=True)
    parser.add_argument("--preset", type=Path, default=DEFAULT_PRESET)
    parser.add_argument("--style", default="auto")
    parser.add_argument("--format", dest="output_format", default=None)
    parser.add_argument("--fit", choices=("auto", "cover", "contain"), default="auto")
    parser.add_argument("--max-images", type=int, default=24)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="editimg-media-") as temporary:
        try:
            media = download_link(args.url, temporary, args.max_images)
        except Exception as exc:
            parser.error(str(exc))
        input_paths = [item["path"] for item in media]
        command = [
            sys.executable,
            str(COMPOSER),
            *input_paths,
            str(args.output),
            "--top",
            args.top,
            "--bottom",
            args.bottom,
            "--preset",
            str(args.preset),
            "--style",
            args.style,
            "--fit",
            args.fit,
        ]
        if args.output_format:
            command.extend(["--format", args.output_format])
        completed = subprocess.run(command, check=False)
        raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
