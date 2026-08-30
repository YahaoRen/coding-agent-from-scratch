"""Atomic file creation and exact text replacement tools."""

from __future__ import annotations

import difflib
import os
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from coding_agent.policy import ToolRisk
from coding_agent.tools.arguments import (
    boolean_argument,
    reject_extra_arguments,
    text_argument,
)
from coding_agent.tools.base import Tool, ToolResult
from coding_agent.workspace import Workspace, WorkspaceError


MAX_WRITE_BYTES = 512 * 1024
MAX_DIFF_CHARACTERS = 24 * 1024


def create_write_tools(workspace: Workspace) -> tuple[Tool, ...]:
    return (
        Tool(
            name="write_file",
            description=(
                "Create a UTF-8 text file. Set overwrite=true only when replacing "
                "an existing file intentionally."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path."},
                    "content": {"type": "string", "description": "Complete UTF-8 text."},
                    "overwrite": {"type": "boolean"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            handler=lambda arguments: _write_file(workspace, arguments),
            risk=ToolRisk.WRITE,
        ),
        Tool(
            name="edit_file",
            description=(
                "Replace exact text in one UTF-8 file. By default old_text must "
                "occur exactly once; set replace_all=true deliberately for all matches."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path."},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
                "required": ["path", "old_text", "new_text"],
                "additionalProperties": False,
            },
            handler=lambda arguments: _edit_file(workspace, arguments),
            risk=ToolRisk.WRITE,
        ),
    )


def _write_file(workspace: Workspace, arguments: Mapping[str, Any]) -> ToolResult:
    reject_extra_arguments(arguments, {"path", "content", "overwrite"})
    path_text = text_argument(arguments, "path")
    content = text_argument(arguments, "content", allow_empty=True)
    overwrite = boolean_argument(arguments, "overwrite", default=False)
    encoded_content = content.encode("utf-8")
    if len(encoded_content) > MAX_WRITE_BYTES:
        return ToolResult.failure(
            "TOO_LARGE",
            f"content exceeds {MAX_WRITE_BYTES} UTF-8 bytes",
        )

    try:
        target = workspace.resolve_for_write(path_text)
        existed = target.exists()
        if existed and not overwrite:
            return ToolResult.failure(
                "ALREADY_EXISTS",
                "File already exists; use edit_file or set overwrite=true",
            )
        previous_bytes = target.stat().st_size if existed else 0
        _atomic_write(target, encoded_content)
    except WorkspaceError as error:
        return ToolResult.failure(error.code, str(error))
    except OSError:
        return ToolResult.failure("IO_ERROR", f"Could not write file: {path_text}")

    return ToolResult.success(
        {
            "path": workspace.display_path(target),
            "created": not existed,
            "bytes_written": len(encoded_content),
            "previous_bytes": previous_bytes,
        }
    )


def _edit_file(workspace: Workspace, arguments: Mapping[str, Any]) -> ToolResult:
    reject_extra_arguments(arguments, {"path", "old_text", "new_text", "replace_all"})
    path_text = text_argument(arguments, "path")
    old_text = text_argument(arguments, "old_text")
    new_text = text_argument(arguments, "new_text", allow_empty=True)
    replace_all = boolean_argument(arguments, "replace_all", default=False)
    if old_text == new_text:
        return ToolResult.failure("INVALID_ARGUMENTS", "old_text and new_text are identical")

    try:
        target = workspace.resolve_for_write(path_text)
        if not target.exists():
            return ToolResult.failure("NOT_FOUND", f"Path not found: {path_text}")
        original_bytes = target.read_bytes()
        if len(original_bytes) > MAX_WRITE_BYTES:
            return ToolResult.failure(
                "TOO_LARGE",
                f"File exceeds {MAX_WRITE_BYTES} bytes",
            )
        try:
            original = original_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult.failure("INVALID_ENCODING", "File is not valid UTF-8 text")

        match_count = original.count(old_text)
        if match_count == 0:
            return ToolResult.failure("TEXT_NOT_FOUND", "old_text was not found")
        if match_count > 1 and not replace_all:
            return ToolResult.failure(
                "AMBIGUOUS_MATCH",
                f"old_text appears {match_count} times; make it unique or set replace_all=true",
            )

        updated = original.replace(old_text, new_text, -1 if replace_all else 1)
        updated_bytes = updated.encode("utf-8")
        if len(updated_bytes) > MAX_WRITE_BYTES:
            return ToolResult.failure(
                "TOO_LARGE",
                f"Edited content exceeds {MAX_WRITE_BYTES} UTF-8 bytes",
            )
        _atomic_write(target, updated_bytes)
    except WorkspaceError as error:
        return ToolResult.failure(error.code, str(error))
    except OSError:
        return ToolResult.failure("IO_ERROR", f"Could not edit file: {path_text}")

    diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=path_text,
            tofile=path_text,
        )
    )
    diff_truncated = len(diff) > MAX_DIFF_CHARACTERS
    if diff_truncated:
        diff = diff[:MAX_DIFF_CHARACTERS]
    return ToolResult.success(
        {
            "path": workspace.display_path(target),
            "replacements": match_count if replace_all else 1,
            "diff": diff,
            "diff_truncated": diff_truncated,
        }
    )


def _atomic_write(target: Path, content: bytes) -> None:
    existing_mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else None
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=".coding-agent-",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_name = temporary_file.name
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        if existing_mode is not None:
            os.chmod(temporary_name, existing_mode)
        os.replace(temporary_name, target)
        temporary_name = None
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
