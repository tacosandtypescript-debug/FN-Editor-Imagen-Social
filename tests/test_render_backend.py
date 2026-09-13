import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
COMPOSER = BIN / "compose_image.py"
PRESET = BIN / "preset.json"
sys.path.insert(0, str(BIN))

import compose_image as composer  # noqa: E402
from render_backend import (  # noqa: E402
    BackendUnavailable,
    make_background,
    resolve_backend,
    resize_image,
)


class RenderBackendTests(unittest.TestCase):
    def test_composer_exposes_backend_selection(self):
        result = subprocess.run(
            [sys.executable, str(COMPOSER), "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--backend", result.stdout)
        self.assertIn("auto", result.stdout)
        self.assertIn("cpu", result.stdout)
        self.assertIn("gpu", result.stdout)

    def test_cpu_backend_is_always_available(self):
        self.assertEqual(resolve_backend("cpu"), "cpu")

    def test_auto_falls_back_and_gpu_fails_closed_without_cuda(self):
        with patch(
            "render_backend._load_cupy",
            side_effect=BackendUnavailable("CUDA no disponible"),
        ):
            self.assertEqual(resolve_backend("auto"), "cpu")
            with self.assertRaises(BackendUnavailable):
                resolve_backend("gpu")

    def test_cpu_resize_preserves_requested_dimensions(self):
        image = Image.new("RGB", (19, 11), (20, 40, 80))
        resized = resize_image(image, (37, 23), "cpu")
        self.assertEqual(resized.size, (37, 23))

    def test_cpu_render_reports_backend(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = work / "source.png"
            output = work / "output.png"
            Image.new("RGB", (320, 180), (20, 40, 80)).save(source)
            result = subprocess.run(
                [
                    sys.executable,
                    str(COMPOSER),
                    str(source),
                    str(output),
                    "--top",
                    "NOVEDADES",
                    "--bottom",
                    "CONTEXTO",
                    "--preset",
                    str(PRESET),
                    "--backend",
                    "cpu",
                ],
                cwd=work,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            metadata = __import__("json").loads(result.stdout.strip().splitlines()[-1])
            self.assertEqual(metadata["backend"], "cpu")
            with Image.open(output) as image:
                self.assertEqual(image.size, (1080, 1920))

    def test_small_gpu_resize_stays_cpu_to_avoid_transfer_overhead(self):
        image = Image.new("RGB", (320, 180), (20, 40, 80))
        with patch(
            "render_backend._numpy_and_cupy",
            side_effect=AssertionError("no debe inicializar CUDA para una celda pequeña"),
        ):
            resized = resize_image(image, (640, 360), "gpu")
        self.assertEqual(resized.size, (640, 360))

    def test_gpu_resize_smoke_when_cuda_is_available(self):
        try:
            backend = resolve_backend("auto")
        except BackendUnavailable as exc:
            self.skipTest(str(exc))
        if backend != "gpu":
            self.skipTest("CuPy/CUDA no está disponible")
        image = Image.new("RGB", (32, 24), (20, 40, 80))
        resized = resize_image(image, (1600, 1300), "gpu")
        self.assertEqual(resized.size, (1600, 1300))
        self.assertEqual(resized.mode, "RGBA")
    def test_small_gpu_background_stays_cpu_to_avoid_transfer_overhead(self):
        image = Image.new("RGB", (320, 180), (20, 40, 80))
        cfg = {
            "canvas": {"width": 1080, "height": 1920},
            "background_blur": 16,
            "background_dim": 0.92,
        }
        with patch(
            "compose_image.make_gpu_background",
            side_effect=AssertionError("no debe usar CUDA en un lienzo pequeño"),
        ):
            background = composer.make_bg(image, cfg, "gpu")
        self.assertEqual(background.size, (1080, 1920))

    def test_gpu_background_smoke_when_cuda_is_available(self):
        try:
            backend = resolve_backend("auto")
        except BackendUnavailable as exc:
            self.skipTest(str(exc))
        if backend != "gpu":
            self.skipTest("CuPy/CUDA no está disponible")
        image = Image.new("RGB", (320, 180), (20, 40, 80))
        cfg = {
            "canvas": {"width": 640, "height": 360},
            "background_blur": 2,
            "background_dim": 0.9,
        }
        background = make_background(image, cfg, "gpu")
        self.assertEqual(background.size, (640, 360))
        self.assertEqual(background.mode, "RGBA")


if __name__ == "__main__":
    unittest.main()
