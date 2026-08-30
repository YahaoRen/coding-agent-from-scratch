"""Optional, local JSONL persistence for a completed agent run."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from coding_agent.agent import AgentResult


class SessionStore:
    """Write one immutable, secret-redacted transcript per run."""

    def __init__(self, directory: Path, *, secrets: Sequence[str] = ()) -> None:
        self._directory = directory
        self._secrets = tuple(secret for secret in secrets if len(secret) >= 4)

    def save(self, result: AgentResult) -> Path:
        self._directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        session_path = self._directory / f"session-{timestamp}-{uuid4().hex[:8]}.jsonl"

        records: list[dict[str, Any]] = [
            {
                "type": "run",
                "status": result.status.value,
                "model_steps": result.model_steps,
                "tool_calls": result.tool_calls,
                "final_text": result.final_text,
                "error": result.error,
            }
        ]
        records.extend(
            {"type": "message", "message": message.to_api_dict()}
            for message in result.history
        )

        with session_path.open("x", encoding="utf-8", newline="\n") as session_file:
            for record in records:
                redacted = _redact(record, self._secrets)
                session_file.write(
                    json.dumps(redacted, ensure_ascii=False, separators=(",", ":"))
                    + "\n"
                )
        return session_path


def _redact(value: Any, secrets: Sequence[str]) -> Any:
    if isinstance(value, str):
        redacted = value
        for secret in secrets:
            redacted = redacted.replace(secret, "[REDACTED]")
        return redacted
    if isinstance(value, dict):
        return {key: _redact(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    return value
