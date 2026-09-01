"""A bounded and explicit model -> tool -> result agent loop."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from coding_agent.context import ContextOverflowError, ContextWindow
from coding_agent.domain import Message, ToolCall
from coding_agent.model import ModelClient, ModelError
from coding_agent.policy import ApprovalPolicy, DenySideEffectsPolicy
from coding_agent.prompt import SYSTEM_PROMPT
from coding_agent.redaction import normalized_secrets, redact_text, redact_value
from coding_agent.tools import ToolError, ToolRegistry, ToolResult


MAX_TOOL_RESULTS_PER_TURN_CHARACTERS = 60_000
MAX_SINGLE_TOOL_RESULT_CHARACTERS = 32_000


class AgentStatus(str, Enum):
    """Why an agent run stopped."""

    COMPLETED = "completed"
    MAX_STEPS = "max_steps"
    MAX_TOOL_CALLS = "max_tool_calls"
    MODEL_ERROR = "model_error"
    PROTOCOL_ERROR = "protocol_error"
    CONTEXT_ERROR = "context_error"
    STALLED = "stalled"
    CANCELLED = "cancelled"


class AgentEventKind(str, Enum):
    """Small progress events a CLI may render without owning the loop."""

    MODEL_REQUEST = "model_request"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"


@dataclass(frozen=True)
class AgentEvent:
    kind: AgentEventKind
    step: int
    call: ToolCall | None = None
    result: ToolResult | None = None


AgentObserver = Callable[[AgentEvent], None]


@dataclass(frozen=True)
class AgentLimits:
    """Hard bounds that prevent an accidental infinite loop."""

    max_steps: int = 20
    max_tool_calls: int = 50
    max_consecutive_identical_calls: int = 2

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        if self.max_tool_calls < 1:
            raise ValueError("max_tool_calls must be at least 1")
        if self.max_consecutive_identical_calls < 1:
            raise ValueError("max_consecutive_identical_calls must be at least 1")


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
        observer: AgentObserver | None = None,
        context_window: ContextWindow | None = None,
        secrets: tuple[str, ...] = (),
        stop_requested: Callable[[], bool] | None = None,
    ) -> None:
        if not system_prompt.strip():
            raise ValueError("system_prompt cannot be empty")
        self._model = model
        self._tools = tools
        self._limits = limits or AgentLimits()
        self._system_prompt = system_prompt
        self._approval_policy = approval_policy or DenySideEffectsPolicy()
        self._observer = observer
        self._context_window = context_window or ContextWindow()
        self._secrets = normalized_secrets(secrets)
        self._stop_requested = stop_requested

    def run(self, task: str) -> AgentResult:
        """Run until the model answers, an error occurs, or a limit is reached."""

        if not task.strip():
            raise ValueError("task cannot be empty")

        history = [
            Message(role="system", content=self._system_prompt),
            Message(role="user", content=redact_text(task, self._secrets)),
        ]
        tool_call_count = 0
        last_call_signature: tuple[str, str] | None = None
        consecutive_identical_calls = 0

        for step in range(1, self._limits.max_steps + 1):
            if self._should_stop():
                return _cancelled_result(history, step - 1, tool_call_count)
            schemas = self._tools.schemas()
            try:
                request_messages = self._context_window.build(history, schemas)
            except ContextOverflowError as error:
                return AgentResult(
                    status=AgentStatus.CONTEXT_ERROR,
                    final_text="",
                    history=tuple(history),
                    model_steps=step,
                    tool_calls=tool_call_count,
                    error=str(error),
                )
            self._emit(AgentEvent(kind=AgentEventKind.MODEL_REQUEST, step=step))
            try:
                turn = self._model.complete(request_messages, schemas)
            except ModelError as error:
                if self._should_stop():
                    return _cancelled_result(history, step, tool_call_count)
                return AgentResult(
                    status=AgentStatus.MODEL_ERROR,
                    final_text="",
                    history=tuple(history),
                    model_steps=step,
                    tool_calls=tool_call_count,
                    error=redact_text(str(error), self._secrets),
                )

            if self._should_stop():
                return _cancelled_result(history, step, tool_call_count)

            assistant = _redact_message(turn.message, self._secrets)
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

            finish_error = _finish_reason_error(turn.finish_reason, assistant)
            if finish_error is not None:
                return AgentResult(
                    status=AgentStatus.PROTOCOL_ERROR,
                    final_text="",
                    history=tuple(history),
                    model_steps=step,
                    tool_calls=tool_call_count,
                    error=finish_error,
                )

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

            stalled = False
            next_signature = last_call_signature
            next_consecutive_count = consecutive_identical_calls
            for call in assistant.tool_calls:
                signature = _tool_call_signature(call)
                if signature == next_signature:
                    next_consecutive_count += 1
                else:
                    next_signature = signature
                    next_consecutive_count = 1
                if (
                    next_consecutive_count
                    > self._limits.max_consecutive_identical_calls
                ):
                    stalled = True
                    break
            if stalled:
                stalled_result = ToolResult.failure(
                    "REPEATED_TOOL_CALL",
                    "The same tool request was repeated too many times in a row",
                )
                for call in assistant.tool_calls:
                    self._emit(
                        AgentEvent(
                            kind=AgentEventKind.TOOL_CALL,
                            step=step,
                            call=call,
                        )
                    )
                    self._emit(
                        AgentEvent(
                            kind=AgentEventKind.TOOL_RESULT,
                            step=step,
                            call=call,
                            result=stalled_result,
                        )
                    )
                    history.append(
                        Message(
                            role="tool",
                            content=stalled_result.to_json(),
                            tool_call_id=call.id,
                        )
                    )
                return AgentResult(
                    status=AgentStatus.STALLED,
                    final_text="",
                    history=tuple(history),
                    model_steps=step,
                    tool_calls=tool_call_count,
                    error="Agent stopped after repeated identical tool requests",
                )

            last_call_signature = next_signature
            consecutive_identical_calls = next_consecutive_count
            result_budget = min(
                MAX_SINGLE_TOOL_RESULT_CHARACTERS,
                MAX_TOOL_RESULTS_PER_TURN_CHARACTERS // len(assistant.tool_calls),
            )

            for call in assistant.tool_calls:
                if self._should_stop():
                    return _cancelled_result(history, step, tool_call_count)
                self._emit(
                    AgentEvent(
                        kind=AgentEventKind.TOOL_CALL,
                        step=step,
                        call=call,
                    )
                )
                result = _redact_tool_result(
                    self._tools.execute(
                        call,
                        self._approval_policy,
                        self._should_stop,
                    ),
                    self._secrets,
                )
                self._emit(
                    AgentEvent(
                        kind=AgentEventKind.TOOL_RESULT,
                        step=step,
                        call=call,
                        result=result,
                    )
                )
                history.append(
                    Message(
                        role="tool",
                        content=_bounded_tool_result_json(result, result_budget),
                        tool_call_id=call.id,
                    )
                )
                tool_call_count += 1
                if self._should_stop():
                    return _cancelled_result(history, step, tool_call_count)

        return AgentResult(
            status=AgentStatus.MAX_STEPS,
            final_text="",
            history=tuple(history),
            model_steps=self._limits.max_steps,
            tool_calls=tool_call_count,
            error="Maximum model-step count reached",
        )

    def _emit(self, event: AgentEvent) -> None:
        if self._observer is None:
            return
        try:
            self._observer(event)
        except Exception:
            # Rendering progress must never change the agent's behavior.
            pass

    def _should_stop(self) -> bool:
        if self._stop_requested is None:
            return False
        try:
            return bool(self._stop_requested())
        except Exception:
            # A broken UI callback must not crash the agent loop.
            return False


def _cancelled_result(
    history: list[Message],
    model_steps: int,
    tool_calls: int,
) -> AgentResult:
    return AgentResult(
        status=AgentStatus.CANCELLED,
        final_text="",
        history=tuple(history),
        model_steps=model_steps,
        tool_calls=tool_calls,
        error="Run cancelled by user",
    )


def _finish_reason_error(reason: str | None, message: Message) -> str | None:
    if reason == "length":
        return "Model response was truncated because its output limit was reached"
    if reason == "content_filter":
        return "Model response was stopped by the provider's content filter"
    if reason == "tool_calls" and not message.tool_calls:
        return "Model reported tool calls but did not provide any"
    return None


def _tool_call_signature(call: ToolCall) -> tuple[str, str]:
    try:
        arguments = json.loads(call.arguments)
        normalized_arguments = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        normalized_arguments = call.arguments
    return call.name, normalized_arguments


def _redact_message(message: Message, secrets: tuple[str, ...]) -> Message:
    content = (
        redact_text(message.content, secrets)
        if message.content is not None
        else None
    )
    calls = tuple(
        ToolCall(
            id=redact_text(call.id, secrets),
            name=redact_text(call.name, secrets),
            arguments=redact_text(call.arguments, secrets),
        )
        for call in message.tool_calls
    )
    return Message(
        role=message.role,
        content=content,
        tool_calls=calls,
        tool_call_id=(
            redact_text(message.tool_call_id, secrets)
            if message.tool_call_id is not None
            else None
        ),
    )


def _redact_tool_result(
    result: ToolResult,
    secrets: tuple[str, ...],
) -> ToolResult:
    error = result.error
    redacted_error = (
        ToolError(
            code=redact_text(error.code, secrets),
            message=redact_text(error.message, secrets),
            retryable=error.retryable,
        )
        if error is not None
        else None
    )
    return ToolResult(
        ok=result.ok,
        data=redact_value(result.data, secrets),
        error=redacted_error,
        meta=redact_value(dict(result.meta), secrets),
    )


def _bounded_tool_result_json(result: ToolResult, max_characters: int) -> str:
    rendered = result.to_json()
    if len(rendered) <= max_characters:
        return rendered

    preview_characters = max(100, (max_characters - 500) // 2)
    while True:
        data = {
            "result_truncated": True,
            "original_characters": len(rendered),
            "head": rendered[:preview_characters],
            "tail": rendered[-preview_characters:],
        }
        bounded = ToolResult(
            ok=result.ok,
            data=data,
            error=result.error,
        ).to_json()
        if len(bounded) <= max_characters or preview_characters <= 100:
            return bounded
        preview_characters = max(100, preview_characters // 2)
