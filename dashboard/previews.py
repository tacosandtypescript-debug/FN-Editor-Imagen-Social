"""Versiones reducidas de las tarjetas, para mostrarlas sin arruinar el móvil.

El PNG de una tarjeta ronda los 30 MB: se compone a resolución nativa
(2160x3840, 3840x2160 o más). Servirlo entero para verlo en una lista de móvil
es servir una imagen de 4000 px en un hueco de 400 px; cinco tarjetas suponían
150 MB de descarga.

Aquí se genera una versión reducida en JPEG y se guarda en caché, de modo que
solo se calcula la primera vez. El PNG original no se toca: sigue siendo lo que
se envía a Telegram y lo que se descarga.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

from . import config

#: Lado mayor de la versión reducida. 720 px cubre con holgura una miniatura a
#: doble densidad en móvil (375 px de ancho lógico).
PREVIEW_MAX_SIDE = 720
PREVIEW_QUALITY = 82


def cache_directory() -> Path:
    path = config.VAR_DIR / "previews"
    path.mkdir(parents=True, exist_ok=True)
    return path


def preview_path(source: Path, max_side: int = PREVIEW_MAX_SIDE) -> Path:
    """Devuelve la versión reducida de una imagen, generándola si hace falta.

    La clave de caché incluye el tamaño y la fecha de modificación del
    original, así que al recomponer una tarjeta la miniatura se regenera sola.
    """
    source = Path(source)
    stat = source.stat()
    max_side = max(64, min(4000, int(max_side)))
    clave = f"{source.name}|{stat.st_mtime_ns}|{stat.st_size}|{max_side}"
    destino = cache_directory() / f"{hashlib.sha1(clave.encode()).hexdigest()[:20]}.jpg"
    if destino.is_file() and destino.stat().st_size > 0:
        return destino

    with Image.open(source) as original:
        imagen = original.convert("RGB")
        ancho, alto = imagen.size
        escala = min(1.0, max_side / max(ancho, alto))
        if escala < 1.0:
            imagen = imagen.resize(
                (max(1, round(ancho * escala)), max(1, round(alto * escala))),
                Image.LANCZOS,
            )
        temporal = destino.with_suffix(".parcial")
        imagen.save(
            temporal, format="JPEG", quality=PREVIEW_QUALITY, optimize=True, progressive=True
        )
    # Se publica con un renombrado para que nadie lea un archivo a medias.
    temporal.replace(destino)
    return destino


def prune(keep: int = 400) -> int:
    """Borra las miniaturas más antiguas. Devuelve cuántas eliminó."""
    archivos = sorted(
        cache_directory().glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    sobrantes = archivos[max(0, int(keep)):]
    for archivo in sobrantes:
        try:
            archivo.unlink()
        except OSError:
            continue
    return len(sobrantes)
