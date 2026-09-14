#!/usr/bin/env python3
"""Download a link and expose the post text needed before composing a card.

This is the deterministic first half of the Hermes link workflow. It writes
media files in source order and prints JSON so the agent can read the real post
text, draft the headline, and immediately invoke ``compose_image.py``.
"""

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError, URLError

from fetch_media import download_link_info
from runtime_config import DEFAULT_MAX_IMAGES


def main():
    parser = argparse.ArgumentParser(
        description="Descarga medios y devuelve el texto visible del enlace en JSON."
    )
    parser.add_argument("url", help="URL de un post de X/Twitter o imagen directa")
    parser.add_argument("output_dir", type=Path, help="carpeta de medios descargados")
    parser.add_argument(
        "--max-images", type=int, default=DEFAULT_MAX_IMAGES,
        help=f"máximo de imágenes (defecto: {DEFAULT_MAX_IMAGES})",
    )
    parser.add_argument(
        "--metadata", type=Path, default=None,
        help="opcional: guarda el mismo manifiesto JSON en esta ruta",
    )
    args = parser.parse_args()

    try:
        info = download_link_info(args.url, args.output_dir, args.max_images)
    except (HTTPError, URLError, OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))

    manifest = {**info, "count": len(info["images"])}
    encoded = json.dumps(manifest, ensure_ascii=False, indent=2)
    if args.metadata:
        args.metadata.parent.mkdir(parents=True, exist_ok=True)
        args.metadata.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
