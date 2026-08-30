"""Offline tests for the complete model-tool conversation loop."""

from __future__ import annotations

import json
import unittest
from collections.abc import Mapping
from typing import Any

from coding_agent.agent import Agent, AgentEventKind, AgentLimits, AgentStatus
from coding_agent.context import ContextLimits, ContextWindow
from coding_agent.domain import Message, ModelTurn, ToolCall
from coding_agent.model import ModelConnectionError
from coding_agent.tools import Tool, ToolRegistry, ToolResult
from tests.fakes import ScriptedModel


def assistant(
    content: str | None = None,
    *calls: ToolCall,
    finish_reason: str | None = None,
) -> ModelTurn:
    return ModelTurn(
        message=Message(role="assistant", content=content, tool_calls=tuple(calls)),
        finish_reason=finish_reason,
    )


def echo_tool(seen: list[str] | None = None) -> Tool:
    def echo(arguments: Mapping[str, Any]) -> ToolResult:
        text = arguments.get("text")
        if not isinstance(text, str):
            return ToolResult.failure("INVALID_ARGUMENTS", "text must be a string")
        if seen is not None:
            seen.append(text)
        return ToolResult.success({"text": text})

    return Tool(
        name="echo",
        description="Return the supplied text.",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        handler=echo,
    )


class AgentLoopTests(unittest.TestCase):
    def test_model_can_finish_without_a_tool(self) -> None:
        model = ScriptedModel([assistant("Task complete", finish_reason="stop")])

        result = Agent(model, ToolRegistry()).run("Do the task")

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(result.final_text, "Task complete")
        self.assertEqual(result.model_steps, 1)
        self.assertEqual([message.role for message in result.history], ["system", "user", "assistant"])

    def test_tool_call_and_result_are_sent_back_to_model(self) -> None:
        call = ToolCall("call_1", "echo", '{"text":"hello"}')
        model = ScriptedModel([assistant(None, call), assistant("Done")])

        result = Agent(model, ToolRegistry((echo_tool(),))).run("Echo hello")

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(result.tool_calls, 1)
        second_request = model.requests[1][0]
        self.assertEqual([message.role for message in second_request], ["system", "user", "assistant", "tool"])
        self.assertEqual(second_request[-1].tool_call_id, "call_1")
        self.assertEqual(json.loads(second_request[-1].content)["data"], {"text": "hello"})

    def test_multiple_calls_execute_in_model_order(self) -> None:
        seen: list[str] = []
        calls = (
            ToolCall("call_1", "echo", '{"text":"first"}'),
            ToolCall("call_2", "echo", '{"text":"second"}'),
        )
        model = ScriptedModel([assistant(None, *calls), assistant("Done")])

        result = Agent(model, ToolRegistry((echo_tool(seen),))).run("Echo twice")

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(seen, ["first", "second"])
        self.assertEqual(result.tool_calls, 2)

    def test_unknown_tool_error_can_be_seen_and_corrected(self) -> None:
        model = ScriptedModel(
            [
                assistant(None, ToolCall("call_1", "missing", "{}")),
                assistant("I recovered"),
            ]
        )

        result = Agent(model, ToolRegistry()).run("Use a tool")

        error_payload = json.loads(model.requests[1][0][-1].content)
        self.assertEqual(error_payload["error"]["code"], "UNKNOWN_TOOL")
        self.assertEqual(result.status, AgentStatus.COMPLETED)

    def test_empty_model_turn_is_a_protocol_error(self) -> None:
        result = Agent(
            ScriptedModel([assistant(None)]),
            ToolRegistry(),
        ).run("Do something")

        self.assertEqual(result.status, AgentStatus.PROTOCOL_ERROR)
        self.assertIn("neither text nor tool calls", result.error)

    def test_model_error_stops_cleanly(self) -> None:
        result = Agent(
            ScriptedModel([ModelConnectionError("offline")]),
            ToolRegistry(),
        ).run("Do something")

        self.assertEqual(result.status, AgentStatus.MODEL_ERROR)
        self.assertIn("offline", result.error)

    def test_max_steps_stops_an_infinite_tool_loop(self) -> None:
        turns = [
            assistant(None, ToolCall(f"call_{index}", "echo", '{"text":"again"}'))
            for index in range(2)
        ]

        result = Agent(
            ScriptedModel(turns),
            ToolRegistry((echo_tool(),)),
            limits=AgentLimits(max_steps=2),
        ).run("Keep going")

        self.assertEqual(result.status, AgentStatus.MAX_STEPS)
        self.assertEqual(result.model_steps, 2)
        self.assertEqual(result.tool_calls, 2)

    def test_tool_call_limit_prevents_partial_batch_execution(self) -> None:
        seen: list[str] = []
        calls = (
            ToolCall("call_1", "echo", '{"text":"first"}'),
            ToolCall("call_2", "echo", '{"text":"second"}'),
        )

        result = Agent(
            ScriptedModel([assistant(None, *calls)]),
            ToolRegistry((echo_tool(seen),)),
            limits=AgentLimits(max_tool_calls=1),
        ).run("Echo twice")

        self.assertEqual(result.status, AgentStatus.MAX_TOOL_CALLS)
        self.assertEqual(seen, [])
        self.assertEqual([message.tool_call_id for message in result.history[-2:]], ["call_1", "call_2"])

    def test_empty_task_is_rejected_before_model_call(self) -> None:
        with self.assertRaisesRegex(ValueError, "task cannot be empty"):
            Agent(ScriptedModel([]), ToolRegistry()).run("  ")

    def test_observer_receives_model_and_tool_progress(self) -> None:
        events = []
        call = ToolCall("call_1", "echo", '{"text":"hello"}')
        model = ScriptedModel([assistant(None, call), assistant("Done")])

        result = Agent(
            model,
            ToolRegistry((echo_tool(),)),
            observer=events.append,
        ).run("Echo hello")

        self.assertEqual(result.status, AgentStatus.COMPLETED)
        self.assertEqual(
            [event.kind for event in events],
            [
                AgentEventKind.MODEL_REQUEST,
                AgentEventKind.TOOL_CALL,
                AgentEventKind.TOOL_RESULT,
                AgentEventKind.MODEL_REQUEST,
            ],
        )

    def test_context_overflow_stops_before_an_invalid_model_request(self) -> None:
        model = ScriptedModel([])

        result = Agent(
            model,
            ToolRegistry(),
            context_window=ContextWindow(
                ContextLimits(
                    max_characters=4_000,
                    reserved_response_characters=1_000,
                )
            ),
        ).run("x" * 4_000)

        self.assertEqual(result.status, AgentStatus.CONTEXT_ERROR)
        self.assertEqual(model.requests, [])


if __name__ == "__main__":
    unittest.main()
