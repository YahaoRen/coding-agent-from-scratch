"""Offline tests for the browser workbench run manager."""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from coding_agent.config import ConfigurationError, Settings
from coding_agent.domain import Message, ModelTurn, ToolCall
from coding_agent.web.manager import RunManager, WorkbenchError
from tests.fakes import ScriptedModel


def assistant(content: str | None = None, *calls: ToolCall) -> ModelTurn:
    return ModelTurn(
        message=Message(role="assistant", content=content, tool_calls=tuple(calls))
    )


class WebRunManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.secret = "private-web-api-key"
        self.settings = Settings(
            api_key=self.secret,
            base_url="https://example.test/v1",
            model="test-model",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def manager(self, model: ScriptedModel) -> RunManager:
        return RunManager(
            self.root,
            self.root.parent / "model.env",
            settings_loader=lambda: self.settings,
            model_factory=lambda settings: model,
            approval_timeout_s=5,
        )

    def wait_for(
        self,
        manager: RunManager,
        predicate,
        *,
        timeout_s: float = 5,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        revision = -1
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.fail("Timed out waiting for workbench state")
            snapshot = manager.snapshot(
                after_revision=revision,
                wait_s=min(remaining, 1),
            )
            run = snapshot["run"]
            if run is not None and predicate(run):
                return run
            if run is not None:
                revision = run["revision"]

    def test_missing_configuration_starts_ui_but_rejects_run(self) -> None:
        def missing_settings() -> Settings:
            raise ConfigurationError("missing key")

        manager = RunManager(
            self.root,
            self.root / ".env",
            settings_loader=missing_settings,
        )

        snapshot = manager.snapshot()
        self.assertFalse(snapshot["configuration"]["ready"])
        self.assertNotIn(str(self.root), json.dumps(snapshot, ensure_ascii=False))
        with self.assertRaises(WorkbenchError) as raised:
            manager.start("Do the task")
        self.assertEqual(raised.exception.code, "CONFIGURATION_REQUIRED")

    def test_unreadable_configuration_is_reported_without_details(self) -> None:
        def unreadable_settings() -> Settings:
            raise OSError("private local path")

        manager = RunManager(
            self.root,
            self.root / ".env",
            settings_loader=unreadable_settings,
        )

        configuration = manager.snapshot()["configuration"]
        self.assertFalse(configuration["ready"])
        self.assertNotIn("private local path", configuration["message"])

    def test_simple_run_completes_and_never_exposes_secret(self) -> None:
        model = ScriptedModel([assistant(f"Done without {self.secret}")])
        manager = self.manager(model)

        run_id = manager.start(f"Do not print {self.secret}")
        run = self.wait_for(
            manager,
            lambda value: value["status"] == "completed",
        )

        self.assertEqual(run["id"], run_id)
        self.assertEqual(run["final_text"], "Done without [REDACTED]")
        rendered = json.dumps(manager.snapshot(), ensure_ascii=False)
        self.assertNotIn(self.secret, rendered)
        self.assertEqual(run["stats"]["model_steps"], 1)

    def test_edit_waits_for_approval_before_changing_file(self) -> None:
        target = self.root / "value.py"
        target.write_text("VALUE = 1\n", encoding="utf-8")
        model = ScriptedModel(
            [
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
                assistant("Updated the value."),
            ]
        )
        manager = self.manager(model)

        run_id = manager.start("Update the value")
        waiting = self.wait_for(
            manager,
            lambda value: value["status"] == "waiting_approval",
        )

        self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 1\n")
        approval = waiting["approval"]
        self.assertEqual(approval["tool_name"], "edit_file")
        self.assertIn("value.py", approval["summary"])
        manager.approve(run_id, approval["id"], True)
        with self.assertRaises(WorkbenchError) as repeated:
            manager.approve(run_id, approval["id"], False)
        self.assertEqual(repeated.exception.status, 409)

        completed = self.wait_for(
            manager,
            lambda value: value["status"] == "completed",
        )
        self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 2\n")
        self.assertEqual(completed["stats"]["tool_calls"], 1)
        self.assertTrue(
            any(event["kind"] == "approval_resolved" for event in completed["events"])
        )

    def test_rejected_edit_is_reported_to_model_without_changing_file(self) -> None:
        target = self.root / "value.py"
        target.write_text("VALUE = 1\n", encoding="utf-8")
        model = ScriptedModel(
            [
                assistant(
                    None,
                    ToolCall(
                        "call_edit",
                        "edit_file",
                        '{"path":"value.py","old_text":"1","new_text":"2"}',
                    ),
                ),
                assistant("The edit was not approved."),
            ]
        )
        manager = self.manager(model)

        run_id = manager.start("Update the value")
        waiting = self.wait_for(
            manager,
            lambda value: value["approval"] is not None,
        )
        manager.approve(run_id, waiting["approval"]["id"], False)
        self.wait_for(manager, lambda value: value["status"] == "completed")

        self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 1\n")
        tool_payload = json.loads(model.requests[1][0][-1].content)
        self.assertEqual(tool_payload["error"]["code"], "PERMISSION_DENIED")

    def test_command_requires_a_separate_approval(self) -> None:
        model = ScriptedModel(
            [
                assistant(
                    None,
                    ToolCall(
                        "call_command",
                        "run_command",
                        json.dumps(
                            {"argv": [sys.executable, "-c", "print('command ok')"]}
                        ),
                    ),
                ),
                assistant("Command finished."),
            ]
        )
        manager = self.manager(model)

        run_id = manager.start("Run a check")
        waiting = self.wait_for(
            manager,
            lambda value: value["approval"] is not None,
        )
        self.assertEqual(waiting["approval"]["risk"], "execute")
        manager.approve(run_id, waiting["approval"]["id"], True)
        completed = self.wait_for(
            manager,
            lambda value: value["status"] == "completed",
        )

        previews = "\n".join(event["preview"] for event in completed["events"])
        self.assertIn("command ok", previews)

    def test_approval_text_is_bounded_before_reaching_browser(self) -> None:
        long_argument = "x" * 10_000
        model = ScriptedModel(
            [
                assistant(
                    None,
                    ToolCall(
                        "call_command",
                        "run_command",
                        json.dumps({"argv": [long_argument]}),
                    ),
                )
            ]
        )
        manager = self.manager(model)

        run_id = manager.start("Preview a long command")
        waiting = self.wait_for(manager, lambda value: value["approval"] is not None)
        approval = waiting["approval"]

        self.assertLessEqual(len(approval["title"]), 120)
        self.assertLessEqual(len(approval["summary"]), 500)
        self.assertLessEqual(len(approval["preview"]), 4_100)
        self.assertLessEqual(len(approval["tool_name"]), 80)
        manager.cancel(run_id)
        self.wait_for(manager, lambda value: value["status"] == "cancelled")

    def test_cancel_wakes_pending_approval_and_prevents_edit(self) -> None:
        target = self.root / "value.py"
        target.write_text("VALUE = 1\n", encoding="utf-8")
        model = ScriptedModel(
            [
                assistant(
                    None,
                    ToolCall(
                        "call_edit",
                        "edit_file",
                        '{"path":"value.py","old_text":"1","new_text":"2"}',
                    ),
                )
            ]
        )
        manager = self.manager(model)

        run_id = manager.start("Update the value")
        self.wait_for(manager, lambda value: value["approval"] is not None)
        manager.cancel(run_id)
        cancelled = self.wait_for(
            manager,
            lambda value: value["status"] == "cancelled",
        )

        self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 1\n")
        self.assertIsNone(cancelled["approval"])
        with self.assertRaises(WorkbenchError) as raised:
            manager.cancel(run_id)
        self.assertEqual(raised.exception.code, "RUN_FINISHED")

    def test_close_is_idempotent_and_cancels_pending_run(self) -> None:
        model = ScriptedModel(
            [
                assistant(
                    None,
                    ToolCall(
                        "call_edit",
                        "write_file",
                        '{"path":"new.py","content":"VALUE = 1\\n"}',
                    ),
                )
            ]
        )
        manager = self.manager(model)
        manager.start("Create a file")
        self.wait_for(manager, lambda value: value["approval"] is not None)

        manager.close()
        manager.close()

        cancelled = manager.snapshot()["run"]
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertFalse((self.root / "new.py").exists())
        with self.assertRaises(WorkbenchError) as raised:
            manager.start("Start after close")
        self.assertEqual(raised.exception.code, "WORKBENCH_CLOSED")


if __name__ == "__main__":
    unittest.main()
