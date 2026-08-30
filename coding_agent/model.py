"""The small interface between the agent loop and a model provider."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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
