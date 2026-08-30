"""Provider-neutral data structures shared by the model and agent layers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


Role = Literal["system", "developer", "user", "assistant", "tool"]


@dataclass(frozen=True)
class ToolCall:
    """One function call requested by the model."""

    id: str
    name: str
    arguments: str

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


@dataclass(frozen=True)
class Message:
    """A chat message independent from any vendor SDK classes."""

    role: Role
    content: str | None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None

    def to_api_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            data["tool_calls"] = [call.to_api_dict() for call in self.tool_calls]
        if self.tool_call_id is not None:
            data["tool_call_id"] = self.tool_call_id
        return data


@dataclass(frozen=True)
class ModelTurn:
    """The assistant message and stop reason returned by one model request."""

    message: Message
    finish_reason: str | None = None
