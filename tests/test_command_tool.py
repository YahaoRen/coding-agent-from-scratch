"""Tests for bounded, non-shell command execution."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from coding_agent.domain import ToolCall
from coding_agent.policy import AllowAllPolicy
from coding_agent.tools import ToolRegistry
from coding_agent.tools.command import create_command_tool
from coding_agent.workspace import Workspace


class CommandToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.allow = AllowAllPolicy()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def make_registry(
        self,
        *secrets: str,
        stop_requested=None,
    ) -> ToolRegistry:
        return ToolRegistry(
            (
                create_command_tool(
                    Workspace(self.root),
                    secrets=secrets,
                    stop_requested=stop_requested,
                ),
            )
        )

    def execute(
        self,
        arguments: dict,
        *,
        approved: bool = True,
        secrets: tuple[str, ...] = (),
        stop_requested=None,
    ):
        policy = self.allow if approved else None
        return self.make_registry(
            *secrets,
            stop_requested=stop_requested,
        ).execute(
            ToolCall("call_1", "run_command", json.dumps(arguments)),
            policy,
        )

    def test_command_is_denied_by_default(self) -> None:
        sentinel = self.root / "sentinel.txt"

        result = self.execute(
            {
                "argv": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('sentinel.txt').write_text('bad')",
                ]
            },
            approved=False,
        )

        self.assertEqual(result.error.code, "PERMISSION_DENIED")
        self.assertFalse(sentinel.exists())

    def test_success_runs_in_workspace_without_shell(self) -> None:
        result = self.execute(
            {
                "argv": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; print(Path.cwd().name)",
                ]
            }
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.data["exit_code"], 0)
        self.assertEqual(result.data["stdout"].strip(), self.root.name)
        self.assertFalse(result.data["timed_out"])

    def test_nonzero_exit_is_returned_as_tool_error(self) -> None:
        result = self.execute(
            {"argv": [sys.executable, "-c", "import sys; sys.exit(7)"]}
        )

        self.assertEqual(result.error.code, "NONZERO_EXIT")
        self.assertEqual(result.data["exit_code"], 7)
        self.assertTrue(result.error.retryable)

    def test_timeout_stops_command(self) -> None:
        result = self.execute(
            {
                "argv": [sys.executable, "-c", "import time; time.sleep(10)"],
                "timeout_seconds": 1,
            }
        )

        self.assertEqual(result.error.code, "COMMAND_TIMEOUT")
        self.assertTrue(result.data["timed_out"])
        self.assertLess(result.data["duration_ms"], 7000)

    def test_cancel_before_start_does_not_launch_command(self) -> None:
        stop = threading.Event()
        stop.set()

        with patch("coding_agent.tools.command._start_process") as start_process:
            result = self.execute(
                {
                    "argv": [sys.executable, "-c", "import time; time.sleep(10)"],
                    "timeout_seconds": 30,
                },
                stop_requested=stop.is_set,
            )

        self.assertEqual(result.error.code, "COMMAND_CANCELLED")
        start_process.assert_not_called()
        self.assertTrue(result.data["cancelled"])
        self.assertFalse(result.data["timed_out"])
        self.assertLess(result.data["duration_ms"], 7000)

    def test_cancel_stops_running_command(self) -> None:
        stop = threading.Event()
        timer = threading.Timer(0.2, stop.set)
        timer.start()
        try:
            result = self.execute(
                {
                    "argv": [sys.executable, "-c", "import time; time.sleep(10)"],
                    "timeout_seconds": 30,
                },
                stop_requested=stop.is_set,
            )
        finally:
            timer.cancel()

        self.assertEqual(result.error.code, "COMMAND_CANCELLED")
        self.assertTrue(result.data["cancelled"])
        self.assertFalse(result.data["timed_out"])
        self.assertLess(result.data["duration_ms"], 7000)

    def test_large_stdout_and_stderr_are_drained_and_truncated(self) -> None:
        code = (
            "import sys; "
            "sys.stdout.write('A' * 100000); "
            "sys.stderr.write('B' * 100000)"
        )

        result = self.execute({"argv": [sys.executable, "-c", code]})

        self.assertTrue(result.ok)
        self.assertTrue(result.data["stdout_truncated"])
        self.assertTrue(result.data["stderr_truncated"])
        self.assertGreater(result.data["stdout_omitted_bytes"], 0)
        self.assertIn("omitted", result.data["stdout"])

    def test_model_api_key_is_not_inherited_by_child_process(self) -> None:
        code = "import os; print(os.getenv('CODING_AGENT_API_KEY', 'missing'))"
        with patch.dict(os.environ, {"CODING_AGENT_API_KEY": "private-key"}):
            result = self.execute({"argv": [sys.executable, "-c", code]})

        self.assertTrue(result.ok)
        self.assertEqual(result.data["stdout"].strip(), "missing")

    def test_known_secret_is_redacted_from_output(self) -> None:
        secret = "known-secret-value"
        result = self.execute(
            {"argv": [sys.executable, "-c", f"print({secret!r})"]},
            secrets=(secret,),
        )

        self.assertTrue(result.ok)
        self.assertNotIn(secret, result.data["stdout"])
        self.assertIn("[REDACTED]", result.data["stdout"])

    def test_invalid_utf8_and_terminal_color_codes_are_sanitized(self) -> None:
        code = "import sys; sys.stdout.buffer.write(b'\\x1b[31mred\\x1b[0m\\xff')"

        result = self.execute({"argv": [sys.executable, "-c", code]})

        self.assertTrue(result.ok)
        self.assertNotIn("\x1b", result.data["stdout"])
        self.assertIn("red", result.data["stdout"])
        self.assertIn("�", result.data["stdout"])

    def test_python_child_uses_utf8_for_chinese_output(self) -> None:
        result = self.execute(
            {
                "argv": [
                    sys.executable,
                    "-c",
                    "print('中文路径')",
                ]
            }
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.data["stdout"].strip(), "中文路径")

    def test_invalid_argv_is_rejected_before_start(self) -> None:
        empty = self.execute({"argv": []})
        wrong_item = self.execute({"argv": [sys.executable, 3]})

        self.assertEqual(empty.error.code, "INVALID_ARGUMENTS")
        self.assertEqual(wrong_item.error.code, "INVALID_ARGUMENTS")


if __name__ == "__main__":
    unittest.main()
