"""Small test doubles shared by offline agent tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from coding_agent.domain import Message, ModelTurn


class ScriptedModel:
    """Return predefined turns while recording every request."""

    def __init__(self, turns: Sequence[ModelTurn | Exception]) -> None:
        self._turns = list(turns)
        self.requests: list[tuple[tuple[Message, ...], tuple[Mapping[str, Any], ...]]] = []

    def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, Any]] = (),
    ) -> ModelTurn:
        self.requests.append((tuple(messages), tuple(tools)))
        if not self._turns:
            raise AssertionError("ScriptedModel received more requests than expected")
        next_turn = self._turns.pop(0)
        if isinstance(next_turn, Exception):
            raise next_turn
        return next_turn
