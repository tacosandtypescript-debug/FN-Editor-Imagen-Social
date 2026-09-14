#!/usr/bin/env python3
"""Download several links as one ordered editorial batch for Hermes."""

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError, URLError

from fetch_media import download_link_info
from runtime_config import DEFAULT_MAX_IMAGES


def download_batch_info(urls, output_dir, max_images=DEFAULT_MAX_IMAGES):
    if not urls:
        raise ValueError("el lote necesita al menos un enlace")
    if isinstance(max_images, bool) or not isinstance(max_images, int) or max_images <= 0:
        raise ValueError("max-images debe ser mayor que cero")
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    sources, images = [], []
    for source_index, url in enumerate(urls, 1):
        remaining = max_images - len(images)
        if remaining <= 0:
            raise ValueError(f"el lote supera el máximo de {max_images} imágenes; no se pudo procesar el enlace {url}")
        info = download_link_info(url, root / f"source-{source_index:02d}", remaining)
        source_images = [{**item, "source_index": source_index, "source_url": url} for item in info["images"]]
        images.extend(source_images)
        sources.append({
            "source_index": source_index, "url": info["url"],
            "source_type": info["source_type"], "post_text": info["post_text"],
            "images": source_images, "count": len(source_images),
        })
    return {
        "batch_type": "carousel", "source_count": len(sources),
        "sources": sources, "images": images, "count": len(images),
        "editorial_contract": {
            "one_general_title": True, "one_shared_context_and_date": True,
            "caption_hashtag_count": 5, "required_brand_hashtag": "#khetzalgg",
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Descarga varios enlaces como un solo carrusel.")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("urls", nargs="+", help="enlaces en el orden del carrusel")
    parser.add_argument("--max-images", type=int, default=DEFAULT_MAX_IMAGES)
    parser.add_argument("--metadata", type=Path, default=None)
    args = parser.parse_args()
    try:
        manifest = download_batch_info(args.urls, args.output_dir, args.max_images)
    except (HTTPError, URLError, OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    encoded = json.dumps(manifest, ensure_ascii=False, indent=2)
    if args.metadata:
        args.metadata.parent.mkdir(parents=True, exist_ok=True)
        args.metadata.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
