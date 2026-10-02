"""Utilidades de URL para no descargar más de lo necesario.

El CDN de imágenes de X admite variantes de tamaño en la propia URL. Medido en
este equipo sobre el mismo archivo:

    .jpg (por defecto)      99,4 KB
    ?name=large            243,7 KB
    ?name=orig             665,5 KB
    ?name=small             38,5 KB
    ?name=360x360           14,0 KB
    ?name=120x120            3,1 KB

Mostrar una miniatura de 92 px pidiendo el archivo por defecto cuesta siete
veces más bytes de los necesarios, y en el móvil eso se nota.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

#: Lado del cuadrado que se pide para las miniaturas. 360 px cubre de sobra una
#: miniatura de 92-108 px incluso en pantallas de alta densidad.
THUMB_SIZE = 360

#: Solo se reescriben las URL de este CDN.
TWITTER_MEDIA_HOSTS = ("pbs.twimg.com",)

# X suele entregar una imagen de portada para los vídeos. No basta con mirar
# la extensión: muchas portadas no tienen ninguna pista en el nombre del
# archivo, pero sí conservan la ruta que las identifica como vídeo.
VIDEO_URL_MARKERS = (
    "amplify_video",
    "video.twimg.com",
    "ext_tw_video_thumb",
    "tw_video_thumb",
    "video_thumb",
)
VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov", ".m3u8")


def is_twitter_media(url: str) -> bool:
    host = (urlsplit(str(url)).hostname or "").lower()
    return host in TWITTER_MEDIA_HOSTS or host.endswith(".twimg.com")


def is_video_url(url: str) -> bool:
    """Indica si una URL es un vídeo de X o su miniatura.

    La función es intencionadamente conservadora: una imagen normal de
    ``pbs.twimg.com/media`` no se convierte en vídeo solo por ser un medio.
    """
    raw = str(url or "").strip().lower()
    if not raw:
        return False
    parsed = urlsplit(raw)
    path = parsed.path.lower()
    return any(marker in raw for marker in VIDEO_URL_MARKERS) or path.endswith(VIDEO_EXTENSIONS)


def thumbnail_url(url: str, size: int = THUMB_SIZE) -> str:
    """Versión reducida de una imagen de X, para previsualizar.

    Se conserva la ruta y se sustituye el tamaño pedido. A las URL de otros
    dominios se las deja intactas: no se puede suponer que admitan parámetros.
    """
    raw = str(url or "").strip()
    if not raw or not is_twitter_media(raw):
        return raw

    parsed = urlsplit(raw)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.pop("name", None)
    query["name"] = f"{int(size)}x{int(size)}"
    # Sin `format` el CDN responde 404 cuando la ruta no trae extensión.
    query.setdefault("format", "jpg")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def full_size_url(url: str) -> str:
    """Versión a máxima resolución, la que se usa al componer la tarjeta."""
    raw = str(url or "").strip()
    if not raw or not is_twitter_media(raw):
        return raw

    parsed = urlsplit(raw)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["name"] = "orig"
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def thumbnails(urls, size: int = THUMB_SIZE) -> list[str]:
    """Lista de miniaturas a partir de las URL originales."""
    return [thumbnail_url(url, size) for url in (urls or [])]
