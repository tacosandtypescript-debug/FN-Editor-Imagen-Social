"""Puente hacia el código canónico del repositorio.

Carga `bin/*.py` por ruta, sin modificar ni copiar nada. Es exactamente el
mecanismo que ya usa `bin/edit_link.py` para importar `compose_image` y
`fetch_media`, así que el comportamiento es el del flujo existente.
"""

from __future__ import annotations

import importlib
import sys
import threading

from .. import config

_LOCK = threading.Lock()
_CACHE: dict[str, object] = {}


def _module(name: str):
    cached = _CACHE.get(name)
    if cached is not None:
        return cached
    with _LOCK:
        cached = _CACHE.get(name)
        if cached is None:
            bin_dir = str(config.BIN_DIR)
            if bin_dir not in sys.path:
                sys.path.insert(0, bin_dir)
            cached = importlib.import_module(name)
            _CACHE[name] = cached
    return cached


def fetch_media():
    """`bin/fetch_media.py`: descarga ordenada de medios de un post."""
    return _module("fetch_media")


def compose_image():
    """`bin/compose_image.py`: compositor canónico."""
    return _module("compose_image")


def edit_link():
    """`bin/edit_link.py`: construcción del comando y validación de salida."""
    return _module("edit_link")


def runtime_config():
    """`bin/runtime_config.py`: límites compartidos."""
    return _module("runtime_config")


def default_max_images() -> int:
    return int(runtime_config().DEFAULT_MAX_IMAGES)


def repository_root():
    return config.ROOT
