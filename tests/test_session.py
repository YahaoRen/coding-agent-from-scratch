"""Tests for optional immutable and redacted session transcripts."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from coding_agent.agent import AgentResult, AgentStatus
from coding_agent.domain import Message, ToolCall
from coding_agent.session import SessionStore


class SessionStoreTests(unittest.TestCase):
    def test_saves_metadata_and_full_tool_protocol_as_jsonl(self) -> None:
        call = ToolCall("call_1", "read_file", '{"path":"a.py"}')
        result = AgentResult(
            status=AgentStatus.COMPLETED,
            final_text="done",
            history=(
                Message(role="system", content="rules"),
                Message(role="user", content="task"),
                Message(role="assistant", content=None, tool_calls=(call,)),
                Message(role="tool", content='{"ok":true}', tool_call_id="call_1"),
                Message(role="assistant", content="done"),
            ),
            model_steps=2,
            tool_calls=1,
        )

        with tempfile.TemporaryDirectory() as directory:
            session_path = SessionStore(Path(directory, "sessions")).save(result)
            records = [
                json.loads(line)
                for line in session_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(records[0]["status"], "completed")
        self.assertEqual(records[0]["model_steps"], 2)
        self.assertEqual(len(records), 6)
        self.assertEqual(records[3]["message"]["tool_calls"][0]["id"], "call_1")
        self.assertEqual(records[4]["message"]["tool_call_id"], "call_1")

    def test_redacts_known_secrets_in_nested_messages_and_errors(self) -> None:
        secret = "private-api-key"
        result = AgentResult(
            status=AgentStatus.MODEL_ERROR,
            final_text="",
            history=(Message(role="user", content=f"accidental {secret}"),),
            model_steps=1,
            tool_calls=0,
            error=f"endpoint echoed {secret}",
        )

        with tempfile.TemporaryDirectory() as directory:
            session_path = SessionStore(Path(directory), secrets=(secret,)).save(result)
            saved_text = session_path.read_text(encoding="utf-8")

        self.assertNotIn(secret, saved_text)
        self.assertEqual(saved_text.count("[REDACTED]"), 2)

    def test_each_save_uses_a_new_immutable_file(self) -> None:
        result = AgentResult(
            status=AgentStatus.COMPLETED,
            final_text="done",
            history=(),
            model_steps=1,
            tool_calls=0,
        )

        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            first = store.save(result)
            second = store.save(result)

        self.assertNotEqual(first.name, second.name)


if __name__ == "__main__":
    unittest.main()
