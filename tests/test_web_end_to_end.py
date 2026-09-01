"""A complete offline coding task through the browser workbench HTTP API."""

from __future__ import annotations

import http.client
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from coding_agent.config import Settings
from coding_agent.domain import Message, ModelTurn, ToolCall
from coding_agent.web.manager import ACTIVE_STATUSES, RunManager
from coding_agent.web.server import create_server
from tests.fakes import ScriptedModel


TEST_TOKEN = "offline-workbench-token"


def assistant(content: str | None = None, *calls: ToolCall) -> ModelTurn:
    return ModelTurn(
        message=Message(role="assistant", content=content, tool_calls=tuple(calls))
    )


class WebWorkbenchEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.target = self.workspace / "value.py"
        self.target.write_text("VALUE = 1\n", encoding="utf-8")
        (self.workspace / "test_value.py").write_text(
            "import unittest\n"
            "from value import VALUE\n\n"
            "class ValueTests(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(VALUE, 2)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )

        self.secret = "private-web-e2e-key"
        self.model = ScriptedModel(
            [
                assistant(
                    None,
                    ToolCall(
                        "call_read",
                        "read_file",
                        json.dumps({"path": "value.py"}),
                    ),
                ),
                assistant(
                    None,
                    ToolCall(
                        "call_edit",
                        "edit_file",
                        json.dumps(
                            {
                                "path": "value.py",
                                "old_text": "VALUE = 1",
                                "new_text": "VALUE = 2",
                            }
                        ),
                    ),
                ),
                assistant(
                    None,
                    ToolCall(
                        "call_test",
                        "run_command",
                        json.dumps(
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
                            }
                        ),
                    ),
                ),
                assistant(f"Fixed the value and verified its test. {self.secret}"),
            ]
        )
        settings = Settings(
            api_key=self.secret,
            base_url="https://offline.invalid/v1",
            model="scripted-offline-model",
        )
        self.manager = RunManager(
            self.workspace,
            self.root / "model.env",
            settings_loader=lambda: settings,
            model_factory=lambda loaded_settings: self.model,
            approval_timeout_s=5,
        )
        self.server = create_server(
            self.manager,
            port=0,
            token=TEST_TOKEN,
        )
        self.port = self.server.server_address[1]
        self.server_thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self.server_thread.start()
        self.response_bodies: list[bytes] = []

    def tearDown(self) -> None:
        self.manager.close()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            run = self.manager.snapshot()["run"]
            if run is None or run["status"] not in ACTIVE_STATUSES:
                break
            self.manager.snapshot(
                after_revision=run["revision"],
                wait_s=0.1,
            )
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=5)
        self.temporary_directory.cleanup()

    def request_json(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        raw_body = (
            json.dumps(body, ensure_ascii=False).encode("utf-8")
            if body is not None
            else None
        )
        headers = {"X-Workbench-Token": TEST_TOKEN}
        if method == "POST":
            headers.update(
                {
                    "Content-Type": "application/json",
                    "Origin": f"http://127.0.0.1:{self.port}",
                }
            )

        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.port,
            timeout=10,
        )
        try:
            connection.request(method, path, body=raw_body, headers=headers)
            response = connection.getresponse()
            response_body = response.read()
            self.response_bodies.append(response_body)
            payload = json.loads(response_body)
            self.assertIsInstance(payload, dict)
            return response.status, payload
        finally:
            connection.close()

    def wait_for_run(
        self,
        predicate,
        *,
        timeout_s: float = 10,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        revision = -1
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.fail("Timed out waiting for the workbench HTTP state")
            query = urlencode(
                {
                    "after": revision,
                    "wait": min(remaining, 1.0),
                }
            )
            status, snapshot = self.request_json(
                "GET",
                f"/api/status?{query}",
            )
            self.assertEqual(status, 200)
            run = snapshot["run"]
            if run is not None and predicate(run):
                return run
            if run is not None:
                revision = run["revision"]

    def approve(self, run_id: str, approval_id: str) -> None:
        status, payload = self.request_json(
            "POST",
            f"/api/runs/{run_id}/approval",
            {"approval_id": approval_id, "approved": True},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"ok": True})

    def test_http_run_waits_for_both_approvals_and_finishes_without_secret(self) -> None:
        status, started = self.request_json(
            "POST",
            "/api/runs",
            {"task": f"Fix the failing value test without exposing {self.secret}"},
        )
        self.assertEqual(status, 202)
        run_id = started["run_id"]

        edit_waiting = self.wait_for_run(
            lambda run: run["status"] == "waiting_approval"
            and run["approval"] is not None
            and run["approval"]["tool_name"] == "edit_file"
        )
        self.assertEqual(self.target.read_text(encoding="utf-8"), "VALUE = 1\n")
        edit_approval = edit_waiting["approval"]
        self.assertEqual(edit_approval["risk"], "write")
        self.assertIn("value.py", edit_approval["summary"])

        self.approve(run_id, edit_approval["id"])

        command_waiting = self.wait_for_run(
            lambda run: run["status"] == "waiting_approval"
            and run["approval"] is not None
            and run["approval"]["tool_name"] == "run_command"
        )
        self.assertEqual(self.target.read_text(encoding="utf-8"), "VALUE = 2\n")
        command_approval = command_waiting["approval"]
        self.assertNotEqual(command_approval["id"], edit_approval["id"])
        self.assertEqual(command_approval["risk"], "execute")

        self.approve(run_id, command_approval["id"])

        completed = self.wait_for_run(
            lambda run: run["status"] == "completed"
        )
        self.assertEqual(completed["id"], run_id)
        self.assertEqual(
            completed["final_text"],
            "Fixed the value and verified its test. [REDACTED]",
        )
        self.assertIsNone(completed["error"])
        self.assertEqual(completed["stats"]["model_steps"], 4)
        self.assertEqual(completed["stats"]["tool_calls"], 3)
        self.assertEqual(
            [
                event["tool"]
                for event in completed["events"]
                if event["kind"] == "tool_call"
            ],
            ["read_file", "edit_file", "run_command"],
        )
        command_results = [
            event
            for event in completed["events"]
            if event["kind"] == "tool_result"
            and event["tool"] == "run_command"
        ]
        self.assertEqual(len(command_results), 1)
        self.assertIn("OK", command_results[0]["preview"])

        rendered_responses = b"\n".join(self.response_bodies).decode("utf-8")
        self.assertNotIn(self.secret, rendered_responses)


if __name__ == "__main__":
    unittest.main()
