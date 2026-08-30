"""Tests for predictable and secret-safe configuration loading."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coding_agent.config import ConfigurationError, Settings, read_env_file


class SettingsTests(unittest.TestCase):
    def test_environment_overrides_dotenv_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory, ".env")
            env_file.write_text(
                "CODING_AGENT_API_KEY=file-secret\n"
                "CODING_AGENT_MODEL=file-model\n",
                encoding="utf-8",
            )

            settings = Settings.load(
                env={
                    "CODING_AGENT_API_KEY": "environment-secret",
                    "CODING_AGENT_MODEL": "environment-model",
                    "CODING_AGENT_BASE_URL": "https://example.test/v1/",
                },
                env_file=env_file,
            )

        self.assertEqual(settings.api_key, "environment-secret")
        self.assertEqual(settings.model, "environment-model")
        self.assertEqual(settings.base_url, "https://example.test/v1")

    def test_secret_is_hidden_from_repr(self) -> None:
        settings = Settings.load(
            env={
                "CODING_AGENT_API_KEY": "do-not-print-this",
                "CODING_AGENT_MODEL": "test-model",
            },
            env_file=None,
        )

        self.assertNotIn("do-not-print-this", repr(settings))

    def test_missing_required_setting_is_clear(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "API_KEY is required"):
            Settings.load(env={}, env_file=None)

    def test_invalid_timeout_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "between 0 and 300"):
            Settings.load(
                env={
                    "CODING_AGENT_API_KEY": "secret",
                    "CODING_AGENT_MODEL": "test-model",
                    "CODING_AGENT_REQUEST_TIMEOUT": "0",
                },
                env_file=None,
            )

    def test_dotenv_supports_comments_export_and_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory, ".env")
            env_file.write_text(
                "# local settings\n"
                "export CODING_AGENT_API_KEY='quoted secret'\n"
                'CODING_AGENT_MODEL="quoted-model"\n',
                encoding="utf-8",
            )

            values = read_env_file(env_file)

        self.assertEqual(values["CODING_AGENT_API_KEY"], "quoted secret")
        self.assertEqual(values["CODING_AGENT_MODEL"], "quoted-model")
