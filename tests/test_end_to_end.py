"""A full offline coding task through the real CLI and HTTP adapter."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from coding_agent.cli import main


class ScriptedChatServer(ThreadingHTTPServer):
    responses: list[dict[str, Any]]
    requests: list[dict[str, Any]]
    authorization_headers: list[str | None]


class ChatHandler(BaseHTTPRequestHandler):
    server: ScriptedChatServer

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", "0"))
        request_body = json.loads(self.rfile.read(length))
        self.server.requests.append(request_body)
        self.server.authorization_headers.append(self.headers.get("Authorization"))

        if self.path != "/v1/chat/completions" or not self.server.responses:
            self.send_error(500)
            return
        response_body = json.dumps(self.server.responses.pop(0)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def log_message(self, format: str, *arguments: Any) -> None:
        return


def tool_turn(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }


class EndToEndCodingTaskTests(unittest.TestCase):
    def test_cli_reads_edits_tests_and_finishes(self) -> None:
        responses = [
            tool_turn("call_read", "read_file", {"path": "calculator.py"}),
            tool_turn(
                "call_edit",
                "edit_file",
                {
                    "path": "calculator.py",
                    "old_text": "return left - right",
                    "new_text": "return left + right",
                },
            ),
            tool_turn(
                "call_test",
                "run_command",
                {
                    "argv": [
                        sys.executable,
                        "-m",
                        "unittest",
                        "discover",
                        "-s",
                        ".",
                        "-v",
                    ]
                },
            ),
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Fixed calculator.py and verified the tests pass.",
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        ]
        server = ScriptedChatServer(("127.0.0.1", 0), ChatHandler)
        server.responses = responses
        server.requests = []
        server.authorization_headers = []
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                workspace = root / "workspace"
                workspace.mkdir()
                (workspace / "calculator.py").write_bytes(
                    b"def add(left, right):\n    return left - right\n"
                )
                (workspace / "test_calculator.py").write_bytes(
                    b"import unittest\n"
                    b"from calculator import add\n\n"
                    b"class CalculatorTests(unittest.TestCase):\n"
                    b"    def test_add(self):\n"
                    b"        self.assertEqual(add(2, 3), 5)\n\n"
                    b"if __name__ == '__main__':\n"
                    b"    unittest.main()\n"
                )
                env_file = root / ".env"
                env_file.write_text(
                    "CODING_AGENT_API_KEY=local-test-key\n"
                    f"CODING_AGENT_BASE_URL=http://127.0.0.1:{server.server_port}/v1\n"
                    "CODING_AGENT_MODEL=scripted-test-model\n",
                    encoding="utf-8",
                )
                stdout = io.StringIO()
                stderr = io.StringIO()

                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = main(
                        [
                            "run",
                            "Fix the failing calculator test.",
                            "--workspace",
                            str(workspace),
                            "--env-file",
                            str(env_file),
                            "--model-retries",
                            "0",
                            "--yes",
                        ]
                    )

                fixed_source = (workspace / "calculator.py").read_text(encoding="utf-8")
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)

        self.assertEqual(exit_code, 0, stderr.getvalue())
        self.assertIn("return left + right", fixed_source)
        self.assertIn("Completed", stdout.getvalue())
        self.assertIn("run_command: ok", stdout.getvalue())
        self.assertNotIn("local-test-key", stdout.getvalue() + stderr.getvalue())
        self.assertEqual(len(server.requests), 4)
        self.assertEqual(
            server.authorization_headers,
            ["Bearer local-test-key"] * 4,
        )
        self.assertEqual(
            [message["role"] for message in server.requests[-1]["messages"]],
            [
                "system",
                "user",
                "assistant",
                "tool",
                "assistant",
                "tool",
                "assistant",
                "tool",
            ],
        )
        final_tool_payload = json.loads(server.requests[-1]["messages"][-1]["content"])
        self.assertTrue(final_tool_payload["ok"])
        self.assertEqual(final_tool_payload["data"]["exit_code"], 0)


if __name__ == "__main__":
    unittest.main()
