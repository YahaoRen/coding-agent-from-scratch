"""The small interface between the agent loop and a model provider."""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from coding_agent.domain import Message, ModelTurn


class ModelError(RuntimeError):
    """Base class for failures while requesting or decoding a model response."""


class ModelConnectionError(ModelError):
    """Raised when the model endpoint cannot be reached."""


class ModelHTTPError(ModelError):
    """Raised when the model endpoint returns a non-success status code."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"Model endpoint returned HTTP {status_code}: {message}")
        self.status_code = status_code


class ModelProtocolError(ModelError):
    """Raised when a model response does not follow the expected schema."""


class ModelClient(Protocol):
    """Anything able to produce one assistant turn can drive the agent."""

    def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, Any]] = (),
    ) -> ModelTurn:
        """Return the next assistant turn for the supplied conversation."""


@dataclass(frozen=True)
class RetryPolicy:
    """Small exponential-backoff policy for transient model failures."""

    max_attempts: int = 3
    initial_delay_s: float = 0.5
    maximum_delay_s: float = 4.0
    jitter_ratio: float = 0.2

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 6:
            raise ValueError("max_attempts must be between 1 and 6")
        if self.initial_delay_s < 0 or self.maximum_delay_s < self.initial_delay_s:
            raise ValueError("retry delays are invalid")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between 0 and 1")


class RetryingModelClient:
    """Retry only failures that are safe before any tool has executed."""

    def __init__(
        self,
        inner: ModelClient,
        policy: RetryPolicy | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        self._inner = inner
        self._policy = policy or RetryPolicy()
        self._sleep = sleep
        self._random_value = random_value

    def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, Any]] = (),
    ) -> ModelTurn:
        for attempt in range(1, self._policy.max_attempts + 1):
            try:
                return self._inner.complete(messages, tools)
            except ModelError as error:
                if attempt >= self._policy.max_attempts or not _is_retryable(error):
                    raise
                delay = min(
                    self._policy.initial_delay_s * (2 ** (attempt - 1)),
                    self._policy.maximum_delay_s,
                )
                jitter = 1 + self._policy.jitter_ratio * (
                    (2 * float(self._random_value())) - 1
                )
                self._sleep(max(0.0, delay * jitter))
        raise AssertionError("retry loop ended unexpectedly")


def _is_retryable(error: ModelError) -> bool:
    if isinstance(error, ModelConnectionError):
        return True
    if isinstance(error, ModelHTTPError):
        return error.status_code == 429 or 500 <= error.status_code <= 599
    return False
