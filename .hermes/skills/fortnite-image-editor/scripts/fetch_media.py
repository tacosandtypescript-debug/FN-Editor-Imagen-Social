#!/usr/bin/env python3
"""Download all image media from an X/Twitter post in stable order.

The module is intentionally dependency-free apart from Pillow, so it can be
used by both the CLI and the link-to-composition workflow.
"""

import argparse
import json
import re
import sys
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from PIL import Image
from runtime_config import DEFAULT_MAX_IMAGES, MAX_SOURCE_PIXELS


USER_AGENT = "EditImg/1.0 (+https://github.com/tacosandtypescript-debug/editimg-fortnite-editor)"
X_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}
STATUS_RE = re.compile(r"^/(?:[^/]+/)?status/(\d+)(?:/.*)?$")
USER_STATUS_RE = re.compile(r"^/([^/]+)/status/(\d+)(?:/.*)?$")
POST_TEXT_KEYS = ("text", "full_text", "fullText", "tweetText", "tweet_text")
POST_CONTAINER_KEYS = ("tweet", "post", "data", "result", "legacy", "status")
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
FORMAT_EXTENSIONS = {
    "JPEG": ".jpg",
    "JPG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "GIF": ".gif",
    "BMP": ".bmp",
    "TIFF": ".tiff",
}


def request_bytes(url, accept="*/*", timeout=30):
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urlopen(request, timeout=timeout) as response:
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_DOWNLOAD_BYTES:
            raise ValueError(f"la respuesta supera {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB")
        chunks = []
        total = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_DOWNLOAD_BYTES:
                raise ValueError(f"la respuesta supera {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB")
            chunks.append(chunk)
    return b"".join(chunks)


def parse_x_status_url(url):
    parsed = urlsplit(url)
    host = parsed.hostname.lower() if parsed.hostname else ""
    if host not in X_HOSTS:
        return None
    match = USER_STATUS_RE.match(parsed.path)
    if match:
        if match.group(1).lower() == "i":
            raise ValueError("el enlace no incluye el usuario; usa la URL completa del post de X")
        return match.group(1), match.group(2)
    match = STATUS_RE.match(parsed.path)
    if match:
        raise ValueError("el enlace no incluye el usuario; usa la URL completa del post de X")
    raise ValueError("el enlace de X no parece apuntar a un post /status/ID")


