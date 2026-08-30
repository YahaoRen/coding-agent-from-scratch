"""Load model settings without exposing secrets in code or logs."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ConfigurationError(ValueError):
    """Raised when required configuration is missing or invalid."""


def read_env_file(path: Path) -> dict[str, str]:
    """Read a small, predictable subset of the dotenv file format."""

    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()

        key, separator, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not separator or not ENV_KEY_PATTERN.fullmatch(key):
            raise ConfigurationError(f"Invalid .env entry at {path}:{line_number}")

        if value[:1] in {"'", '"'}:
            quote = value[0]
            if len(value) < 2 or value[-1] != quote:
                raise ConfigurationError(
                    f"Unclosed quoted value at {path}:{line_number}"
                )
            value = value[1:-1]
        values[key] = value
    return values


@dataclass(frozen=True)
class Settings:
    """Runtime settings for one OpenAI-compatible model endpoint."""

    api_key: str = field(repr=False)
    base_url: str
    model: str
    request_timeout_s: float = 60.0

    @classmethod
    def load(
        cls,
        env: Mapping[str, str] | None = None,
        env_file: Path | None = Path(".env"),
    ) -> "Settings":
        """Load settings, giving real environment variables highest priority."""

        values = read_env_file(env_file) if env_file is not None else {}
        values.update(os.environ if env is None else env)

        api_key = values.get("CODING_AGENT_API_KEY", "").strip()
        model = values.get("CODING_AGENT_MODEL", "").strip()
        base_url = values.get(
            "CODING_AGENT_BASE_URL", "https://api.openai.com/v1"
        ).strip()

        if not api_key:
            raise ConfigurationError("CODING_AGENT_API_KEY is required")
        if not model:
            raise ConfigurationError("CODING_AGENT_MODEL is required")
        if not base_url.startswith(("http://", "https://")):
            raise ConfigurationError("CODING_AGENT_BASE_URL must be an HTTP(S) URL")

        timeout_text = values.get("CODING_AGENT_REQUEST_TIMEOUT", "60").strip()
        try:
            timeout = float(timeout_text)
        except ValueError as error:
            raise ConfigurationError(
                "CODING_AGENT_REQUEST_TIMEOUT must be a number"
            ) from error
        if not 0 < timeout <= 300:
            raise ConfigurationError(
                "CODING_AGENT_REQUEST_TIMEOUT must be between 0 and 300 seconds"
            )

        return cls(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            model=model,
            request_timeout_s=timeout,
        )
