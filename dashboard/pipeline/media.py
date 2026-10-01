"""Descarga y sondeo de medios, delegando en `bin/fetch_media.py`."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError, URLError

from . import repo

#: Claves donde los mirrors públicos exponen la fecha de publicación.
_DATE_KEYS = ("created_at", "date", "createdAt", "tweet_created_at", "published_at")


def _find_date(payload) -> str | None:
    """Busca la fecha de publicación en las respuestas de vx/fxtwitter."""
    if isinstance(payload, dict):
        for key in _DATE_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in payload.values():
            found = _find_date(value)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_date(value)
            if found:
                return found
    return None


def probe_post(url: str) -> dict:
    """Devuelve medios ordenados y texto de un post sin descargarlos.

    Reutiliza `parse_x_status_url`, `request_bytes`, `extract_media_urls` y
    `extract_post_text` del módulo canónico y añade la fecha, que ese módulo
    no expone.

    Lanza `ValueError`/`RuntimeError` con el motivo si el post no sirve.
    """
    fm = repo.fetch_media()
    post = fm.parse_x_status_url(url)
    if not post:
        raise ValueError("el enlace no apunta a un post de X")
    username, status_id = post

    errors: list[str] = []
    media_without_text: dict | None = None
    for api_host in ("api.vxtwitter.com", "api.fxtwitter.com"):
        api_url = f"https://{api_host}/{username}/status/{status_id}"
        try:
            raw = fm.request_bytes(api_url, accept="application/json")
            payload = json.loads(raw.decode("utf-8"))
        except (HTTPError, URLError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{api_host}: {exc}")
            continue

        media_urls = fm.extract_media_urls(payload)
        post_text = fm.extract_post_text(payload)
        posted_at = _find_date(payload)
        if media_urls:
            data = {
                "media_urls": media_urls,
                "post_text": post_text,
                "posted_at": posted_at,
                "api_host": api_host,
            }
            if post_text or posted_at:
                return data
            media_without_text = media_without_text or data
            continue
        # Un post sin imágenes es válido; se devuelve igualmente.
        if post_text or posted_at:
            return {
                "media_urls": [],
                "post_text": post_text,
                "posted_at": posted_at,
                "api_host": api_host,
            }
        errors.append(f"{api_host}: respuesta sin texto ni imágenes")

    if media_without_text:
        return media_without_text
    raise RuntimeError("no se pudo leer el post (" + "; ".join(errors) + ")")


def download_media(url: str, destination: Path, max_images: int | None = None) -> dict:
    """Descarga todos los medios del post en orden estable.

    Delega por completo en `fetch_media.download_link_info`, así que los
    límites de tamaño, la validación de imagen y el orden son los del flujo
    actual.
    """
    fm = repo.fetch_media()
    limit = default_limit(max_images)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    return fm.download_link_info(url, destination, limit)


def default_limit(max_images: int | None) -> int:
    if isinstance(max_images, bool) or max_images is None:
        return repo.default_max_images()
    value = int(max_images)
    if value <= 0:
        raise ValueError("max_images debe ser mayor que cero")
    return value


def image_sizes(paths) -> list[tuple[int, int]]:
    """Dimensiones reales de una lista de archivos, para elegir el preset."""
    from PIL import Image

    sizes: list[tuple[int, int]] = []
    for path in paths:
        with Image.open(path) as image:
            sizes.append(image.size)
    return sizes
