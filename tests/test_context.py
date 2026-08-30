"""Tests for deterministic context trimming and tool-pair integrity."""

from __future__ import annotations

import unittest

from coding_agent.context import ContextLimits, ContextOverflowError, ContextWindow
from coding_agent.domain import Message, ToolCall


def base_history() -> list[Message]:
    return [
        Message(role="system", content="system rules"),
        Message(role="user", content="original task"),
    ]


def tool_group(index: int, payload_size: int = 80) -> list[Message]:
    call = ToolCall(
        id=f"call_{index}",
        name="read_file",
        arguments=f'{{"path":"file_{index}.txt"}}',
    )
    return [
        Message(role="assistant", content=None, tool_calls=(call,)),
        Message(
            role="tool",
            content='{"ok":true,"data":"' + (str(index) * payload_size) + '"}',
            tool_call_id=call.id,
        ),
    ]


class ContextWindowTests(unittest.TestCase):
    def test_small_history_is_returned_unchanged(self) -> None:
        history = base_history() + tool_group(1)

        request = ContextWindow().build(history)

        self.assertEqual(request, history)

    def test_old_groups_are_removed_but_recent_group_stays_complete(self) -> None:
        history = base_history()
        for index in range(8):
            history.extend(tool_group(index, payload_size=180))
        window = ContextWindow(
            ContextLimits(max_characters=4_000, reserved_response_characters=1_000)
        )

        request = window.build(history)

        self.assertEqual(request[0].role, "system")
        self.assertIn("Earlier context omitted", request[1].content)
        self.assertEqual(request[2], history[1])
        kept_call_ids = {
            call.id
            for message in request
            for call in message.tool_calls
        }
        kept_result_ids = {
            message.tool_call_id for message in request if message.role == "tool"
        }
        self.assertEqual(kept_call_ids, kept_result_ids)
        self.assertIn("call_7", kept_call_ids)
        self.assertNotIn("call_0", kept_call_ids)

    def test_tool_schema_size_is_part_of_budget(self) -> None:
        history = base_history() + tool_group(1, payload_size=100)
        tools = [{"type": "function", "description": "x" * 3_500}]
        window = ContextWindow(
            ContextLimits(max_characters=4_000, reserved_response_characters=1_000)
        )

        with self.assertRaisesRegex(ContextOverflowError, "Tool schemas"):
            window.build(history, tools)

    def test_newest_group_is_never_split_or_silently_dropped(self) -> None:
        history = base_history() + tool_group(1, payload_size=5_000)
        window = ContextWindow(
            ContextLimits(max_characters=4_000, reserved_response_characters=1_000)
        )

        with self.assertRaisesRegex(ContextOverflowError, "Newest complete"):
            window.build(history)

    def test_oversized_original_task_fails_clearly(self) -> None:
        history = [
            Message(role="system", content="rules"),
            Message(role="user", content="x" * 4_000),
            Message(role="assistant", content="later"),
        ]
        window = ContextWindow(
            ContextLimits(max_characters=4_000, reserved_response_characters=1_000)
        )

        with self.assertRaisesRegex(ContextOverflowError, "original task"):
            window.build(history)


if __name__ == "__main__":
    unittest.main()
