"""Tests de las versiones reducidas de las tarjetas.

Motivo real: el PNG de una tarjeta ronda los 30 MB. La vista de procesadas lo
servía entero para una miniatura de móvil, así que cinco tarjetas suponían
150 MB de descarga. La guía lo llama «servir una imagen de 4000 px en un hueco
de 400 px».
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from dashboard import config, previews  # noqa: E402


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.work = Path(self._temporary.name)
        self._originals = {n: getattr(config, n) for n in ("VAR_DIR", "DB_PATH")}
        config.VAR_DIR = self.work
        config.DB_PATH = self.work / "dashboard.sqlite3"
        config.ensure_directories()
        # Una tarjeta como las de verdad, pero más pequeña para ir rápido.
        self.origen = self.work / "tarjeta.png"
        Image.new("RGB", (2160, 3840), (120, 60, 180)).save(self.origen)

    def tearDown(self):
        for name, value in self._originals.items():
            setattr(config, name, value)
        self._temporary.cleanup()

    def test_the_preview_is_much_smaller_than_the_original(self):
        reducida = previews.preview_path(self.origen)
        self.assertLess(
            reducida.stat().st_size,
            self.origen.stat().st_size / 10,
            "la reducida debería pesar una fracción del original",
        )

    def test_the_preview_keeps_the_proportions(self):
        reducida = previews.preview_path(self.origen, max_side=720)
        with Image.open(reducida) as imagen:
            ancho, alto = imagen.size
        self.assertEqual(max(ancho, alto), 720)
        # 2160x3840 es 9:16; la reducida debe mantener esa proporción.
        self.assertAlmostEqual(ancho / alto, 2160 / 3840, places=2)

    def test_a_square_card_stays_square(self):
        cuadrada = self.work / "cuadrada.png"
        Image.new("RGB", (2160, 2160), (10, 90, 40)).save(cuadrada)
        reducida = previews.preview_path(cuadrada, max_side=600)
        with Image.open(reducida) as imagen:
            self.assertEqual(imagen.size, (600, 600))

    def test_the_same_image_is_not_regenerated(self):
        primera = previews.preview_path(self.origen)
        marca = primera.stat().st_mtime_ns
        segunda = previews.preview_path(self.origen)
        self.assertEqual(primera, segunda)
        self.assertEqual(segunda.stat().st_mtime_ns, marca, "no debería regenerarse")

    def test_a_changed_image_regenerates_its_preview(self):
        primera = previews.preview_path(self.origen)
        ruta_primera = primera
        # Se recompone la tarjeta con otro contenido.
        Image.new("RGB", (2160, 3840), (10, 200, 90)).save(self.origen)
        segunda = previews.preview_path(self.origen)
        self.assertNotEqual(
            ruta_primera, segunda, "al cambiar el original debe cambiar la reducida"
        )

    def test_an_image_smaller_than_the_limit_is_not_enlarged(self):
        pequena = self.work / "pequena.png"
        Image.new("RGB", (300, 200), (200, 200, 10)).save(pequena)
        reducida = previews.preview_path(pequena, max_side=720)
        with Image.open(reducida) as imagen:
            self.assertEqual(imagen.size, (300, 200), "no se agranda")

    def test_the_result_is_a_valid_jpeg(self):
        reducida = previews.preview_path(self.origen)
        self.assertEqual(reducida.suffix, ".jpg")
        with Image.open(reducida) as imagen:
            self.assertEqual(imagen.format, "JPEG")
            imagen.load()

    def test_the_limit_is_clamped_to_something_sane(self):
        # Un valor absurdo no debe reventar ni generar gigantes.
        for valor in (0, -50, 999999):
            with self.subTest(valor=valor):
                reducida = previews.preview_path(self.origen, max_side=valor)
                self.assertTrue(reducida.is_file())

    def test_pruning_keeps_the_newest(self):
        for indice in range(8):
            imagen = self.work / f"c{indice}.png"
            Image.new("RGB", (400, 400), (indice * 20, 40, 90)).save(imagen)
            previews.preview_path(imagen, max_side=200)
        antes = len(list(previews.cache_directory().glob("*.jpg")))
        self.assertEqual(antes, 8)
        borradas = previews.prune(keep=3)
        self.assertEqual(borradas, 5)
        self.assertEqual(len(list(previews.cache_directory().glob("*.jpg"))), 3)

    def test_pruning_an_empty_cache_is_harmless(self):
        self.assertEqual(previews.prune(keep=10), 0)


if __name__ == "__main__":
    unittest.main()
