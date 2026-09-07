import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from fetch_media import extract_media_urls, parse_x_status_url


COMPOSER = ROOT / "bin" / "compose_image.py"
SKILL_COMPOSER = ROOT / "skills" / "media" / "vertical-image-editor" / "scripts" / "compose_image.py"
PRESET = ROOT / "bin" / "preset.json"
COMPOSER_SPEC = importlib.util.spec_from_file_location("compose_image_under_test", COMPOSER)
COMPOSER_MODULE = importlib.util.module_from_spec(COMPOSER_SPEC)
COMPOSER_SPEC.loader.exec_module(COMPOSER_MODULE)


class ComposeImageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        self.inputs = []
        for index in range(4):
            path = self.work / f"input-{index}.png"
            Image.new("RGB", (320 + index * 20, 180 + index * 10), (40 + index * 30, 80, 140)).save(path)
            self.inputs.append(path)

    def tearDown(self):
        self.tmp.cleanup()

    def run_composer(self, script, output, *extra):
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                str(self.inputs[0]),
                str(output),
                "--top",
                "NOVEDADES {FORTNITE|8B3DFF}",
                "--bottom",
                "CONTEXTO · 07/09",
                "--preset",
                str(PRESET),
                *extra,
            ],
            cwd=self.work,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_basic_output_and_skill_wrapper_match(self):
        main_output = self.work / "main.png"
        skill_output = self.work / "skill.png"
        main_meta = self.run_composer(COMPOSER, main_output)
        skill_meta = self.run_composer(SKILL_COMPOSER, skill_output)

        self.assertEqual(main_meta["width"], 1080)
        self.assertEqual(main_meta["height"], 1920)
        self.assertEqual(main_meta["style"], skill_meta["style"])
        self.assertEqual(hashlib.sha256(main_output.read_bytes()).digest(), hashlib.sha256(skill_output.read_bytes()).digest())
        with Image.open(main_output) as image:
            self.assertEqual((image.width, image.height, image.format), (1080, 1920, "PNG"))
            self.assertEqual(image.mode, "RGBA")

    def test_multiline_text_is_shrunk_into_safe_area(self):
        output = self.work / "long.png"
        top = "\n".join(["TITULAR MUY LARGO {FORTNITE|8B3DFF}"] * 12)
        bottom = "\n".join(["CONTEXTO MUY LARGO 07/09"] * 12)
        result = subprocess.run(
            [
                sys.executable,
                str(COMPOSER),
                str(self.inputs[0]),
                str(output),
                "--top",
                top,
                "--bottom",
                bottom,
                "--preset",
                str(PRESET),
            ],
            cwd=self.work,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertGreaterEqual(metadata["top"]["y"], 90)
        self.assertLess(metadata["top"]["font_size"], 72)
        with Image.open(output) as image:
            self.assertEqual(image.size, (1080, 1920))

    def test_collage_styles(self):
        for style in ("grid", "bento", "mosaico", "puzzle", "jerarquico", "asimetrico"):
            output = self.work / f"{style}.png"
            result = subprocess.run(
                [
                    sys.executable,
                    str(COMPOSER),
                    *(str(path) for path in self.inputs),
                    str(output),
                    "--top",
                    "TITULAR",
                    "--bottom",
                    "CONTEXTO",
                    "--preset",
                    str(PRESET),
                    "--style",
                    style,
                ],
                cwd=self.work,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, f"{style}: {result.stderr}")
            with Image.open(output) as image:
                self.assertEqual(image.size, (1080, 1920))

    def test_six_mixed_images_and_output_formats(self):
        inputs = []
        dimensions = [(1600, 900), (900, 1600), (1000, 1000), (1400, 800), (700, 1100), (1200, 1200)]
        for index, size in enumerate(dimensions):
            path = self.work / f"mixed-{index}.png"
            Image.new("RGB", size, (20 + index * 25, 60, 100 + index * 20)).save(path)
            inputs.append(path)

        expected_sizes = {"1:1": (1080, 1080), "4:5": (1080, 1350), "16:9": (1920, 1080), "9:16": (1080, 1920)}
        for output_format, expected_size in expected_sizes.items():
            output = self.work / f"mixed-{output_format.replace(':', '-')}.png"
            result = subprocess.run(
                [
                    sys.executable,
                    str(COMPOSER),
                    *(str(path) for path in inputs),
                    str(output),
                    "--top",
                    "NOVEDADES",
                    "--bottom",
                    "CONTEXTO",
                    "--preset",
                    str(PRESET),
                    "--format",
                    output_format,
                ],
                cwd=self.work,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, f"{output_format}: {result.stderr}")
            metadata = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertEqual(metadata["images"], 6)
            self.assertEqual(metadata["style"], "adaptive")
            self.assertEqual(
                metadata["orientations"],
                ["landscape", "portrait", "square", "landscape", "portrait", "square"],
            )
            with Image.open(output) as image:
                self.assertEqual(image.size, expected_size)

    def test_auto_uses_adaptive_for_same_orientation_collage(self):
        output = self.work / "same-orientation.png"
        result = subprocess.run(
            [
                sys.executable,
                str(COMPOSER),
                *(str(path) for path in self.inputs),
                str(output),
                "--top",
                "NOVEDADES",
                "--bottom",
                "CONTEXTO",
                "--preset",
                str(PRESET),
            ],
            cwd=self.work,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(metadata["style"], "adaptive")
        self.assertEqual(metadata["orientations"], ["landscape"] * 4)

    def test_adaptive_cells_keep_orientation_ratios(self):
        images = [
            Image.new("RGB", (1600, 900)),
            Image.new("RGB", (1000, 1000)),
            Image.new("RGB", (900, 1600)),
        ]
        cells, total_height = COMPOSER_MODULE.adaptive_layout(
            images,
            canvas_width=1080,
            content_width=900,
            max_height=600,
            gap=24,
        )

        self.assertAlmostEqual(cells[0][2] / cells[0][3], 16 / 9, places=5)
        self.assertAlmostEqual(cells[1][2] / cells[1][3], 1.0, places=5)
        self.assertAlmostEqual(cells[2][2] / cells[2][3], 9 / 16, places=5)
        self.assertLessEqual(max(y + h for _, y, _, h in cells), total_height + 0.01)

    def test_square_pair_stacks_on_portrait_canvas(self):
        images = [
            Image.new("RGB", (1000, 1000)),
            Image.new("RGB", (2160, 2160)),
        ]
        cells, total_height = COMPOSER_MODULE.adaptive_layout(
            images,
            canvas_width=1080,
            content_width=900,
            max_height=1000,
            gap=24,
            stack_square_pair=True,
        )

        self.assertAlmostEqual(cells[0][2] / cells[0][3], 1.0, places=5)
        self.assertAlmostEqual(cells[1][2] / cells[1][3], 1.0, places=5)
        self.assertAlmostEqual(cells[0][0], cells[1][0], places=5)
        self.assertGreater(cells[1][1], cells[0][1])
        self.assertLessEqual(max(y + h for _, y, _, h in cells), total_height + 0.01)

    def test_link_media_extraction_preserves_order(self):
        payload = {
            "media_extended": [
                {"type": "image", "url": "https://cdn.example/one.jpg"},
                {"type": "video", "thumbnail_url": "https://cdn.example/two.jpg"},
                {"type": "image", "url": "https://cdn.example/one.jpg"},
                {"type": "image", "url": "https://cdn.example/three.png"},
            ]
        }
        self.assertEqual(
            extract_media_urls(payload),
            [
                "https://cdn.example/one.jpg",
                "https://cdn.example/two.jpg",
                "https://cdn.example/three.png",
            ],
        )
        self.assertEqual(
            parse_x_status_url("https://x.com/example/status/123456"),
            ("example", "123456"),
        )
        self.assertEqual(
            extract_media_urls({
                "mediaURLs": [
                    "https://pbs.twimg.com/media/photo.jpg?format=jpg&name=small",
                ],
            }),
            ["https://pbs.twimg.com/media/photo.jpg?format=jpg&name=orig"],
        )


if __name__ == "__main__":
    unittest.main()
