"""Offline tests for the provider-neutral model boundary."""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from typing import Any

from coding_agent.config import Settings
from coding_agent.domain import Message, ToolCall
from coding_agent.model import ModelProtocolError
from coding_agent.providers.openai_compatible import OpenAICompatibleClient


class RecordingTransport:
    def __init__(self, response: Mapping[str, Any]) -> None:
        self.response = response
        self.request: dict[str, Any] | None = None

    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_s: float,
    ) -> Mapping[str, Any]:
        self.request = {
            "url": url,
            "headers": dict(headers),
            "payload": dict(payload),
            "timeout_s": timeout_s,
        }
        return self.response


class OpenAICompatibleClientTests(unittest.TestCase):
    def make_client(
        self, response: Mapping[str, Any]
    ) -> tuple[OpenAICompatibleClient, RecordingTransport]:
        settings = Settings(
            api_key="test-secret",
            base_url="https://example.test/v1",
            model="test-model",
            request_timeout_s=12,
        )
        transport = RecordingTransport(response)
        return OpenAICompatibleClient(settings, transport), transport

    def test_sends_messages_and_parses_text_response(self) -> None:
        client, transport = self.make_client(
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "Hello"},
                        "finish_reason": "stop",
                    }
                ]
            }
        )

        turn = client.complete([Message(role="user", content="Hi")])

        self.assertEqual(turn.message.content, "Hello")
        self.assertEqual(turn.finish_reason, "stop")
        self.assertEqual(transport.request["url"], "https://example.test/v1/chat/completions")
        self.assertEqual(
            transport.request["payload"]["messages"],
            [{"role": "user", "content": "Hi"}],
        )
        self.assertNotIn("tools", transport.request["payload"])
        self.assertNotIn("thinking", transport.request["payload"])

    def test_sends_tools_and_parses_function_call(self) -> None:
        client, transport = self.make_client(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"path":"README.md"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        )
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read one file",
                    "parameters": {"type": "object"},
                },
            }
        ]

        turn = client.complete([Message(role="user", content="Read it")], tools)

        self.assertEqual(
            turn.message.tool_calls,
            (ToolCall("call_1", "read_file", '{"path":"README.md"}'),),
        )
        self.assertEqual(transport.request["payload"]["tools"], tools)
        self.assertEqual(transport.request["payload"]["tool_choice"], "auto")

    def test_serializes_assistant_calls_and_tool_results(self) -> None:
        call = ToolCall("call_7", "read_file", '{"path":"a.py"}')
        messages = [
            Message(role="assistant", content=None, tool_calls=(call,)),
            Message(role="tool", content='{"ok":true}', tool_call_id="call_7"),
        ]

        self.assertEqual(
            [message.to_api_dict() for message in messages],
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [call.to_api_dict()],
                },
                {
                    "role": "tool",
                    "content": '{"ok":true}',
                    "tool_call_id": "call_7",
                },
            ],
        )

    def test_rejects_invalid_model_response(self) -> None:
        client, _ = self.make_client({"choices": []})

        with self.assertRaisesRegex(ModelProtocolError, "no choices"):
            client.complete([Message(role="user", content="Hi")])

    def test_disables_default_thinking_for_official_deepseek_v4(self) -> None:
        settings = Settings(
            api_key="test-secret",
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
        )
        transport = RecordingTransport(
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "ready"},
                        "finish_reason": "stop",
                    }
                ]
            }
        )
        client = OpenAICompatibleClient(settings, transport)
        tools = [{"type": "function", "function": {"name": "read_file"}}]

        client.complete([Message(role="user", content="Hi")], tools)

        self.assertEqual(
            transport.request["payload"]["thinking"],
            {"type": "disabled"},
        )
        self.assertEqual(transport.request["payload"]["tools"], tools)

    def test_deepseek_option_requires_both_official_host_and_v4_model(self) -> None:
        cases = (
            ("https://api.deepseek.com", "other-model"),
            ("https://api.deepseek.com.example.test", "deepseek-v4-flash"),
        )
        response = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "ready"},
                    "finish_reason": "stop",
                }
            ]
        }

        for base_url, model in cases:
            with self.subTest(base_url=base_url, model=model):
                settings = Settings(
                    api_key="test-secret",
                    base_url=base_url,
                    model=model,
                )
                transport = RecordingTransport(response)
                client = OpenAICompatibleClient(settings, transport)

                client.complete([Message(role="user", content="Hi")])

                self.assertNotIn("thinking", transport.request["payload"])


if __name__ == "__main__":
    unittest.main()
