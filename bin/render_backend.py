"""Optional CUDA pixel backend with a transparent Pillow fallback.

The compositor still owns layout, text, masks, and file output. This module
only accelerates the expensive resize and background-blur stages when CuPy can
open a CUDA device. Keeping the dependency optional preserves the CPU-only
install on machines without NVIDIA hardware.
"""

from __future__ import annotations

from typing import Any

from PIL import Image


GPU_RESIZE_MIN_PIXELS = 2_000_000
GPU_BACKGROUND_MIN_PIXELS = 4_000_000


def should_use_gpu_background(cfg: dict) -> bool:
    """Return whether the canvas is large enough to amortize CUDA transfers."""
    canvas = cfg.get("canvas", {})
    return (
        int(canvas.get("width", 0)) * int(canvas.get("height", 0))
        >= GPU_BACKGROUND_MIN_PIXELS
    )


class BackendUnavailable(RuntimeError):
    """Raised when the requested GPU backend cannot be initialized."""


def _load_cupy():
    try:
        import cupy as cp
    except Exception as exc:  # pragma: no cover - depends on the host install
        raise BackendUnavailable(
            "el backend GPU requiere CuPy; instala requirements-gpu.txt "
            "(cupy-cuda12x) o usa --backend cpu"
        ) from exc
    try:
        if int(cp.cuda.runtime.getDeviceCount()) < 1:
            raise BackendUnavailable("no se encontró una GPU CUDA disponible; usa --backend cpu")
        cp.cuda.Device(0).use()
    except BackendUnavailable:
        raise
    except Exception as exc:  # pragma: no cover - depends on the CUDA driver
        raise BackendUnavailable(
            f"no se pudo inicializar CUDA ({exc}); usa --backend cpu"
        ) from exc
    return cp


def resolve_backend(requested: str = "auto") -> str:
    """Resolve ``auto``, ``cpu`` or ``gpu`` to the active backend name."""
    if requested not in {"auto", "cpu", "gpu"}:
        raise ValueError("backend debe ser auto, cpu o gpu")
    if requested == "cpu":
        return "cpu"
    try:
        _load_cupy()
    except BackendUnavailable:
        if requested == "gpu":
            raise
        return "cpu"
    return "gpu"


def _numpy_and_cupy():
    cp = _load_cupy()
    try:
        import numpy as np
    except Exception as exc:  # pragma: no cover - CuPy normally installs NumPy
        raise BackendUnavailable("el backend GPU requiere NumPy; reinstala CuPy") from exc
    return np, cp


def _gpu_resize_array(array: Any, size: tuple[int, int], np, cp):
    """Resize an HWC array on CUDA using cubic interpolation."""
    from cupyx.scipy.ndimage import zoom

    target_width, target_height = size
    if array.shape[1] == target_width and array.shape[0] == target_height:
        return cp.asarray(array)
    device = cp.asarray(array, dtype=cp.float32)
    factors = (
        target_height / array.shape[0],
        target_width / array.shape[1],
        1.0,
    )
    resized = zoom(device, factors, order=3, mode="nearest", prefilter=False)
    if resized.shape[:2] != (target_height, target_width):
        resized = resized[:target_height, :target_width, :]
        if resized.shape[:2] != (target_height, target_width):
            padded = cp.zeros((target_height, target_width, array.shape[2]), dtype=resized.dtype)
            padded[:resized.shape[0], :resized.shape[1], :] = resized
            resized = padded
    return cp.clip(resized, 0, 255).astype(cp.uint8)


def resize_image(image: Image.Image, size: tuple[int, int], backend: str) -> Image.Image:
    """Resize with CUDA for large surfaces and Pillow for small surfaces."""
    if backend != "gpu" or size[0] * size[1] < GPU_RESIZE_MIN_PIXELS:
        return image.resize(size, Image.Resampling.LANCZOS)
    np, cp = _numpy_and_cupy()
    array = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    resized = _gpu_resize_array(array, size, np, cp)
    cp.cuda.Stream.null.synchronize()
    return Image.fromarray(cp.asnumpy(resized), "RGBA")


def make_background(image: Image.Image, cfg: dict, backend: str) -> Image.Image:
    """Build the blurred/dimmed background, using CUDA for pixel transforms."""
    if backend != "gpu":
        raise ValueError("make_background requiere backend gpu")
    np, cp = _numpy_and_cupy()
    from cupyx.scipy.ndimage import gaussian_filter

    width = int(cfg["canvas"]["width"])
    height = int(cfg["canvas"]["height"])
    source = np.asarray(image.convert("RGB"), dtype=np.uint8)
    source_height, source_width = source.shape[:2]
    scale = max(width / source_width, height / source_height)
    resized_width = max(1, round(source_width * scale))
    resized_height = max(1, round(source_height * scale))
    resized = _gpu_resize_array(source, (resized_width, resized_height), np, cp)
    left = (resized_width - width) // 2
    top = (resized_height - height) // 2
    cropped = resized[top : top + height, left : left + width, :3]
    sigma = float(cfg.get("background_blur", 16))
    blurred = gaussian_filter(cropped, sigma=(sigma, sigma, 0), mode="nearest")
    dim = float(cfg.get("background_dim", 0.92))
    rgb = cp.clip(blurred.astype(cp.float32) * dim, 0, 255).astype(cp.uint8)
    alpha = cp.full((height, width, 1), 255, dtype=cp.uint8)
    output = cp.concatenate((rgb, alpha), axis=2)
    cp.cuda.Stream.null.synchronize()
    return Image.fromarray(cp.asnumpy(output), "RGBA")
