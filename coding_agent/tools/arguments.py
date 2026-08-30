"""Small manual validators shared by local tool handlers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from coding_agent.tools.base import ToolArgumentError


def reject_extra_arguments(arguments: Mapping[str, Any], allowed: set[str]) -> None:
    extras = sorted(set(arguments) - allowed)
    if extras:
        raise ToolArgumentError(f"Unexpected arguments: {', '.join(extras)}")


def text_argument(
    arguments: Mapping[str, Any],
    name: str,
    *,
    default: str | None = None,
    allow_empty: bool = False,
) -> str:
    value = arguments.get(name, default)
    if not isinstance(value, str):
        raise ToolArgumentError(f"{name} must be text")
    if not allow_empty and not value.strip():
        raise ToolArgumentError(f"{name} cannot be empty")
    return value


def integer_argument(
    arguments: Mapping[str, Any],
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int | None = None,
) -> int:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolArgumentError(f"{name} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        range_text = f"at least {minimum}"
        if maximum is not None:
            range_text = f"between {minimum} and {maximum}"
        raise ToolArgumentError(f"{name} must be {range_text}")
    return value


def boolean_argument(
    arguments: Mapping[str, Any],
    name: str,
    *,
    default: bool,
) -> bool:
    value = arguments.get(name, default)
    if not isinstance(value, bool):
        raise ToolArgumentError(f"{name} must be a boolean")
    return value
