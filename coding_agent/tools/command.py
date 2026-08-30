"""Approval-gated local command execution with hard resource bounds."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, BinaryIO

from coding_agent.policy import ToolRisk
from coding_agent.tools.arguments import integer_argument, reject_extra_arguments
from coding_agent.tools.base import Tool, ToolArgumentError, ToolResult
from coding_agent.workspace import Workspace


DEFAULT_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 300
MAX_COMMAND_CHARACTERS = 8192
CAPTURE_HEAD_BYTES = 16 * 1024
CAPTURE_TAIL_BYTES = 16 * 1024
READ_CHUNK_BYTES = 8192

SAFE_ENVIRONMENT_NAMES = {
    "APPDATA",
    "COMSPEC",
    "HOME",
    "LANG",
    "LC_ALL",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "VIRTUAL_ENV",
    "WINDIR",
}

ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


def create_command_tool(
    workspace: Workspace,
    *,
    secrets: Sequence[str] = (),
) -> Tool:
    """Create one command tool bound to a fixed workspace and secret set."""

    secret_values = tuple(value for value in secrets if len(value) >= 4)
    return Tool(
        name="run_command",
        description=(
            "Run one non-interactive command in the workspace. Pass the executable "
            "and each argument separately; shell syntax such as pipes is not interpreted."
        ),
        parameters={
            "type": "object",
            "properties": {
                "argv": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": "Executable followed by its arguments.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_TIMEOUT_SECONDS,
                },
            },
            "required": ["argv"],
            "additionalProperties": False,
        },
        handler=lambda arguments: _run_command(
            workspace,
            arguments,
            secret_values,
        ),
        risk=ToolRisk.EXECUTE,
    )


def _run_command(
    workspace: Workspace,
    arguments: Mapping[str, Any],
    secrets: Sequence[str],
) -> ToolResult:
    reject_extra_arguments(arguments, {"argv", "timeout_seconds"})
    argv = _argv_argument(arguments)
    timeout_seconds = integer_argument(
        arguments,
        "timeout_seconds",
        default=DEFAULT_TIMEOUT_SECONDS,
        minimum=1,
        maximum=MAX_TIMEOUT_SECONDS,
    )

    stdout_capture = BoundedCapture()
    stderr_capture = BoundedCapture()
    started_at = time.monotonic()
    try:
        process = _start_process(argv, workspace)
    except OSError:
        return ToolResult.failure(
            "COMMAND_START_FAILED",
            f"Could not start executable: {argv[0]}",
        )

    stdout_thread = threading.Thread(
        target=_drain_pipe,
        args=(process.stdout, stdout_capture),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_drain_pipe,
        args=(process.stderr, stderr_capture),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    timed_out = False
    try:
        return_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process_tree(process)
        try:
            return_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            return_code = process.wait()
    except KeyboardInterrupt:
        _terminate_process_tree(process)
        raise
    finally:
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)

    duration_ms = round((time.monotonic() - started_at) * 1000)
    stdout, stdout_truncated, stdout_omitted = stdout_capture.render(secrets)
    stderr, stderr_truncated, stderr_omitted = stderr_capture.render(secrets)
    data = {
        "exit_code": return_code,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": timed_out,
        "duration_ms": duration_ms,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "stdout_omitted_bytes": stdout_omitted,
        "stderr_omitted_bytes": stderr_omitted,
    }
    if timed_out:
        return ToolResult.failure(
            "COMMAND_TIMEOUT",
            f"Command exceeded {timeout_seconds} seconds",
            retryable=True,
            data=data,
        )
    if return_code != 0:
        return ToolResult.failure(
            "NONZERO_EXIT",
            f"Command exited with code {return_code}",
            retryable=True,
            data=data,
        )
    return ToolResult.success(data)


def _argv_argument(arguments: Mapping[str, Any]) -> list[str]:
    raw_argv = arguments.get("argv")
    if not isinstance(raw_argv, list) or not raw_argv:
        raise ToolArgumentError("argv must be a non-empty array of strings")
    if any(not isinstance(part, str) or not part for part in raw_argv):
        raise ToolArgumentError("every argv item must be a non-empty string")
    if any("\x00" in part for part in raw_argv):
        raise ToolArgumentError("argv cannot contain NUL bytes")
    if sum(len(part) for part in raw_argv) > MAX_COMMAND_CHARACTERS:
        raise ToolArgumentError(
            f"argv exceeds {MAX_COMMAND_CHARACTERS} characters"
        )
    return list(raw_argv)


def _start_process(
    argv: Sequence[str],
    workspace: Workspace,
) -> subprocess.Popen[bytes]:
    options: dict[str, Any] = {
        "args": list(argv),
        "cwd": workspace.root,
        "env": _safe_child_environment(),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "shell": False,
    }
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    return subprocess.Popen(**options)


def _safe_child_environment() -> dict[str, str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in SAFE_ENVIRONMENT_NAMES
    }
    environment.update(
        {
            "CI": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "NO_COLOR": "1",
            "PAGER": "cat",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return environment


def _drain_pipe(pipe: BinaryIO | None, capture: "BoundedCapture") -> None:
    if pipe is None:
        return
    try:
        while True:
            chunk = pipe.read(READ_CHUNK_BYTES)
            if not chunk:
                return
            capture.feed(chunk)
    finally:
        pipe.close()


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


@dataclass
class BoundedCapture:
    """Keep only the useful beginning and end while continuously draining a pipe."""

    head_limit: int = CAPTURE_HEAD_BYTES
    tail_limit: int = CAPTURE_TAIL_BYTES
    total_bytes: int = 0
    _head: bytearray = field(default_factory=bytearray)
    _tail: bytearray = field(default_factory=bytearray)

    def feed(self, chunk: bytes) -> None:
        self.total_bytes += len(chunk)
        remaining = chunk
        if len(self._head) < self.head_limit:
            head_space = self.head_limit - len(self._head)
            self._head.extend(remaining[:head_space])
            remaining = remaining[head_space:]
        if remaining:
            self._tail.extend(remaining)
            if len(self._tail) > self.tail_limit:
                del self._tail[: len(self._tail) - self.tail_limit]

    def render(self, secrets: Sequence[str] = ()) -> tuple[str, bool, int]:
        kept_bytes = len(self._head) + len(self._tail)
        omitted_bytes = max(0, self.total_bytes - kept_bytes)
        if omitted_bytes:
            combined = (
                bytes(self._head)
                + f"\n[... omitted {omitted_bytes} bytes ...]\n".encode()
                + bytes(self._tail)
            )
        else:
            combined = bytes(self._head) + bytes(self._tail)
        text = combined.decode("utf-8", errors="replace").replace("\x00", "")
        text = ANSI_ESCAPE.sub("", text)
        for secret in secrets:
            text = text.replace(secret, "[REDACTED]")
        return text, bool(omitted_bytes), omitted_bytes
