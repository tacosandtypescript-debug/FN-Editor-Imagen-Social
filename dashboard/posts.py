"""Lectura de una publicación de X: texto, medios y fecha reales.

Es lo único que el dashboard necesita del código canónico del repositorio. Se
carga `bin/fetch_media.py` por ruta, **sin modificarlo ni copiarlo**: es el mismo
mecanismo que usa `bin/edit_link.py` para importarlo, así que el comportamiento
es el del flujo existente.

Antes esto vivía repartido entre `pipeline/media.py`, `pipeline/repo.py` y el
compositor, porque el dashboard también componía tarjetas. Ya no compone nada.
"""

from __future__ import annotations

import importlib
import json
import sys
import threading
from urllib.error import HTTPError, URLError

from . import config

_LOCK = threading.Lock()
_CACHE: dict[str, object] = {}

#: Claves donde los mirrors públicos exponen la fecha de publicación.
_DATE_KEYS = ("created_at", "date", "createdAt", "tweet_created_at", "published_at")

#: Mirrors que devuelven el JSON del post sin necesidad de credenciales.
API_HOSTS = ("api.vxtwitter.com", "api.fxtwitter.com")


def fetch_media():
    """`bin/fetch_media.py`, cargado una sola vez."""
    cached = _CACHE.get("fetch_media")
    if cached is not None:
        return cached
    with _LOCK:
        cached = _CACHE.get("fetch_media")
        if cached is None:
            bin_dir = str(config.BIN_DIR)
            if bin_dir not in sys.path:
                sys.path.insert(0, bin_dir)
            cached = importlib.import_module("fetch_media")
            _CACHE["fetch_media"] = cached
    return cached


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
    """Devuelve texto, medios ordenados y fecha de un post sin descargarlos.

    Reutiliza `parse_x_status_url`, `request_bytes`, `extract_media_urls` y
    `extract_post_text` del módulo canónico, y añade la fecha, que ese módulo no
    expone.

    Lanza `ValueError`/`RuntimeError` con el motivo si el post no sirve.
    """
    module = fetch_media()
    post = module.parse_x_status_url(url)
    if not post:
        raise ValueError("el enlace no apunta a un post de X")
    username, status_id = post

    errors: list[str] = []
    media_without_text: dict | None = None
    for api_host in API_HOSTS:
        api_url = f"https://{api_host}/{username}/status/{status_id}"
        try:
            raw = module.request_bytes(api_url, accept="application/json")
            payload = json.loads(raw.decode("utf-8"))
        except (HTTPError, URLError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{api_host}: {exc}")
            continue

        media_urls = module.extract_media_urls(payload)
        post_text = module.extract_post_text(payload)
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
