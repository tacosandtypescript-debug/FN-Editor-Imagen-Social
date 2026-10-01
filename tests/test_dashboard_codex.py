"""Tests del proveedor de análisis basado en el CLI de Codex.

El CLI real no se invoca nunca desde los tests: sería lento y consumiría la
suscripción del usuario en cada ejecución. Se comprueba el descubrimiento del
ejecutable, el modo de autenticación, el saneado de la respuesta y, sobre todo,
que la orden que se lanza es la prevista y con el sandbox en solo lectura.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.providers import analysis as analysis_providers  # noqa: E402
from dashboard.providers import codex_cli  # noqa: E402
from dashboard.providers.base import ProviderError  # noqa: E402


class DiscoveryTests(unittest.TestCase):
    def test_candidate_paths_are_unique_and_existing(self):
        paths = codex_cli.candidate_paths()
        self.assertEqual(len(paths), len(set(paths)))
        for path in paths:
            self.assertTrue(Path(path).is_file(), path)

    def test_finds_the_installed_cli_in_this_machine(self):
        """En este equipo el CLI está instalado; si no, el proveedor se declara no disponible."""
        executable = codex_cli.find_codex_executable()
        provider = codex_cli.CodexCliAnalysis()
        status = provider.status()
        if executable is None:
            self.assertFalse(status.available)
            self.assertIn("no se encontró", status.detail)
        else:
            self.assertTrue(Path(executable).is_file())
            self.assertTrue(status.available, status.detail)
            self.assertIn("Codex", status.detail)

    def test_override_wins(self):
        key = "DASHBOARD_CODEX_PATH"
        original = os.environ.get(key)
        self.addCleanup(
            lambda: os.environ.pop(key, None) if original is None else os.environ.__setitem__(key, original)
        )
        os.environ[key] = r"C:\ruta\inventada\codex.exe"
        self.assertEqual(codex_cli.candidate_paths(), [])

    def test_auth_mode_is_read_from_the_cli_state(self):
        mode = codex_cli.auth_mode()
        # En este equipo hay sesión; en otro puede no haberla.
        self.assertIn(mode, (None, "chatgpt", "apikey"))


class CommandLineTests(unittest.TestCase):
    """Se comprueba la orden exacta que se ejecutaría, con `subprocess` falseado."""

    def setUp(self):
        self.original_run = codex_cli.subprocess.run
        self.addCleanup(setattr, codex_cli.subprocess, "run", self.original_run)
        self.original_find = codex_cli.find_codex_executable
        self.addCleanup(setattr, codex_cli, "find_codex_executable", self.original_find)
        codex_cli.find_codex_executable = lambda: r"C:\fake\codex.exe"
        self.original_auth = codex_cli.auth_mode
        self.addCleanup(setattr, codex_cli, "auth_mode", self.original_auth)
        codex_cli.auth_mode = lambda: "chatgpt"
        self.seen = {}

    def _fake_run(self, payload, returncode=0):
        def runner(command, **kwargs):
            self.seen["command"] = command
            self.seen["kwargs"] = kwargs
            # Se escribe el archivo indicado por -o, como haría el CLI real.
            if "-o" in command:
                Path(command[command.index("-o") + 1]).write_text(
                    json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                )
            return subprocess.CompletedProcess(command, returncode, stdout="", stderr="")

        return runner

    def test_command_uses_a_read_only_sandbox_and_a_schema(self):
        codex_cli.subprocess.run = self._fake_run(
            {"top": "TITULAR {UNO|8B3DFF}", "bottom": "CONTEXTO", "hashtags": ["#a"]}
        )
        provider = codex_cli.CodexCliAnalysis()
        analysis = provider.analyse({"text": "una noticia", "source_handle": "cuenta"})

        command = self.seen["command"]
        self.assertEqual(command[0], r"C:\fake\codex.exe")
        self.assertEqual(command[1], "exec")
        # Nunca se permite escribir ni saltarse el sandbox.
        self.assertIn("--sandbox", command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)
        # Sesión efímera: no ensucia el historial del usuario.
        self.assertIn("--ephemeral", command)
        self.assertIn("--skip-git-repo-check", command)
        # Salida estructurada y a un archivo concreto.
        self.assertIn("--output-schema", command)
        self.assertIn("-o", command)
        self.assertEqual(Path(command[command.index("-o") + 1]).name, "salida.json")
        # Se ejecuta en una carpeta temporal, no en el repositorio.
        self.assertIn("-C", command)
        self.assertNotEqual(command[command.index("-C") + 1], str(ROOT))
        self.assertEqual(self.seen["kwargs"]["cwd"], command[command.index("-C") + 1])

    def test_result_is_sanitised_like_every_other_provider(self):
        codex_cli.subprocess.run = self._fake_run(
            {
                "top": "TIENDA {DE|ff0000} NUEVA",
                "bottom": "EL {CONTENIDO|123456}",
                "hashtags": ["#fortnite"],
                "suggested_format": "banana",
            }
        )
        analysis = analysis_providers.sanitize_analysis(
            codex_cli.CodexCliAnalysis().analyse({"text": "x"})
        )
        # La palabra funcional no queda coloreada aunque el modelo la marque.
        self.assertNotIn("{DE|", analysis.top.upper().replace(" ", ""))
        self.assertEqual(len(analysis.hashtags), 5)
        self.assertEqual(analysis.hashtags[0], "#khetzalgg")
        self.assertIn(analysis.suggested_format, analysis_providers.DEFAULT_FORMATS)
        analysis_providers.repo.compose_image().validate_text_markup(
            analysis.top, analysis.bottom
        )

    def test_provider_is_tagged_in_the_result(self):
        codex_cli.subprocess.run = self._fake_run({"top": "A", "bottom": "B", "hashtags": []})
        analysis = codex_cli.CodexCliAnalysis().analyse({"text": "x"})
        self.assertEqual(analysis.provider, "codex")
        self.assertIn("Codex", analysis.reasoning or "")

    def test_a_non_zero_exit_without_output_is_reported(self):
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="boom en codex")

        codex_cli.subprocess.run = runner
        with self.assertRaises(ProviderError) as context:
            codex_cli.CodexCliAnalysis().analyse({"text": "x"})
        self.assertIn("boom en codex", str(context.exception))

    def test_a_timeout_is_reported_with_the_setting_name(self):
        def runner(command, **kwargs):
            raise subprocess.TimeoutExpired(command, 1)

        codex_cli.subprocess.run = runner
        with self.assertRaises(ProviderError) as context:
            codex_cli.CodexCliAnalysis().analyse({"text": "x"})
        self.assertIn("DASHBOARD_CODEX_TIMEOUT", str(context.exception))

    def test_missing_output_file_is_reported(self):
        codex_cli.subprocess.run = lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout="", stderr=""
        )
        with self.assertRaises(ProviderError) as context:
            codex_cli.CodexCliAnalysis().analyse({"text": "x"})
        self.assertIn("no devolvió ningún mensaje final", str(context.exception))

    def test_unrecognisable_output_is_reported(self):
        codex_cli.subprocess.run = self._fake_run_raw("esto no es json")
        with self.assertRaises(ProviderError):
            codex_cli.CodexCliAnalysis().analyse({"text": "x"})

    def _fake_run_raw(self, text):
        def runner(command, **kwargs):
            if "-o" in command:
                Path(command[command.index("-o") + 1]).write_text(text, encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        return runner

    def test_the_model_can_be_forced(self):
        key = "DASHBOARD_CODEX_MODEL"
        original = os.environ.get(key)
        self.addCleanup(
            lambda: os.environ.pop(key, None) if original is None else os.environ.__setitem__(key, original)
        )
        os.environ[key] = "gpt-5-codex"
        codex_cli.subprocess.run = self._fake_run({"top": "A", "bottom": "B", "hashtags": []})
        codex_cli.CodexCliAnalysis().analyse({"text": "x"})
        command = self.seen["command"]
        self.assertIn("--model", command)
        self.assertEqual(command[command.index("--model") + 1], "gpt-5-codex")


class OutputSchemaTests(unittest.TestCase):
    def test_schema_requires_every_property(self):
        """Regresión: el modo estricto de OpenAI rechaza el esquema si falta alguno.

        Falló en real con `invalid_json_schema` porque `additionalProperties`
        era `false` y no todas las propiedades estaban en `required`.
        """
        schema = codex_cli.OUTPUT_SCHEMA
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(
            set(schema["required"]),
            set(schema["properties"].keys()),
            "con additionalProperties:false, 'required' debe listarlas todas",
        )
        # Y el esquema debe poder serializarse para el CLI.
        self.assertIn("top", json.dumps(schema))

    def test_schema_covers_the_fields_the_pipeline_needs(self):
        properties = codex_cli.OUTPUT_SCHEMA["properties"]
        for key in ("top", "bottom", "hashtags", "caption", "suggested_format", "reasoning"):
            with self.subTest(key=key):
                self.assertIn(key, properties)


class FailureMessageTests(unittest.TestCase):
    """El mensaje de error debe ser útil, no una llave suelta."""

    REAL_STDERR = """
