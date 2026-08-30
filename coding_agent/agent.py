"""A bounded and explicit model -> tool -> result agent loop."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from coding_agent.domain import Message
from coding_agent.model import ModelClient, ModelError
from coding_agent.policy import ApprovalPolicy, DenySideEffectsPolicy
from coding_agent.prompt import SYSTEM_PROMPT
from coding_agent.tools import ToolRegistry, ToolResult


class AgentStatus(str, Enum):
    """Why an agent run stopped."""

    COMPLETED = "completed"
    MAX_STEPS = "max_steps"
    MAX_TOOL_CALLS = "max_tool_calls"
    MODEL_ERROR = "model_error"
    PROTOCOL_ERROR = "protocol_error"


@dataclass(frozen=True)
class AgentLimits:
    """Hard bounds that prevent an accidental infinite loop."""

    max_steps: int = 20
    max_tool_calls: int = 50

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        if self.max_tool_calls < 1:
            raise ValueError("max_tool_calls must be at least 1")


@dataclass(frozen=True)
class AgentResult:
    """The final status and full in-memory conversation for one run."""

    status: AgentStatus
    final_text: str
    history: tuple[Message, ...]
    model_steps: int
    tool_calls: int
    error: str | None = None


class Agent:
    """Coordinate a model and local tools without a framework runtime."""

    def __init__(
        self,
        model: ModelClient,
        tools: ToolRegistry,
        *,
        limits: AgentLimits | None = None,
        system_prompt: str = SYSTEM_PROMPT,
        approval_policy: ApprovalPolicy | None = None,
    ) -> None:
        if not system_prompt.strip():
            raise ValueError("system_prompt cannot be empty")
        self._model = model
        self._tools = tools
        self._limits = limits or AgentLimits()
        self._system_prompt = system_prompt
        self._approval_policy = approval_policy or DenySideEffectsPolicy()

    def run(self, task: str) -> AgentResult:
        """Run until the model answers, an error occurs, or a limit is reached."""

        if not task.strip():
            raise ValueError("task cannot be empty")

        history = [
            Message(role="system", content=self._system_prompt),
            Message(role="user", content=task),
        ]
        tool_call_count = 0

        for step in range(1, self._limits.max_steps + 1):
            try:
                turn = self._model.complete(history, self._tools.schemas())
            except ModelError as error:
                return AgentResult(
                    status=AgentStatus.MODEL_ERROR,
                    final_text="",
                    history=tuple(history),
                    model_steps=step,
                    tool_calls=tool_call_count,
                    error=str(error),
                )

            assistant = turn.message
            if assistant.role != "assistant":
                return AgentResult(
                    status=AgentStatus.PROTOCOL_ERROR,
                    final_text="",
                    history=tuple(history),
                    model_steps=step,
                    tool_calls=tool_call_count,
                    error="Model turn did not contain an assistant message",
                )
            history.append(assistant)

            if not assistant.tool_calls:
                final_text = (assistant.content or "").strip()
                if final_text:
                    return AgentResult(
                        status=AgentStatus.COMPLETED,
                        final_text=final_text,
                        history=tuple(history),
                        model_steps=step,
                        tool_calls=tool_call_count,
                    )
                return AgentResult(
                    status=AgentStatus.PROTOCOL_ERROR,
                    final_text="",
                    history=tuple(history),
                    model_steps=step,
                    tool_calls=tool_call_count,
                    error="Model returned neither text nor tool calls",
                )

            if tool_call_count + len(assistant.tool_calls) > self._limits.max_tool_calls:
                limit_result = ToolResult.failure(
                    "TOOL_CALL_LIMIT",
                    "The run reached its maximum number of tool calls",
                ).to_json()
                for call in assistant.tool_calls:
                    history.append(
                        Message(
                            role="tool",
                            content=limit_result,
                            tool_call_id=call.id,
                        )
                    )
                return AgentResult(
                    status=AgentStatus.MAX_TOOL_CALLS,
                    final_text="",
                    history=tuple(history),
                    model_steps=step,
                    tool_calls=tool_call_count,
                    error="Maximum tool-call count reached",
                )

            for call in assistant.tool_calls:
                result = self._tools.execute(call, self._approval_policy)
                history.append(
                    Message(
                        role="tool",
                        content=result.to_json(),
                        tool_call_id=call.id,
                    )
                )
                tool_call_count += 1

        return AgentResult(
            status=AgentStatus.MAX_STEPS,
            final_text="",
            history=tuple(history),
            model_steps=self._limits.max_steps,
            tool_calls=tool_call_count,
            error="Maximum model-step count reached",
        )
