"""Tests for tool registration, validation, and error recovery."""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from typing import Any

from coding_agent.domain import ToolCall
from coding_agent.tools import Tool, ToolArgumentError, ToolRegistry, ToolResult


def add_numbers(arguments: Mapping[str, Any]) -> ToolResult:
    left = arguments.get("left")
    right = arguments.get("right")
    if not isinstance(left, int) or not isinstance(right, int):
        raise ToolArgumentError("left and right must be integers")
    return ToolResult.success({"sum": left + right})


def make_add_tool() -> Tool:
    return Tool(
        name="add_numbers",
        description="Add two integers.",
        parameters={
            "type": "object",
            "properties": {
                "left": {"type": "integer"},
                "right": {"type": "integer"},
            },
            "required": ["left", "right"],
            "additionalProperties": False,
        },
        handler=add_numbers,
    )


class ToolRegistryTests(unittest.TestCase):
    def test_schema_is_exposed_in_model_format(self) -> None:
        registry = ToolRegistry((make_add_tool(),))

        schemas = registry.schemas()

        self.assertEqual(schemas[0]["type"], "function")
        self.assertEqual(schemas[0]["function"]["name"], "add_numbers")
        self.assertEqual(
            schemas[0]["function"]["parameters"]["required"],
            ["left", "right"],
        )

    def test_valid_call_returns_json_serializable_result(self) -> None:
        registry = ToolRegistry((make_add_tool(),))

        result = registry.execute(
            ToolCall("call_1", "add_numbers", '{"left":2,"right":3}')
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.data, {"sum": 5})
        self.assertEqual(result.to_json(), '{"ok":true,"data":{"sum":5}}')

    def test_unknown_tool_is_a_recoverable_result(self) -> None:
        result = ToolRegistry().execute(ToolCall("call_1", "missing", "{}"))

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "UNKNOWN_TOOL")

    def test_invalid_json_is_a_recoverable_result(self) -> None:
        registry = ToolRegistry((make_add_tool(),))

        result = registry.execute(ToolCall("call_1", "add_numbers", "not json"))

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "INVALID_ARGUMENTS")

    def test_non_object_arguments_are_rejected(self) -> None:
        registry = ToolRegistry((make_add_tool(),))

        result = registry.execute(ToolCall("call_1", "add_numbers", "[]"))

        self.assertFalse(result.ok)
        self.assertIn("JSON object", result.error.message)

    def test_handler_argument_error_is_returned_to_model(self) -> None:
        registry = ToolRegistry((make_add_tool(),))

        result = registry.execute(
            ToolCall("call_1", "add_numbers", '{"left":"two","right":3}')
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, "INVALID_ARGUMENTS")
        self.assertIn("must be integers", result.error.message)

    def test_unexpected_handler_error_does_not_leak_details(self) -> None:
        def broken_handler(arguments: Mapping[str, Any]) -> ToolResult:
            raise RuntimeError("private implementation detail")

        tool = Tool(
            name="broken",
            description="Fail for a test.",
            parameters={"type": "object"},
            handler=broken_handler,
        )

        result = ToolRegistry((tool,)).execute(ToolCall("call_1", "broken", "{}"))

        self.assertEqual(result.error.code, "INTERNAL_ERROR")
        self.assertNotIn("private implementation detail", result.error.message)

    def test_non_serializable_result_is_replaced_with_error(self) -> None:
        tool = Tool(
            name="bad_result",
            description="Return invalid data for a test.",
            parameters={"type": "object"},
            handler=lambda arguments: ToolResult.success(object()),
        )

        result = ToolRegistry((tool,)).execute(
            ToolCall("call_1", "bad_result", "{}")
        )

        self.assertEqual(result.error.code, "INTERNAL_ERROR")
        self.assertIn("not JSON serializable", result.error.message)

    def test_duplicate_tool_names_are_rejected(self) -> None:
        registry = ToolRegistry((make_add_tool(),))

        with self.assertRaisesRegex(ValueError, "already registered"):
            registry.register(make_add_tool())


if __name__ == "__main__":
    unittest.main()
