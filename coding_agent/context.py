"""Deterministic context budgeting that never splits tool interactions."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from coding_agent.domain import Message


class ContextOverflowError(ValueError):
    """Raised when pinned or newest context cannot fit safely."""


@dataclass(frozen=True)
class ContextLimits:
    """A conservative character budget when no provider tokenizer is available."""

    max_characters: int = 120_000
    reserved_response_characters: int = 20_000

    def __post_init__(self) -> None:
        if self.max_characters < 4_000:
            raise ValueError("max_characters must be at least 4000")
        if not 1_000 <= self.reserved_response_characters < self.max_characters:
            raise ValueError(
                "reserved_response_characters must be at least 1000 and below max_characters"
            )

    @property
    def input_characters(self) -> int:
        return self.max_characters - self.reserved_response_characters


class ContextWindow:
    """Build one bounded request view while retaining full history in memory."""

    def __init__(self, limits: ContextLimits | None = None) -> None:
        self._limits = limits or ContextLimits()

    def build(
        self,
        history: Sequence[Message],
        tools: Sequence[Mapping[str, Any]] = (),
    ) -> list[Message]:
        if len(history) < 2 or history[0].role != "system" or history[1].role != "user":
            raise ContextOverflowError("History must start with system and user messages")

        tool_characters = len(json.dumps(list(tools), ensure_ascii=False))
        budget = self._limits.input_characters - tool_characters
        if budget <= 0:
            raise ContextOverflowError("Tool schemas exceed the available context budget")

        full_size = sum(_message_size(message) for message in history)
        if full_size <= budget:
            return list(history)

        pinned = list(history[:2])
        pinned_size = sum(_message_size(message) for message in pinned)
        groups = _conversation_groups(history[2:])
        omission_marker = Message(
            role="system",
            content=f"[Earlier context omitted: up to {len(groups)} complete interaction groups.]",
        )
        used = pinned_size + _message_size(omission_marker)
        if used > budget:
            raise ContextOverflowError("System prompt and original task exceed the context budget")

        if groups:
            newest_size = sum(_message_size(message) for message in groups[-1])
            if used + newest_size > budget:
                raise ContextOverflowError(
                    "Newest complete interaction group exceeds the context budget"
                )

        selected_reversed: list[tuple[Message, ...]] = []
        omitted_groups = 0
        for group in reversed(groups):
            group_size = sum(_message_size(message) for message in group)
            if used + group_size <= budget:
                selected_reversed.append(group)
                used += group_size
            else:
                omitted_groups = len(groups) - len(selected_reversed)
                break

        selected = list(reversed(selected_reversed))
        marker = Message(
            role="system",
            content=f"[Earlier context omitted: {omitted_groups} complete interaction group(s).]",
        )
        request = [pinned[0], marker, pinned[1]]
        for group in selected:
            request.extend(group)
        return request


def _message_size(message: Message) -> int:
    return len(json.dumps(message.to_api_dict(), ensure_ascii=False))


def _conversation_groups(messages: Sequence[Message]) -> list[tuple[Message, ...]]:
    """Keep an assistant tool request and its contiguous results inseparable."""

    groups: list[tuple[Message, ...]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        group = [message]
        index += 1
        if message.role == "assistant" and message.tool_calls:
            call_ids = {call.id for call in message.tool_calls}
            while (
                index < len(messages)
                and messages[index].role == "tool"
                and messages[index].tool_call_id in call_ids
            ):
                group.append(messages[index])
                index += 1
        groups.append(tuple(group))
    return groups
