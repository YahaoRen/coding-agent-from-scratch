"""Bounded, read-only tools for inspecting a source workspace."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from coding_agent.tools.arguments import (
    boolean_argument,
    integer_argument,
    reject_extra_arguments,
    text_argument,
)
from coding_agent.tools.base import Tool, ToolResult
from coding_agent.workspace import Workspace, WorkspaceError


MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_READ_LINES = 400
DEFAULT_READ_LINES = 200
MAX_READ_CHARACTERS = 64 * 1024
MAX_LIST_RESULTS = 500
DEFAULT_LIST_RESULTS = 200
MAX_SEARCH_RESULTS = 200
DEFAULT_SEARCH_RESULTS = 50
MAX_SEARCH_LINE_CHARACTERS = 500

IGNORED_DIRECTORIES = {
    ".coding-agent",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}


def create_read_only_tools(workspace: Workspace) -> tuple[Tool, ...]:
    """Create the three inspection tools bound to one workspace."""

    return (
        Tool(
            name="list_files",
            description=(
                "List source files below a workspace directory. "
                "Results are recursive, sorted, and bounded."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative directory path; defaults to '.'.",
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_LIST_RESULTS,
                    },
                },
                "additionalProperties": False,
            },
            handler=lambda arguments: _list_files(workspace, arguments),
        ),
        Tool(
            name="read_file",
            description=(
                "Read a UTF-8 text file using one-based line pagination. "
                "The response preserves the original text."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path."},
                    "start_line": {"type": "integer", "minimum": 1},
                    "max_lines": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_READ_LINES,
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=lambda arguments: _read_file(workspace, arguments),
        ),
        Tool(
            name="search_text",
            description=(
                "Search UTF-8 source files for a literal text query. "
                "Returns bounded, line-oriented matches."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Non-empty literal text."},
                    "path": {
                        "type": "string",
                        "description": "Relative file or directory path; defaults to '.'.",
                    },
                    "case_sensitive": {"type": "boolean"},
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_SEARCH_RESULTS,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=lambda arguments: _search_text(workspace, arguments),
        ),
    )


def _list_files(workspace: Workspace, arguments: Mapping[str, Any]) -> ToolResult:
    reject_extra_arguments(arguments, {"path", "max_results"})
    path_text = text_argument(arguments, "path", default=".")
    max_results = integer_argument(
        arguments,
        "max_results",
        default=DEFAULT_LIST_RESULTS,
        minimum=1,
        maximum=MAX_LIST_RESULTS,
    )
    try:
        directory = workspace.resolve_directory(path_text)
        files: list[str] = []
        truncated = False
        for file_path in _iter_files(directory):
            if len(files) >= max_results:
                truncated = True
                break
            files.append(workspace.display_path(file_path))
    except WorkspaceError as error:
        return ToolResult.failure(error.code, str(error))
    except OSError:
        return ToolResult.failure("IO_ERROR", f"Could not list directory: {path_text}")

    return ToolResult.success(
        {
            "path": workspace.display_path(directory),
            "files": files,
            "count": len(files),
            "truncated": truncated,
        }
    )


def _read_file(workspace: Workspace, arguments: Mapping[str, Any]) -> ToolResult:
    reject_extra_arguments(arguments, {"path", "start_line", "max_lines"})
    path_text = text_argument(arguments, "path")
    start_line = integer_argument(arguments, "start_line", default=1, minimum=1)
    max_lines = integer_argument(
        arguments,
        "max_lines",
        default=DEFAULT_READ_LINES,
        minimum=1,
        maximum=MAX_READ_LINES,
    )
    try:
        file_path = workspace.resolve_file(path_text)
        raw_content = _read_text_bytes(file_path)
    except WorkspaceError as error:
        return ToolResult.failure(error.code, str(error))
    except SourceFileError as error:
        return ToolResult.failure(error.code, str(error))
    except OSError:
        return ToolResult.failure("IO_ERROR", f"Could not read file: {path_text}")

    lines = raw_content.splitlines(keepends=True)
    total_lines = len(lines)
    if total_lines and start_line > total_lines:
        return ToolResult.failure(
            "INVALID_ARGUMENTS",
            f"start_line exceeds the file's {total_lines} lines",
        )

    selected_lines = lines[start_line - 1 : start_line - 1 + max_lines]
    selected_text = "".join(selected_lines)
    content_truncated = len(selected_text) > MAX_READ_CHARACTERS
    if content_truncated:
        selected_text = selected_text[:MAX_READ_CHARACTERS]

    end_line = start_line + len(selected_lines) - 1 if selected_lines else 0
    has_more_lines = end_line < total_lines
    return ToolResult.success(
        {
            "path": workspace.display_path(file_path),
            "content": selected_text,
            "start_line": start_line,
            "end_line": end_line,
            "total_lines": total_lines,
            "next_start_line": end_line + 1 if has_more_lines else None,
            "truncated": has_more_lines or content_truncated,
            "content_truncated": content_truncated,
        }
    )


def _search_text(workspace: Workspace, arguments: Mapping[str, Any]) -> ToolResult:
    reject_extra_arguments(
        arguments,
        {"query", "path", "case_sensitive", "max_results"},
    )
    query = text_argument(arguments, "query")
    path_text = text_argument(arguments, "path", default=".")
    case_sensitive = boolean_argument(arguments, "case_sensitive", default=False)
    max_results = integer_argument(
        arguments,
        "max_results",
        default=DEFAULT_SEARCH_RESULTS,
        minimum=1,
        maximum=MAX_SEARCH_RESULTS,
    )
    try:
        search_root = workspace.resolve(path_text)
        candidates = [search_root] if search_root.is_file() else _iter_files(search_root)
        matches: list[dict[str, Any]] = []
        truncated = False
        needle = query if case_sensitive else query.casefold()
        for file_path in candidates:
            try:
                text = _read_text_bytes(file_path)
            except (SourceFileError, OSError):
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                searchable = line if case_sensitive else line.casefold()
                column = searchable.find(needle)
                if column < 0:
                    continue
                if len(matches) >= max_results:
                    truncated = True
                    break
                matches.append(
                    {
                        "path": workspace.display_path(file_path),
                        "line": line_number,
                        "column": column + 1,
                        "text": line[:MAX_SEARCH_LINE_CHARACTERS],
                    }
                )
            if truncated:
                break
    except WorkspaceError as error:
        return ToolResult.failure(error.code, str(error))
    except OSError:
        return ToolResult.failure("IO_ERROR", f"Could not search path: {path_text}")

    return ToolResult.success(
        {
            "query": query,
            "path": workspace.display_path(search_root),
            "matches": matches,
            "count": len(matches),
            "truncated": truncated,
        }
    )


class SourceFileError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _read_text_bytes(file_path: Path) -> str:
    size = file_path.stat().st_size
    if size > MAX_SOURCE_BYTES:
        raise SourceFileError(
            "TOO_LARGE",
            f"File exceeds the {MAX_SOURCE_BYTES}-byte inspection limit",
        )
    content = file_path.read_bytes()
    if b"\x00" in content[:8192]:
        raise SourceFileError("BINARY_FILE", "Binary files are not supported")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SourceFileError("INVALID_ENCODING", "File is not valid UTF-8 text") from error


def _iter_files(directory: Path) -> Iterator[Path]:
    for current_root, directory_names, file_names in os.walk(directory, followlinks=False):
        root_path = Path(current_root)
        directory_names[:] = sorted(
            name
            for name in directory_names
            if name not in IGNORED_DIRECTORIES
            and not (root_path / name).is_symlink()
        )
        for file_name in sorted(file_names):
            file_path = root_path / file_name
            if not file_path.is_symlink():
                yield file_path
