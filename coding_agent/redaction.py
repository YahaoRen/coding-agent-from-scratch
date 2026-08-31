"""Small helpers for removing known secrets before data leaves the process."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def normalized_secrets(secrets: Sequence[str]) -> tuple[str, ...]:
    """Keep useful secret values once, longest first."""

    return tuple(
        sorted(
            {secret for secret in secrets if isinstance(secret, str) and len(secret) >= 4},
            key=len,
            reverse=True,
        )
    )


def redact_text(text: str, secrets: Sequence[str]) -> str:
    """Replace every known secret in text with a stable marker."""

    redacted = text
    for secret in secrets:
        redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def redact_value(value: Any, secrets: Sequence[str]) -> Any:
    """Recursively redact JSON-like values without mutating the input."""

    if isinstance(value, str):
        return redact_text(value, secrets)
    if isinstance(value, Mapping):
        return {
            redact_text(str(key), secrets): redact_value(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_value(item, secrets) for item in value]
    return value
