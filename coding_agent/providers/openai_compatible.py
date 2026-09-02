"""OpenAI-compatible Chat Completions adapter using only the standard library."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, cast

from coding_agent.config import Settings
from coding_agent.domain import Message, ModelTurn, ToolCall
from coding_agent.model import (
    ModelConnectionError,
    ModelHTTPError,
    ModelProtocolError,
)


class JsonTransport(Protocol):
    """Tiny injectable HTTP boundary used to keep model tests offline."""

    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_s: float,
    ) -> Mapping[str, Any]:
        """POST JSON and return a decoded JSON object."""


class UrllibJsonTransport:
    """Production JSON transport implemented with Python's standard library."""

    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_s: float,
    ) -> Mapping[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers=dict(headers),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                response_body = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read(4096).decode("utf-8", errors="replace").strip()
            raise ModelHTTPError(error.code, detail or error.reason) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ModelConnectionError(f"Could not reach model endpoint: {error}") from None

        try:
            decoded = json.loads(response_body)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ModelProtocolError("Model endpoint returned invalid JSON") from error
        if not isinstance(decoded, dict):
            raise ModelProtocolError("Model endpoint returned a non-object JSON value")
        return cast(Mapping[str, Any], decoded)


class OpenAICompatibleClient:
    """Translate provider-neutral messages to Chat Completions requests."""

    def __init__(
        self,
        settings: Settings,
        transport: JsonTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport or UrllibJsonTransport()
        self._disable_thinking = _is_official_deepseek_v4(settings)

    def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, Any]] = (),
    ) -> ModelTurn:
        payload: dict[str, Any] = {
            "model": self._settings.model,
            "messages": [message.to_api_dict() for message in messages],
        }
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"
        if self._disable_thinking:
            # DeepSeek V4 enables thinking by default. Non-thinking mode keeps the
            # standard tool-call message shape used by this compact adapter.
            payload["thinking"] = {"type": "disabled"}

        response = self._transport.post_json(
            url=f"{self._settings.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._settings.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "coding-agent-from-scratch/0.1",
            },
            payload=payload,
            timeout_s=self._settings.request_timeout_s,
        )
        return self._parse_turn(response)

    @staticmethod
    def _parse_turn(response: Mapping[str, Any]) -> ModelTurn:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelProtocolError("Model response has no choices")

        choice = choices[0]
        if not isinstance(choice, dict):
            raise ModelProtocolError("Model response choice is not an object")
        message_data = choice.get("message")
        if not isinstance(message_data, dict):
            raise ModelProtocolError("Model response choice has no message")
        if message_data.get("role") != "assistant":
            raise ModelProtocolError("Model response message is not from assistant")

        content = message_data.get("content")
        if content is not None and not isinstance(content, str):
            raise ModelProtocolError("Assistant content must be text or null")

        calls: list[ToolCall] = []
        raw_calls = message_data.get("tool_calls", [])
        if raw_calls is None:
            raw_calls = []
        if not isinstance(raw_calls, list):
            raise ModelProtocolError("Assistant tool_calls must be a list")
        for raw_call in raw_calls:
            calls.append(OpenAICompatibleClient._parse_tool_call(raw_call))

        finish_reason = choice.get("finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            raise ModelProtocolError("finish_reason must be text or null")

        return ModelTurn(
            message=Message(
                role="assistant",
                content=content,
                tool_calls=tuple(calls),
            ),
            finish_reason=finish_reason,
        )

    @staticmethod
    def _parse_tool_call(raw_call: Any) -> ToolCall:
        if not isinstance(raw_call, dict) or raw_call.get("type") != "function":
            raise ModelProtocolError("Tool call must be a function object")
        call_id = raw_call.get("id")
        function = raw_call.get("function")
        if not isinstance(call_id, str) or not call_id:
            raise ModelProtocolError("Tool call has no id")
        if not isinstance(function, dict):
            raise ModelProtocolError("Tool call has no function")

        name = function.get("name")
        arguments = function.get("arguments")
        if not isinstance(name, str) or not name:
            raise ModelProtocolError("Tool call has no function name")
        if not isinstance(arguments, str):
            raise ModelProtocolError("Tool call arguments must be JSON text")
        return ToolCall(id=call_id, name=name, arguments=arguments)


def _is_official_deepseek_v4(settings: Settings) -> bool:
    """Use DeepSeek V4's simpler non-thinking tool-call protocol by default."""

    try:
        hostname = urllib.parse.urlparse(settings.base_url).hostname
    except ValueError:
        return False
    return hostname == "api.deepseek.com" and settings.model.startswith("deepseek-v4-")
