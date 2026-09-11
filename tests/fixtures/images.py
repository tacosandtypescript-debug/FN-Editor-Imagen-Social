"""Helpers for creating tiny in-memory or on-disk image fixtures."""

from io import BytesIO
from pathlib import Path

from PIL import Image


def make_image(path: Path, size=(320, 180), color=(40, 80, 140), mode="RGB"):
    """Create a deterministic fixture image at *path*."""
    image = Image.new(mode, size, color)
    image.save(path)
    return path


def image_bytes(image_format, size=(32, 16)):
    """Return a small valid fixture encoded in the requested format."""
    mode = "RGBA" if image_format == "PNG" else "RGB"
    color = (10, 20, 30, 255) if mode == "RGBA" else (10, 20, 30)
    buffer = BytesIO()
    Image.new(mode, size, color).save(buffer, format=image_format)
    return buffer.getvalue()
