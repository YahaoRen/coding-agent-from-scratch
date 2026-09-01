"""Tool definitions, dispatch, and a uniform error format."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from coding_agent.domain import ToolCall
from coding_agent.policy import ApprovalPolicy, DenySideEffectsPolicy, ToolRisk


TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")


class ToolArgumentError(ValueError):
    """A safe, user-facing explanation of invalid tool arguments."""


@dataclass(frozen=True)
class ToolError:
    """A structured failure the model can inspect and recover from."""

    code: str
    message: str
    retryable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


@dataclass(frozen=True)
class ToolResult:
    """The JSON-serializable result of one local tool execution."""

    ok: bool
    data: Any = None
    error: ToolError | None = None
    meta: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def success(
        cls,
        data: Any = None,
        *,
        meta: Mapping[str, Any] | None = None,
    ) -> "ToolResult":
        return cls(ok=True, data=data, meta=meta or {})

    @classmethod
    def failure(
        cls,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        data: Any = None,
        meta: Mapping[str, Any] | None = None,
    ) -> "ToolResult":
        return cls(
            ok=False,
            data=data,
            error=ToolError(code=code, message=message, retryable=retryable),
            meta=meta or {},
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": self.ok}
        if self.data is not None:
            result["data"] = self.data
        if self.error is not None:
            result["error"] = self.error.to_dict()
        if self.meta:
            result["meta"] = dict(self.meta)
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


ToolHandler = Callable[[Mapping[str, Any]], ToolResult]


@dataclass(frozen=True)
class Tool:
    """A local handler plus the JSON schema shown to the model."""

    name: str
    description: str
    parameters: Mapping[str, Any]
    handler: ToolHandler
    risk: ToolRisk = ToolRisk.READ

    def __post_init__(self) -> None:
        if not TOOL_NAME_PATTERN.fullmatch(self.name):
            raise ValueError(f"Invalid tool name: {self.name!r}")
        if not self.description.strip():
            raise ValueError("Tool description cannot be empty")
        if self.parameters.get("type") != "object":
            raise ValueError("Tool parameters must use an object JSON schema")

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dict(self.parameters),
            },
        }


class ToolRegistry:
    """Own the explicit mapping from model-visible names to local functions."""

    def __init__(self, tools: tuple[Tool, ...] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def execute(
        self,
        call: ToolCall,
        approval_policy: ApprovalPolicy | None = None,
        stop_requested: Callable[[], bool] | None = None,
    ) -> ToolResult:
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult.failure(
                "UNKNOWN_TOOL",
                f"Unknown tool: {call.name}",
            )

        try:
            arguments = json.loads(call.arguments)
        except json.JSONDecodeError:
            return ToolResult.failure(
                "INVALID_ARGUMENTS",
                "Tool arguments must be valid JSON",
            )
        if not isinstance(arguments, dict):
            return ToolResult.failure(
                "INVALID_ARGUMENTS",
                "Tool arguments must be a JSON object",
            )

        policy = approval_policy or DenySideEffectsPolicy()
        try:
            approved = policy.approve(tool.name, tool.risk, arguments)
        except Exception:
            return ToolResult.failure(
                "APPROVAL_ERROR",
                "Approval policy failed unexpectedly",
            )
        if not approved:
            return ToolResult.failure(
                "PERMISSION_DENIED",
                f"Permission denied for {tool.risk.value} tool: {tool.name}",
            )
        if stop_requested is not None and stop_requested():
            return ToolResult.failure(
                "RUN_CANCELLED",
                "The run was cancelled before the tool started",
            )

        try:
            result = tool.handler(arguments)
        except ToolArgumentError as error:
            return ToolResult.failure("INVALID_ARGUMENTS", str(error))
        except Exception:
            return ToolResult.failure(
                "INTERNAL_ERROR",
                "Tool failed unexpectedly",
            )

        if not isinstance(result, ToolResult):
            return ToolResult.failure(
                "INTERNAL_ERROR",
                "Tool returned an invalid result",
            )
        try:
            result.to_json()
        except (TypeError, ValueError):
            return ToolResult.failure(
                "INTERNAL_ERROR",
                "Tool returned data that is not JSON serializable",
            )
        return result
