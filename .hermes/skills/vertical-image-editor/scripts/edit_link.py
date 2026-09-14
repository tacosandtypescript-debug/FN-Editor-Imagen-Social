#!/usr/bin/env python3
"""Fetch all media from a link and compose them as one output card."""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

from compose_image import STYLES, infer_output_format, positive_int
from fetch_media import download_link_info
from runtime_config import DEFAULT_MAX_IMAGES


SKILL_ROOT = Path(__file__).resolve().parents[1]
COMPOSER = SKILL_ROOT / "scripts" / "compose_image.py"
DEFAULT_PRESET = SKILL_ROOT / "references" / "presets" / "fortnite_vertical_image.json"
AUTO_PRESETS = {
    "9:16": SKILL_ROOT / "references" / "presets" / "fortnite_vertical_image.json",
    "1:1": SKILL_ROOT / "references" / "presets" / "fortnite_square_image.json",
    "16:9": SKILL_ROOT / "references" / "presets" / "fortnite_horizontal_image.json",
}


def validate_rendered_output(path, compose_metadata):
    """Validate the file produced by the compositor before returning success."""
    expected_by_suffix = {
        ".png": ("PNG", "RGBA"),
        ".jpg": ("JPEG", "RGB"),
        ".jpeg": ("JPEG", "RGB"),
    }
    expected = expected_by_suffix.get(path.suffix.lower())
    if expected is None:
        raise ValueError("la salida debe tener extension .png, .jpg o .jpeg")
    try:
        with Image.open(path) as image:
            image.load()
            actual = {
                "format": str(image.format or "").upper(),
                "mode": image.mode,
                "width": image.width,
                "height": image.height,
            }
    except OSError as exc:
        raise ValueError(f"no se pudo validar la salida generada: {exc}") from exc

    mismatches = []
    if actual["format"] != expected[0]:
        mismatches.append(f"formato {actual['format']} (esperado {expected[0]})")
    if actual["mode"] != expected[1]:
        mismatches.append(f"modo {actual['mode']} (esperado {expected[1]})")
    for key in ("width", "height"):
        expected_value = compose_metadata.get(key)
        if actual[key] != expected_value:
            mismatches.append(f"{key} {actual[key]} (esperado {expected_value})")
    if mismatches:
        raise ValueError("validacion de salida fallida: " + "; ".join(mismatches))
    return actual


def build_composer_command(args, input_paths):
    """Build the canonical compositor command from the link CLI options."""
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
        "--backend",
        getattr(args, "backend", "auto"),
        "--resolution",
        getattr(args, "resolution", "4k"),
        "--max-images",
        str(args.max_images),
    ]
    if args.output_format:
        command.extend(["--format", args.output_format])
    if args.background:
        command.extend(["--background", str(args.background)])
    return command


def select_auto_preset(args, link_info):
    """Select the matching bundled preset when the output format is auto."""
    if str(getattr(args, "output_format", "") or "").strip().lower() != "auto":
        return args.preset
    sizes = [
        (item["width"], item["height"])
        for item in link_info["images"]
        if item.get("width") and item.get("height")
    ]
    if not sizes:
        return args.preset
    resolved_format = infer_output_format(sizes)
    try:
        is_default_preset = Path(args.preset).resolve() == DEFAULT_PRESET.resolve()
    except OSError:
        is_default_preset = False
    if is_default_preset:
        return AUTO_PRESETS[resolved_format]
    return args.preset


def main():
    parser = argparse.ArgumentParser(
        description="Descarga todas las imágenes de un post y crea la composición final."
    )
    parser.add_argument("url", help="URL de X/Twitter o URL directa de imagen")
    parser.add_argument("output", type=Path, help="PNG/JPEG de salida")
    parser.add_argument("--top", required=True)
    parser.add_argument("--bottom", required=True)
    parser.add_argument("--preset", type=Path, default=DEFAULT_PRESET)
    parser.add_argument(
        "--background", type=Path, default=None,
        help="imagen externa para el fondo desenfocado",
    )
    parser.add_argument(
        "--style", default="auto", choices=("auto",) + STYLES,
        help="estilo de collage (defecto: auto)",
    )
    parser.add_argument(
        "--format", dest="output_format", default=None,
        help="proporcion de salida o auto para elegirla por orientacion",
    )
    parser.add_argument("--fit", choices=("auto", "cover", "contain"), default="auto")
    parser.add_argument(
        "--backend", choices=("auto", "cpu", "gpu"), default="auto",
        help="backend de píxeles: auto detecta CUDA y conserva CPU como fallback",
    )
    parser.add_argument(
        "--resolution", choices=("native", "4k"), default="4k",
        help="resolución de exportación: native o 4k (2160 px de lado corto)",
    )
    parser.add_argument(
        "--max-images", type=positive_int, default=DEFAULT_MAX_IMAGES,
        help=f"maximo de imagenes descargadas (defecto: {DEFAULT_MAX_IMAGES})",
    )
    args = parser.parse_args()
    if not args.top.strip():
        parser.error("--top no puede estar vacío: el titular debe aparecer en la imagen")
    if not args.bottom.strip():
        parser.error("--bottom no puede estar vacío: el contexto debe aparecer en la imagen")

    with tempfile.TemporaryDirectory(prefix="editimg-media-") as temporary:
        try:
            link_info = download_link_info(args.url, temporary, args.max_images)
        except Exception as exc:
            parser.error(str(exc))
        input_paths = [item["path"] for item in link_info["images"]]
        args.preset = select_auto_preset(args, link_info)
        command = build_composer_command(args, input_paths)
        completed = subprocess.run(command, check=False, stdout=subprocess.PIPE, text=True)
        if completed.returncode == 0:
            try:
                compose_metadata = json.loads(completed.stdout.strip().splitlines()[-1])
                if not isinstance(compose_metadata, dict):
                    raise ValueError("el compositor no devolvio metadatos validos")
                verification = validate_rendered_output(args.output, compose_metadata)
            except (IndexError, json.JSONDecodeError, ValueError) as exc:
                parser.error(str(exc))
            summary = {
                **compose_metadata,
                "output": str(args.output),
                "source_type": link_info["source_type"],
                "post_text": link_info["post_text"],
                "count": len(input_paths),
                "verification": {**verification, "ok": True},
            }
            print(json.dumps(summary, ensure_ascii=False))
        raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
