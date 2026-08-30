"""Tests that model retries are bounded and never hide fatal errors."""

from __future__ import annotations

import unittest

from coding_agent.domain import Message, ModelTurn
from coding_agent.model import (
    ModelConnectionError,
    ModelHTTPError,
    ModelProtocolError,
    RetryPolicy,
    RetryingModelClient,
)
from tests.fakes import ScriptedModel


SUCCESS = ModelTurn(message=Message(role="assistant", content="ok"))


class ModelRetryTests(unittest.TestCase):
    def retrying(self, scripted: ScriptedModel, sleeps: list[float]):
        return RetryingModelClient(
            scripted,
            RetryPolicy(
                max_attempts=3,
                initial_delay_s=0.5,
                maximum_delay_s=2,
                jitter_ratio=0,
            ),
            sleep=sleeps.append,
            random_value=lambda: 0.5,
        )

    def test_connection_failures_retry_with_exponential_backoff(self) -> None:
        scripted = ScriptedModel(
            [
                ModelConnectionError("offline"),
                ModelConnectionError("still offline"),
                SUCCESS,
            ]
        )
        sleeps: list[float] = []

        result = self.retrying(scripted, sleeps).complete(
            [Message(role="user", content="hello")]
        )

        self.assertEqual(result, SUCCESS)
        self.assertEqual(sleeps, [0.5, 1.0])
        self.assertEqual(len(scripted.requests), 3)

    def test_rate_limit_and_server_error_are_retryable(self) -> None:
        for error in (ModelHTTPError(429, "slow down"), ModelHTTPError(503, "busy")):
            with self.subTest(status=error.status_code):
                scripted = ScriptedModel([error, SUCCESS])
                sleeps: list[float] = []

                result = self.retrying(scripted, sleeps).complete(
                    [Message(role="user", content="hello")]
                )

                self.assertEqual(result, SUCCESS)
                self.assertEqual(sleeps, [0.5])

    def test_authentication_and_bad_request_do_not_retry(self) -> None:
        for status in (400, 401, 403):
            with self.subTest(status=status):
                scripted = ScriptedModel([ModelHTTPError(status, "fatal")])
                sleeps: list[float] = []

                with self.assertRaises(ModelHTTPError):
                    self.retrying(scripted, sleeps).complete(
                        [Message(role="user", content="hello")]
                    )

                self.assertEqual(sleeps, [])
                self.assertEqual(len(scripted.requests), 1)

    def test_protocol_error_does_not_retry(self) -> None:
        scripted = ScriptedModel([ModelProtocolError("bad response")])
        sleeps: list[float] = []

        with self.assertRaises(ModelProtocolError):
            self.retrying(scripted, sleeps).complete(
                [Message(role="user", content="hello")]
            )

        self.assertEqual(sleeps, [])

    def test_retry_exhaustion_raises_last_error(self) -> None:
        scripted = ScriptedModel(
            [
                ModelConnectionError("first"),
                ModelConnectionError("second"),
                ModelConnectionError("last"),
            ]
        )
        sleeps: list[float] = []

        with self.assertRaisesRegex(ModelConnectionError, "last"):
            self.retrying(scripted, sleeps).complete(
                [Message(role="user", content="hello")]
            )

        self.assertEqual(sleeps, [0.5, 1.0])


if __name__ == "__main__":
    unittest.main()
