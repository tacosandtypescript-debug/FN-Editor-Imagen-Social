"""Generación de tarjetas.

No reimplementa nada del compositor: construye el comando con
`edit_link.build_composer_command`, elige el preset con
`edit_link.select_auto_preset` y valida el resultado con
`edit_link.validate_rendered_output`. El renderizado final lo hace
`bin/compose_image.py` como proceso independiente, igual que hoy.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from .. import config
from . import media as media_pipeline
from . import repo

#: Valores aceptados, tomados del propio compositor para no divergir.
ALLOWED_FITS = ("auto", "cover", "contain")
ALLOWED_RESOLUTIONS = ("native", "4k")
ALLOWED_BACKENDS = ("auto", "cpu", "gpu")
#: Solo los formatos que tienen preset real en `bin/`. Estaba también 4:5, que
#: no tiene preset propio: al elegirlo se componía en 9:16 sin avisar, así que
#: la interfaz ofrecía algo que no cumplía.
ALLOWED_FORMATS = ("auto", "9:16", "1:1", "16:9")


class CardError(RuntimeError):
    """Error de parámetros o de renderizado, con mensaje para el usuario."""


def allowed_styles() -> tuple[str, ...]:
    composer = repo.compose_image()
    return ("auto",) + tuple(composer.STYLES)


def normalise_params(params: dict) -> dict:
    """Valida y completa los parámetros de composición."""
    style = str(params.get("style") or config.Settings().default_style).strip().lower()
    fit = str(params.get("fit") or config.Settings().default_fit).strip().lower()
    resolution = str(
        params.get("resolution") or config.Settings().default_resolution
    ).strip().lower()
    backend = str(params.get("backend") or config.Settings().default_backend).strip().lower()
    output_format = str(
        params.get("format") or config.Settings().default_format
    ).strip().lower()

    top = str(params.get("top") or "").strip()
    bottom = str(params.get("bottom") or "").strip()
    if not top:
        raise CardError("el titular (top) no puede estar vacío")
    if not bottom:
        raise CardError("el contexto (bottom) no puede estar vacío")

    if style not in allowed_styles():
        raise CardError(f"estilo no admitido: {style}")
    if fit not in ALLOWED_FITS:
        raise CardError(f"ajuste no admitido: {fit}")
    if resolution not in ALLOWED_RESOLUTIONS:
        raise CardError(f"resolución no admitida: {resolution}")
    if backend not in ALLOWED_BACKENDS:
        raise CardError(f"backend no admitido: {backend}")
    if output_format not in ALLOWED_FORMATS:
        raise CardError(f"formato no admitido: {output_format}")

    # El compositor rechaza palabras funcionales coloreadas y más de dos acentos.
    composer = repo.compose_image()
    try:
        composer.validate_text_markup(top, bottom)
    except ValueError as exc:
        raise CardError(str(exc)) from exc

    return {
        "top": top,
        "bottom": bottom,
        "style": style,
        "fit": fit,
        "resolution": resolution,
        "backend": backend,
        "format": output_format,
        "background": str(params["background"]) if params.get("background") else None,
    }


def render_card(
    *,
    images: list[Path],
    output: Path,
    params: dict,
    max_images: int | None = None,
) -> dict:
    """Compone una tarjeta con las imágenes dadas y devuelve el resumen."""
    if not images:
        raise CardError(
            "la publicación no tiene imágenes; el compositor necesita al menos una"
        )
    limit = media_pipeline.default_limit(max_images)
    if len(images) > limit:
        raise CardError(f"se recibieron {len(images)} imágenes; el máximo es {limit}")

    clean = normalise_params(params)
    edit_link = repo.edit_link()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    preset = Path(params["preset"]) if params.get("preset") else Path(edit_link.DEFAULT_PRESET)

    args = SimpleNamespace(
        output=output,
        top=clean["top"],
        bottom=clean["bottom"],
        preset=preset,
        style=clean["style"],
        fit=clean["fit"],
        backend=clean["backend"],
        resolution=clean["resolution"],
        max_images=limit,
        output_format=clean["format"],
        background=Path(clean["background"]) if clean["background"] else None,
    )

    sizes = media_pipeline.image_sizes(images)
    link_info = {
        "images": [{"width": width, "height": height} for width, height in sizes]
    }
    # Reutiliza la selección automática de preset del flujo de enlaces.
    args.preset = edit_link.select_auto_preset(args, link_info)

    command = edit_link.build_composer_command(args, [str(path) for path in images])
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        message = detail[-1] if detail else f"el compositor falló ({completed.returncode})"
        raise CardError(message)

    metadata = _parse_metadata(completed.stdout)
    try:
        verification = edit_link.validate_rendered_output(output, metadata)
    except ValueError as exc:
        raise CardError(str(exc)) from exc

    return {
        **metadata,
        "output": str(output),
        "verification": {**verification, "ok": True},
        "count": len(images),
        "preset": str(args.preset),
        "params": clean,
    }


def _parse_metadata(stdout: str) -> dict:
    lines = [line for line in (stdout or "").splitlines() if line.strip()]
    if not lines:
        raise CardError("el compositor no devolvió metadatos")
    try:
        metadata = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise CardError(f"metadatos del compositor ilegibles: {exc}") from exc
    if not isinstance(metadata, dict):
        raise CardError("los metadatos del compositor no son un objeto JSON")
    return metadata


def composer_version() -> str:
    """Versión informativa del compositor en uso."""
    return f"{sys.version_info.major}.{sys.version_info.minor}"