def _media_entries(payload):
    """Yield known media containers from VxTwitter/FxTwitter responses."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in {
                "mediaURLs", "media_urls", "media_extended", "mediaDetails", "media_details",
                "media", "all", "photos", "images",
            }:
                # FxTwitter nests its ordered list under tweet.media.all/photos.
                # Recurse into that mapping, while keeping legacy flat arrays intact.
                if key == "media" and isinstance(value, dict):
                    yield from _media_entries(value)
                else:
                    yield value
            elif key in POST_CONTAINER_KEYS:
                yield from _media_entries(value)
    elif isinstance(payload, list):
        for value in payload:
            yield from _media_entries(value)


def extract_media_urls(payload):
    """Extract ordered image/thumbnail URLs without duplicating the same URL."""
    found = []

    def add(value):
        if isinstance(value, str) and value.startswith(("https://", "http://")):
            parsed = urlsplit(value)
            host = parsed.hostname.lower() if parsed.hostname else ""
            if host.endswith("twimg.com"):
                query = dict(parse_qsl(parsed.query, keep_blank_values=True))
                query["name"] = "orig"
                value = urlunsplit(
                    (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
                )
            if value not in found:
                found.append(value)

    for container in _media_entries(payload):
        values = container if isinstance(container, list) else [container]
        for item in values:
            if isinstance(item, str):
                add(item)
                continue
            if not isinstance(item, dict):
                continue
            media_type = str(item.get("type", "")).lower()
            if media_type in {"video", "gif"}:
                add(item.get("thumbnail_url") or item.get("thumbnail"))
            else:
                add(
                    item.get("url")
                    or item.get("media_url_https")
                    or item.get("media_url")
                    or item.get("src")
                )
    return found


def extract_post_text(payload):
    """Return the post text from common VxTwitter/FxTwitter response shapes."""
    def search(node):
        if isinstance(node, dict):
            for key in POST_TEXT_KEYS:
                value = node.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            for key in POST_CONTAINER_KEYS:
                if key in node:
                    found = search(node[key])
                    if found:
                        return found
        elif isinstance(node, list):
            for value in node:
                found = search(value)
                if found:
                    return found
        return None

    return search(payload)


def fetch_post_data(username, status_id):
    """Fetch ordered media and visible text, trying both public X mirrors."""
    errors = []
    for api_host in ("api.vxtwitter.com", "api.fxtwitter.com"):
        api_url = f"https://{api_host}/{username}/status/{status_id}"
        try:
            payload = json.loads(request_bytes(api_url, accept="application/json").decode("utf-8"))
            urls = extract_media_urls(payload)
            if urls:
                return {
                    "media_urls": urls,
                    "post_text": extract_post_text(payload),
                    "api_host": api_host,
                }
            errors.append(f"{api_host}: no devolvió imágenes")
        except (HTTPError, URLError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{api_host}: {exc}")
    raise RuntimeError("no se pudieron obtener los medios del post (" + "; ".join(errors) + ")")


def fetch_post_media(username, status_id):
    """Backward-compatible media-only API used by the existing CLI/tests."""
    return fetch_post_data(username, status_id)["media_urls"]


def is_probably_direct_image(url):
    parsed = urlsplit(url)
    suffix = Path(parsed.path).suffix.lower()
    host = parsed.hostname.lower() if parsed.hostname else ""
    trusted_media_host = (
        host in X_HOSTS
        or host == "twimg.com"
        or host.endswith(".twimg.com")
    )
    return suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"} or trusted_media_host


def inspect_image(raw):
    try:
        with Image.open(BytesIO(raw)) as image:
            image.verify()
        # verify() invalidates the image object, so reopen the bytes to inspect
        # dimensions and force decoding before the file is written to disk.
        with Image.open(BytesIO(raw)) as image:
            image_format = image.format
            size = image.size
            if size[0] <= 0 or size[1] <= 0:
                raise ValueError("sus dimensiones deben ser positivas")
            if size[0] * size[1] > MAX_SOURCE_PIXELS:
                raise ValueError(
                    f"supera el maximo de {MAX_SOURCE_PIXELS:,} pixeles"
                )
            image.load()
    except Exception as exc:
        raise ValueError(f"el recurso descargado no es una imagen válida: {exc}") from exc
    extension = FORMAT_EXTENSIONS.get(str(image_format).upper())
    if not extension:
        raise ValueError(f"formato de imagen no soportado: {image_format}")
    return extension, image_format, size


def download_images(urls, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for index, url in enumerate(urls, 1):
        raw = request_bytes(url)
        extension, image_format, size = inspect_image(raw)
        destination = output_dir / f"{index:02d}{extension}"
        destination.write_bytes(raw)
        results.append(
            {
                "path": str(destination),
                "url": url,
                "format": image_format,
                "width": size[0],
                "height": size[1],
            }
        )
    return results


def download_link_info(url, output_dir, max_images=DEFAULT_MAX_IMAGES):
    """Download a link and return media plus the post text when available."""
    if isinstance(max_images, bool) or not isinstance(max_images, int) or max_images <= 0:
        raise ValueError("max-images debe ser mayor que cero")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("el enlace debe usar http o https")

    post = parse_x_status_url(url)
    if post:
        post_data = fetch_post_data(*post)
        media_urls = post_data["media_urls"]
        post_text = post_data["post_text"]
        source_type = "x-post"
    elif is_probably_direct_image(url):
        media_urls = [url]
        post_text = None
        source_type = "direct-image"
    else:
        raise ValueError("solo se admiten enlaces de posts de X/Twitter o URLs directas de imagen")

    if not media_urls:
        raise ValueError("el enlace no contiene imágenes")
    if len(media_urls) > max_images:
        raise ValueError(f"el enlace contiene {len(media_urls)} imágenes; el máximo configurado es {max_images}")
    return {
        "url": url,
        "source_type": source_type,
        "post_text": post_text,
        "images": download_images(media_urls, output_dir),
    }


def download_link(url, output_dir, max_images=DEFAULT_MAX_IMAGES):
    """Backward-compatible API returning only the downloaded image records."""
    return download_link_info(url, output_dir, max_images)["images"]


def main():
    parser = argparse.ArgumentParser(description="Descarga todas las imágenes de un post de X/Twitter.")
    parser.add_argument("url")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--max-images", type=int, default=DEFAULT_MAX_IMAGES)
    args = parser.parse_args()
    try:
        info = download_link_info(args.url, args.output_dir, args.max_images)
    except (HTTPError, URLError, OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({**info, "count": len(info["images"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
