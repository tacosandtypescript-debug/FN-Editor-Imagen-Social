import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "tests"))

from fetch_media import (
    download_link,
    download_link_info,
    extract_media_urls,
    extract_post_text,
    parse_x_status_url,
)
from fixtures.images import image_bytes, make_image
import edit_link as edit_link_module
import prepare_batch as prepare_batch_module


COMPOSER = ROOT / "bin" / "compose_image.py"
EDIT_LINK = ROOT / "bin" / "edit_link.py"
SKILL_COMPOSER = ROOT / "skills" / "media" / "vertical-image-editor" / "scripts" / "compose_image.py"
SQUARE_SKILL_COMPOSER = ROOT / "skills" / "media" / "square-image-editor" / "scripts" / "compose_image.py"
PRESET = ROOT / "bin" / "preset.json"
SQUARE_PRESET = ROOT / "skills" / "media" / "square-image-editor" / "references" / "presets" / "fortnite_square_image.json"
VERTICAL_PRESET = ROOT / "skills" / "media" / "vertical-image-editor" / "references" / "presets" / "fortnite_vertical_image.json"
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
            make_image(
                path,
                size=(320 + index * 20, 180 + index * 10),
                color=(40 + index * 30, 80, 140),
            )
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

    def test_jpeg_output_is_written_as_rgb(self):
        output = self.work / "main.jpg"
        self.run_composer(COMPOSER, output)
        with Image.open(output) as image:
            self.assertEqual((image.width, image.height, image.format), (1080, 1920, "JPEG"))
            self.assertEqual(image.mode, "RGB")

    def test_load_image_applies_exif_orientation(self):
        source = self.work / "oriented.jpg"
        image = Image.new("RGB", (20, 30), (20, 40, 80))
        exif = image.getexif()
        exif[274] = 6  # Rotate 90 degrees clockwise when displayed.
        image.save(source, exif=exif.tobytes())

        loaded = COMPOSER_MODULE.load_image(source)

        self.assertEqual(loaded.size, (30, 20))

    def test_4k_resolution_scales_canvas_and_layout(self):
        output = self.work / "4k.png"
        metadata = self.run_composer(
            COMPOSER,
            output,
            "--resolution",
            "4k",
            "--backend",
            "cpu",
        )

        self.assertEqual((metadata["width"], metadata["height"]), (2160, 3840))
        self.assertEqual(metadata["resolution"], "4k")
        self.assertEqual(metadata["resolution_scale"], 2)
        with Image.open(output) as image:
            self.assertEqual((image.width, image.height, image.mode), (2160, 3840, "RGBA"))

    def test_auto_format_infers_horizontal_canvas_at_4k(self):
        output = self.work / "auto-horizontal.png"
        metadata = self.run_composer(
            COMPOSER,
            output,
            "--format",
            "auto",
            "--resolution",
            "4k",
            "--backend",
            "cpu",
        )

        self.assertEqual((metadata["width"], metadata["height"]), (3840, 2160))
        self.assertEqual(metadata["requested_format"], "auto")
        self.assertEqual(metadata["output_format"], "16:9")
        with Image.open(output) as image:
            self.assertEqual((image.width, image.height, image.mode), (3840, 2160, "RGBA"))

    def test_auto_format_uses_square_for_mixed_tie(self):
        self.assertEqual(
            COMPOSER_MODULE.infer_output_format([(1600, 900), (900, 1600)]),
            "1:1",
        )
        self.assertEqual(
            COMPOSER_MODULE.infer_output_format([(900, 1600), (900, 1600), (1600, 900)]),
            "9:16",
        )
        self.assertEqual(
            COMPOSER_MODULE.infer_output_format([(1000, 1000), (1600, 900)]),
            "1:1",
        )

    def test_text_markup_rejects_empty_text_stopwords_and_excess_colors(self):
        with self.assertRaisesRegex(ValueError, "superior.*vacío"):
            COMPOSER_MODULE.validate_text_markup("   ", "CONTEXTO")
        with self.assertRaisesRegex(ValueError, "palabra funcional"):
            COMPOSER_MODULE.validate_text_markup("NOVEDADES {de|8B3DFF}", "CONTEXTO")
        with self.assertRaisesRegex(ValueError, "dos colores"):
            COMPOSER_MODULE.validate_text_markup(
                "{FORTNITE|8B3DFF}", "{NOTICIAS|FF8A00} {TEMPORADA|E000FF}"
            )

    def test_prepare_batch_preserves_one_output_group_per_publication(self):
        def fake_download(url, output_dir, max_images):
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            image_count = 2 if url.endswith("/uno") else 1
            images = []
            for index in range(1, image_count + 1):
                image_path = output_dir / f"{index:02d}.png"
                make_image(image_path, size=(640, 360))
                images.append({"path": str(image_path), "url": url, "format": "PNG", "width": 640, "height": 360})
            return {
                "url": url,
                "source_type": "x-post",
                "post_text": f"texto de {url}",
                "images": images,
            }

        with patch.object(prepare_batch_module, "download_link_info", side_effect=fake_download):
            manifest = prepare_batch_module.download_batch_info(
                ["https://x.test/uno", "https://x.test/dos"],
                self.work / "batch",
                max_images=4,
            )

        self.assertEqual(manifest["batch_type"], "publication-set")
        self.assertEqual(manifest["source_count"], 2)
        self.assertEqual([source["source_index"] for source in manifest["sources"]], [1, 2])
        self.assertEqual([source["count"] for source in manifest["sources"]], [2, 1])
        self.assertEqual(
            [source["recommended_format"] for source in manifest["sources"]],
            ["16:9", "16:9"],
        )
        self.assertEqual(
            [source["recommended_4k_dimensions"] for source in manifest["sources"]],
            [[3840, 2160], [3840, 2160]],
        )
        self.assertEqual([item["source_index"] for item in manifest["images"]], [1, 1, 2])
        self.assertTrue(manifest["editorial_contract"]["one_output_per_source"])
        self.assertTrue(manifest["editorial_contract"]["separate_documents"])
        self.assertEqual(manifest["editorial_contract"]["caption_hashtag_count_per_source"], 5)
        self.assertEqual(manifest["editorial_contract"]["required_brand_hashtag"], "#khetzalgg")

    def test_square_skill_wrapper_matches_canonical_compositor(self):
        square_inputs = []
        for index in range(2):
            path = self.work / f"square-{index}.png"
            Image.new("RGB", (1000 + index * 100, 1000 + index * 100), (80, 40 + index * 50, 160)).save(path)
            square_inputs.append(path)

        def run(script, output):
            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    *(str(path) for path in square_inputs),
                    str(output),
                    "--top",
                    "TARJETA {CUADRADA|8B3DFF}",
                    "--bottom",
                    "PRUEBA 1:1",
                    "--preset",
                    str(SQUARE_PRESET),
                ],
                cwd=self.work,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout.strip().splitlines()[-1])

        canonical_output = self.work / "square-canonical.png"
        skill_output = self.work / "square-skill.png"
        canonical_meta = run(COMPOSER, canonical_output)
        skill_meta = run(SQUARE_SKILL_COMPOSER, skill_output)

        self.assertEqual(canonical_meta["width"], 1080)
        self.assertEqual(canonical_meta["height"], 1080)
        self.assertEqual(canonical_meta["style"], "adaptive")
        self.assertEqual(canonical_meta["orientations"], ["square", "square"])
        self.assertEqual(canonical_meta["style"], skill_meta["style"])
        self.assertEqual(
            hashlib.sha256(canonical_output.read_bytes()).digest(),
            hashlib.sha256(skill_output.read_bytes()).digest(),
        )
        with Image.open(skill_output) as image:
            self.assertEqual((image.width, image.height, image.format), (1080, 1080, "PNG"))
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

    def test_equal_pair_cells_make_mixed_square_pair_uniform(self):
        images = [
            Image.new("RGB", (1080, 1350)),
            Image.new("RGB", (1178, 1178)),
        ]
        cells, total_height = COMPOSER_MODULE.adaptive_layout(
            images,
            canvas_width=1080,
            content_width=960,
            max_height=650,
            gap=24,
            equal_pair_cells=True,
        )

        self.assertEqual(len(cells), 2)
        self.assertAlmostEqual(cells[0][2], cells[1][2], places=5)
        self.assertAlmostEqual(cells[0][3], cells[1][3], places=5)
        self.assertAlmostEqual(cells[0][2] / cells[0][3], 1.0, places=5)
        self.assertAlmostEqual(cells[0][1], cells[1][1], places=5)
        self.assertAlmostEqual(
            cells[1][0] - (cells[0][0] + cells[0][2]),
            24,
            places=5,
        )
        self.assertLessEqual(max(y + h for _, y, _, h in cells), total_height + 0.01)

    def test_equal_square_source_keeps_mixed_image_complete(self):
        image = Image.new("RGB", (4, 6), (20, 20, 20))
        image.putpixel((0, 0), (255, 0, 0))
        image.putpixel((3, 0), (0, 255, 0))
        image.putpixel((0, 5), (0, 0, 255))
        image.putpixel((3, 5), (255, 255, 0))

        normalized = COMPOSER_MODULE.equal_square_source(
            image,
            {"background_blur": 0, "background_dim": 1.0},
        )

        self.assertEqual(normalized.size, (6, 6))
        self.assertEqual(normalized.getpixel((1, 0)), (255, 0, 0))
        self.assertEqual(normalized.getpixel((4, 0)), (0, 255, 0))
        self.assertEqual(normalized.getpixel((1, 5)), (0, 0, 255))
        self.assertEqual(normalized.getpixel((4, 5)), (255, 255, 0))

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

    def test_download_link_accepts_png_and_jpeg(self):
        payloads = {
            "https://cdn.example/card.png": image_bytes("PNG"),
            "https://cdn.example/card.jpg": image_bytes("JPEG"),
        }

        def fake_request(url, *args, **kwargs):
            return payloads[url]

        with patch("fetch_media.request_bytes", side_effect=fake_request):
            for suffix, expected_format in ((".png", "PNG"), (".jpg", "JPEG")):
                output_dir = self.work / f"download{suffix}"
                results = download_link(
                    f"https://cdn.example/card{suffix}", output_dir
                )
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0]["format"], expected_format)
                destination = Path(results[0]["path"])
                self.assertEqual(destination.suffix, suffix)
                with Image.open(destination) as image:
                    self.assertEqual(image.format, expected_format)
                    self.assertEqual(image.size, (32, 16))

    def test_invalid_preset_is_rejected_before_rendering(self):
        preset = self.work / "invalid.json"
        preset.write_text('{"canvas": {"width": 0}}', encoding="utf-8")

        with self.assertRaises(ValueError) as context:
            COMPOSER_MODULE.load_cfg(preset)

        self.assertIn("positivos", str(context.exception))

    def test_bundled_presets_pass_schema_validation(self):
        for preset in (PRESET, VERTICAL_PRESET, SQUARE_PRESET):
            with self.subTest(preset=preset):
                config = COMPOSER_MODULE.load_cfg(preset)
                self.assertIn("canvas", config)
                self.assertGreater(config["canvas"]["width"], 0)
                self.assertGreater(config["canvas"]["height"], 0)

    def test_format_dimensions_rejects_extreme_output(self):
        self.assertEqual(
            COMPOSER_MODULE.format_dimensions("4:5"),
            (1080, 1350),
        )
        with self.assertRaises(ValueError):
            COMPOSER_MODULE.format_dimensions("1:100")

    def test_max_image_count_is_enforced(self):
        output = self.work / "too-many.png"
        result = subprocess.run(
            [
                sys.executable,
                str(COMPOSER),
                str(self.inputs[0]),
                str(self.inputs[1]),
                str(self.inputs[2]),
                str(output),
                "--top",
                "TITULAR",
                "--bottom",
                "CONTEXTO",
                "--preset",
                str(PRESET),
                "--max-images",
                "2",
            ],
            cwd=self.work,
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("maximo configurado", result.stderr)

    def test_unsupported_explicit_layout_is_rejected(self):
        extra = self.work / "input-4.png"
        make_image(extra, size=(400, 220), color=(90, 80, 140))
        output = self.work / "unsupported.png"
        result = subprocess.run(
            [
                sys.executable,
                str(COMPOSER),
                *(str(path) for path in [*self.inputs, extra]),
                str(output),
                "--top",
                "TITULAR",
                "--bottom",
                "CONTEXTO",
                "--preset",
                str(PRESET),
                "--style",
                "bento",
            ],
            cwd=self.work,
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no admite 5 imagenes", result.stderr)

    def test_edit_link_exposes_composer_options(self):
        result = subprocess.run(
            [sys.executable, str(EDIT_LINK), "--help"],
            cwd=self.work,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for option in ("--background", "--style", "--format", "--fit", "--backend", "--resolution", "--max-images"):
            self.assertIn(option, result.stdout)

    def test_edit_link_forwards_composer_options(self):
        args = edit_link_module.argparse.Namespace(
            output=self.work / "out.png",
            top="TITULAR",
            bottom="CONTEXTO",
            preset=PRESET,
            style="adaptive",
            fit="contain",
            max_images=7,
            output_format="1:1",
            background=self.work / "background.jpg",
            backend="gpu",
        )

        command = edit_link_module.build_composer_command(
            args, [str(self.inputs[0]), str(self.inputs[1])]
        )

        self.assertIn("--background", command)
        self.assertIn(str(args.background), command)
        self.assertIn("--max-images", command)
        self.assertIn("7", command)
        self.assertIn("--format", command)
        self.assertIn("1:1", command)
        self.assertIn("--fit", command)
        self.assertIn("contain", command)
        self.assertIn("--backend", command)
        self.assertIn("gpu", command)
        self.assertIn("--resolution", command)
        self.assertIn("native", command)

    def test_edit_link_auto_selects_the_matching_bundled_preset(self):
        args = edit_link_module.argparse.Namespace(
            preset=PRESET,
            output_format="auto",
        )
        link_info = {
            "images": [
                {"width": 1600, "height": 900},
                {"width": 1400, "height": 800},
            ],
        }

        selected = edit_link_module.select_auto_preset(args, link_info)

        self.assertEqual(selected, ROOT / "bin" / "preset_horizontal.json")

    def test_relative_preset_is_resolved_from_repository_root(self):
        output = self.work / "relative-preset.png"
        result = subprocess.run(
            [
                sys.executable,
                str(COMPOSER),
                str(self.inputs[0]),
                str(output),
                "--top",
                "TITULAR",
                "--bottom",
                "CONTEXTO",
                "--preset",
                "bin/preset.json",
            ],
            cwd=self.work,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(output.is_file())

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

    def test_fxtwitter_shape_exposes_media_and_post_text(self):
        payload = {
            "tweet": {
                "text": "Nueva noticia de Fortnite",
                "media": {
                    "all": [
                        {"type": "photo", "url": "https://cdn.example/one.jpg"},
                        {"type": "video", "thumbnail_url": "https://cdn.example/two.jpg"},
                    ],
                },
            },
        }
        self.assertEqual(
            extract_media_urls(payload),
            ["https://cdn.example/one.jpg", "https://cdn.example/two.jpg"],
        )
        self.assertEqual(extract_post_text(payload), "Nueva noticia de Fortnite")

    def test_download_link_info_returns_post_text_for_agent(self):
        post_payload = {
            "tweet": {
                "text": "El evento llega esta semana",
                "mediaURLs": ["https://cdn.example/event.jpg"],
            },
        }

        def fake_request(url, *args, **kwargs):
            if url == "https://api.vxtwitter.com/example/status/123456":
                return json.dumps(post_payload).encode("utf-8")
            if url == "https://cdn.example/event.jpg":
                return image_bytes("JPEG")
            raise AssertionError(f"URL inesperada: {url}")

        with patch("fetch_media.request_bytes", side_effect=fake_request):
            info = download_link_info(
                "https://x.com/example/status/123456",
                self.work / "post-download",
            )

        self.assertEqual(info["source_type"], "x-post")
        self.assertEqual(info["post_text"], "El evento llega esta semana")
        self.assertEqual(len(info["images"]), 1)
        self.assertTrue(Path(info["images"][0]["path"]).is_file())

    def test_fetch_post_data_tries_second_mirror_for_missing_text(self):
        first_payload = {
            "tweet": {"mediaURLs": ["https://cdn.example/event.jpg"]},
        }
        second_payload = {
            "tweet": {
                "text": "El evento llega esta semana",
                "mediaURLs": ["https://cdn.example/event.jpg"],
            },
        }

        def fake_request(url, *args, **kwargs):
            if url == "https://api.vxtwitter.com/example/status/123456":
                return json.dumps(first_payload).encode("utf-8")
            if url == "https://api.fxtwitter.com/example/status/123456":
                return json.dumps(second_payload).encode("utf-8")
            if url == "https://cdn.example/event.jpg":
                return image_bytes("JPEG")
            raise AssertionError(f"URL inesperada: {url}")

        with patch("fetch_media.request_bytes", side_effect=fake_request):
            info = download_link_info(
                "https://x.com/example/status/123456",
                self.work / "mirror-fallback",
            )

        self.assertEqual(info["post_text"], "El evento llega esta semana")
        self.assertEqual(info["images"][0]["url"], "https://cdn.example/event.jpg")


if __name__ == "__main__":
    unittest.main()