Reading additional input from stdin...
OpenAI Codex v0.158.0-alpha.2.1
--------
workdir: C:\\Temp\\x
model: gpt-5.6-luna
--------
user
Eres el editor de tarjetas...
ERROR: {
  "type": "error",
  "error": {
    "type": "invalid_request_error",
    "code": "invalid_json_schema",
    "message": "Invalid schema for response_format: 'required' is required to be supplied"
  }
}
}
"""

    def test_extracts_the_json_message(self):
        message = codex_cli._describe_failure(self.REAL_STDERR, "", 1)  # noqa: SLF001
        self.assertIn("required", message)
        self.assertIn("invalid", message.lower())
        self.assertNotEqual(message.strip(), "}")

    def test_never_returns_a_lone_brace(self):
        message = codex_cli._describe_failure("}\n", "", 1)  # noqa: SLF001
        self.assertNotEqual(message.strip(), "}")
        self.assertTrue(message.strip())
        # Y explica el código de salida para poder buscar el problema.
        self.assertIn("1", message)

    def test_a_punctuation_only_line_is_not_used_as_the_message(self):
        for raw in ("}", "  \n}\n", "...", "----"):
            with self.subTest(raw=raw):
                message = codex_cli._describe_failure(raw, "", 3)  # noqa: SLF001
                self.assertNotEqual(message.strip(), raw.strip())
                self.assertIn("3", message)

    def test_falls_back_to_the_error_block(self):
        message = codex_cli._describe_failure("algo\nERROR: fallo grave aqui\n}", "", 2)  # noqa: SLF001
        self.assertIn("fallo grave", message)

    def test_reports_the_exit_code_when_there_is_no_output(self):
        message = codex_cli._describe_failure("", "", 7)  # noqa: SLF001
        self.assertIn("7", message)

    def test_the_provider_uses_this_message(self):
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr=self.REAL_STDERR)

        original = codex_cli.subprocess.run
        self.addCleanup(setattr, codex_cli.subprocess, "run", original)
        codex_cli.subprocess.run = runner
        original_find = codex_cli.find_codex_executable
        self.addCleanup(setattr, codex_cli, "find_codex_executable", original_find)
        codex_cli.find_codex_executable = lambda: r"C:\fake\codex.exe"
        original_auth = codex_cli.auth_mode
        self.addCleanup(setattr, codex_cli, "auth_mode", original_auth)
        codex_cli.auth_mode = lambda: "chatgpt"

        with self.assertRaises(ProviderError) as context:
            codex_cli.CodexCliAnalysis().analyse({"text": "x"})
        self.assertIn("is required to be supplied", str(context.exception))


class RegistryTests(unittest.TestCase):
    def test_codex_is_built_by_the_registry(self):
        provider = analysis_providers.build_provider("codex")
        self.assertEqual(provider.name, "codex")
        self.assertEqual(analysis_providers.build_provider("cli").name, "codex")

    def test_codex_appears_in_the_provider_list(self):
        names = [status.name for status in analysis_providers.available_providers()]
        self.assertIn("codex", names)
        self.assertEqual(names[0], "codex")

    def test_codex_is_the_default_provider(self):
        key = "DASHBOARD_ANALYSIS_PROVIDER"
        original = os.environ.pop(key, None)
        self.addCleanup(
            lambda: os.environ.pop(key, None) if original is None else os.environ.__setitem__(key, original)
        )
        from dashboard import config

        self.assertEqual(config.Settings().analysis_provider, "codex")


if __name__ == "__main__":
    unittest.main()
