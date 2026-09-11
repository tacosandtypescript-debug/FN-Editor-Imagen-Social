import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import ImageFont


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
SKILLS_ROOT = ROOT / "skills" / "media"
COMPOSER = BIN / "compose_image.py"
sys.path.insert(0, str(ROOT / "tests"))
from fixtures.images import make_image

SKILL_SPECS = {
    "vertical-image-editor": {
        "version": "2.2.0",
        "wrapper": SKILLS_ROOT / "vertical-image-editor" / "scripts" / "compose_image.py",
        "preset": SKILLS_ROOT / "vertical-image-editor" / "references" / "presets" / "fortnite_vertical_image.json",
        "size": (1080, 1920),
    },
    "square-image-editor": {
        "version": "1.1.0",
        "wrapper": SKILLS_ROOT / "square-image-editor" / "scripts" / "compose_image.py",
        "preset": SKILLS_ROOT / "square-image-editor" / "references" / "presets" / "fortnite_square_image.json",
        "size": (1080, 1080),
    },
    "editar-publicar-instagram": {
        "version": "1.2.0",
        "wrapper": None,
        "preset": None,
        "size": None,
    },
}


def load_composer_module():
    sys.path.insert(0, str(BIN))
    spec = importlib.util.spec_from_file_location("project_integrity_composer", COMPOSER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProjectIntegrityTests(unittest.TestCase):
    def test_required_runtime_files_exist(self):
        required = (
            ROOT / ".gitignore",
            ROOT / "requirements.txt",
            ROOT / "README.md",
            BIN / "compose_image.py",
            BIN / "fetch_media.py",
            BIN / "edit_link.py",
            BIN / "runtime_config.py",
            BIN / "preset.json",
            BIN / "preset_square.json",
            ROOT / "barlow_font" / "Barlow-BlackItalic.ttf",
            ROOT / "barlow_font" / "OFL.txt",
            BIN / "flags.html",
            BIN / "schedule.html",
            ROOT / "tests" / "fixtures" / "images.py",
            ROOT / ".github" / "workflows" / "test.yml",
            ROOT / "examples" / "historical" / "README.md",
            ROOT / "skills" / "README.md",
        )
        for path in required:
            with self.subTest(path=path):
                self.assertTrue(path.is_file(), path)

    def test_bundled_font_family_is_readable(self):
        fonts = sorted((ROOT / "barlow_font").glob("*.ttf"))
        self.assertEqual(len(fonts), 18)
        for font in fonts:
            with self.subTest(font=font.name):
                ImageFont.truetype(font, 16)

    def test_skill_manifests_and_assets_are_complete(self):
        required_manifest_keys = ("name:", "description:", "version:", "author:", "license:", "platforms:")
        for name, expected in SKILL_SPECS.items():
            skill_dir = SKILLS_ROOT / name
            manifest = skill_dir / "SKILL.md"
            self.assertTrue(manifest.is_file(), manifest)
            content = manifest.read_text(encoding="utf-8")
            self.assertTrue(content.startswith("---\n"), manifest)
            frontmatter = content.split("---\n", 2)[1]
            for key in required_manifest_keys:
                self.assertRegex(frontmatter, re.compile(rf"^{re.escape(key)}", re.MULTILINE))
            self.assertRegex(
                frontmatter,
                re.compile(rf"^name:\s*{re.escape(name)}\s*$", re.MULTILINE),
            )
            self.assertRegex(
                frontmatter,
                re.compile(
                    rf"^version:\s*{re.escape(expected['version'])}\s*$",
                    re.MULTILINE,
                ),
            )

            if expected["wrapper"]:
                wrapper = expected["wrapper"]
                preset = expected["preset"]
                self.assertTrue(wrapper.is_file(), wrapper)
                self.assertTrue(preset.is_file(), preset)
                wrapper_text = wrapper.read_text(encoding="utf-8")
                self.assertIn("bin", wrapper_text)
                self.assertIn("runpy.run_path", wrapper_text)
                config = json.loads(preset.read_text(encoding="utf-8"))
                for key in (
                    "canvas",
                    "gap",
                    "font",
                    "font_size",
                    "min_font_size",
                    "text_color",
                    "outline_width",
                    "foreground_max_width",
                    "foreground_max_height",
                    "corner_radius",
                    "grid_gap",
                    "grid_padding",
                    "text_margin",
                    "outer_margin",
                    "palette",
                    "watermark",
                    "text_shadow",
                    "shadow",
                    "background_blur",
                    "background_dim",
                ):
                    self.assertIn(key, config, f"{preset}: falta {key}")
                self.assertEqual(
                    (config["canvas"]["width"], config["canvas"]["height"]),
                    expected["size"],
                )
                font = ROOT / config["font"]
                self.assertTrue(font.is_file(), font)
            else:
                self.assertIn("IGPUB_DIR", content)
                self.assertIn("enqueue.py", content)
                self.assertIn("queue_worker.py", content)
                self.assertIn("queue/in", content)
                self.assertIsNone(re.search(r"/home/[^/]+/", content))
                self.assertIsNone(re.search(r"/Users/[^/]+/", content))
                self.assertIsNone(re.search(r"[A-Za-z]:\\\\", content))

    def test_presets_pass_the_canonical_validation(self):
        composer = load_composer_module()
        preset_paths = (
            BIN / "preset.json",
            BIN / "preset_square.json",
            SKILL_SPECS["vertical-image-editor"]["preset"],
            SKILL_SPECS["square-image-editor"]["preset"],
        )
        for preset in preset_paths:
            with self.subTest(preset=preset):
                config = composer.load_cfg(preset)
                composer.validate_config(config)

    def test_editing_skill_wrappers_expose_the_canonical_cli(self):
        for name in ("vertical-image-editor", "square-image-editor"):
            wrapper = SKILL_SPECS[name]["wrapper"]
            result = subprocess.run(
                [sys.executable, str(wrapper), "--help"],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            with self.subTest(skill=name):
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--max-images", result.stdout)
                self.assertIn("--background", result.stdout)
                self.assertIn("--style", result.stdout)

    def test_vertical_skill_wrapper_runs_with_its_own_preset(self):
        spec = SKILL_SPECS["vertical-image-editor"]
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = make_image(work / "source.png")
            output = work / "result.png"
            result = subprocess.run(
                [
                    sys.executable,
                    str(spec["wrapper"]),
                    str(source),
                    str(output),
                    "--top",
                    "TITULAR",
                    "--bottom",
                    "CONTEXTO",
                    "--preset",
                    str(spec["preset"]),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            metadata = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertEqual(
                (metadata["width"], metadata["height"]),
                spec["size"],
            )
            self.assertTrue(output.is_file())

    def test_optional_clipboard_help_is_available_without_x11(self):
        result = subprocess.run(
            [sys.executable, str(BIN / "clipboard_server.py"), "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("image", result.stdout)

    def test_dependency_file_is_fully_pinned(self):
        requirements = {
            line.strip()
            for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertEqual(requirements, {"Pillow==12.2.0", "python-xlib==0.33"})
        self.assertTrue(all("==" in requirement for requirement in requirements))

    def test_historical_outputs_are_not_test_fixtures(self):
        self.assertFalse((ROOT / "jobs").exists())
        self.assertTrue((ROOT / "examples" / "historical").is_dir())
        fixture_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "tests").rglob("*.py")
            if "__pycache__" not in path.parts and path != Path(__file__)
        )
        self.assertNotIn("ROOT / 'jobs'", fixture_text)
        self.assertNotIn('ROOT / "jobs"', fixture_text)


if __name__ == "__main__":
    unittest.main()
