"""Smoke tests for the command-line entry point."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from coding_agent.cli import _prompt_for_approval, main
from coding_agent.policy import ToolRisk


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CommandLineTests(unittest.TestCase):
    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        for name in (
            "CODING_AGENT_API_KEY",
            "CODING_AGENT_BASE_URL",
            "CODING_AGENT_MODEL",
            "CODING_AGENT_REQUEST_TIMEOUT",
        ):
            environment.pop(name, None)
        return subprocess.run(
            [sys.executable, "-m", "coding_agent", *arguments],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

    def test_help_is_available(self) -> None:
        result = self.run_cli("--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: coding-agent", result.stdout)

    def test_version_is_available(self) -> None:
        result = self.run_cli("--version")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "coding-agent 0.1.0")

    def test_doctor_reports_missing_configuration_without_traceback(self) -> None:
        result = self.run_cli("doctor", "--env-file", "missing.env")

        self.assertEqual(result.returncode, 2)
        self.assertIn("Configuration error", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_doctor_never_prints_secret(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "CODING_AGENT_API_KEY": "never-print-this",
                "CODING_AGENT_MODEL": "test-model",
                "CODING_AGENT_BASE_URL": "https://example.test/v1",
            },
            clear=True,
        ):
            with patch("builtins.print") as print_mock:
                exit_code = main(["doctor", "--env-file", "missing.env"])

        self.assertEqual(exit_code, 0)
        rendered = " ".join(str(call) for call in print_mock.call_args_list)
        self.assertNotIn("never-print-this", rendered)

    def test_read_approval_is_automatic(self) -> None:
        with patch("builtins.input") as input_mock:
            approved = _prompt_for_approval("read_file", ToolRisk.READ, {"path": "a.py"})

        self.assertTrue(approved)
        input_mock.assert_not_called()

    def test_side_effect_approval_defaults_to_no(self) -> None:
        with patch("builtins.input", return_value=""):
            approved = _prompt_for_approval(
                "write_file",
                ToolRisk.WRITE,
                {"path": "a.py", "content": "private text"},
            )

        self.assertFalse(approved)


if __name__ == "__main__":
    unittest.main()
