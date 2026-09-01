"""Command-line entry point for the coding agent."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from coding_agent import __version__
from coding_agent.agent import (
    AgentEvent,
    AgentEventKind,
    AgentLimits,
    AgentStatus,
)
from coding_agent.config import ConfigurationError, Settings
from coding_agent.context import ContextLimits, ContextWindow
from coding_agent.model import RetryPolicy
from coding_agent.policy import (
    AllowAllPolicy,
    CallbackApprovalPolicy,
    ToolRisk,
)
from coding_agent.runtime import build_agent
from coding_agent.session import SessionStore
from coding_agent.workspace import Workspace, WorkspaceError


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser in one easy-to-test function."""

    parser = argparse.ArgumentParser(
        prog="coding-agent",
        description="A small coding agent implemented from scratch.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")

    doctor = subparsers.add_parser(
        "doctor",
        help="Validate local model configuration without making an API request.",
    )
    doctor.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Local dotenv file (default: .env).",
    )

    run = subparsers.add_parser("run", help="Run the coding agent on one task.")
    run.add_argument("task", help="The coding task to complete.")
    run.add_argument(
        "--workspace",
        type=Path,
        default=Path("."),
        help="Workspace directory visible to the agent (default: current directory).",
    )
    run.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Local dotenv file (default: .env).",
    )
    run.add_argument("--max-steps", type=int, default=20)
    run.add_argument("--max-tool-calls", type=int, default=50)
    run.add_argument(
        "--max-identical-calls",
        type=int,
        default=2,
        help="Maximum consecutive identical tool calls before stopping (default: 2).",
    )
    run.add_argument(
        "--context-characters",
        type=int,
        default=120_000,
        help="Conservative total context character budget (default: 120000).",
    )
    run.add_argument(
        "--model-retries",
        type=int,
        default=2,
        help="Retries for transient model failures (default: 2; maximum: 5).",
    )
    approval_group = run.add_mutually_exclusive_group()
    approval_group.add_argument(
        "--yes",
        action="store_true",
        help="Automatically approve file writes; commands still require confirmation.",
    )
    approval_group.add_argument(
        "--yes-all",
        action="store_true",
        help="Automatically approve writes and commands (trusted isolated workspaces only).",
    )
    run.add_argument(
        "--save-session",
        nargs="?",
        type=Path,
        const=Path(".coding-agent/sessions"),
        default=None,
        metavar="DIRECTORY",
        help="Save a redacted JSONL transcript, optionally choosing its directory.",
    )

    web = subparsers.add_parser(
        "web",
        help="Open the localhost-only browser workbench.",
    )
    web.add_argument(
        "--workspace",
        type=Path,
        default=Path("."),
        help="Workspace directory visible to the agent (default: current directory).",
    )
    web.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Local dotenv file (default: .env).",
    )
    web.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Local TCP port (default: 8765; use 0 for an available port).",
    )
    web.add_argument(
        "--no-open",
        action="store_true",
        help="Start the server without opening a browser window.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse command-line arguments and return a process exit code."""

    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        parser.print_help()
        return 0
    if arguments.command == "doctor":
        return _doctor(arguments.env_file)
    if arguments.command == "run":
        return _run(arguments)
    if arguments.command == "web":
        return _web(arguments)
    parser.error(f"Unknown command: {arguments.command}")
    return 2


def _doctor(env_file: Path) -> int:
    try:
        settings = Settings.load(env_file=env_file)
    except ConfigurationError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2

    print("Configuration OK (no API request was sent)")
    print(f"Model: {settings.model}")
    print(f"Base URL: {settings.base_url}")
    print("API key: configured")
    return 0


def _run(arguments: argparse.Namespace) -> int:
    if not arguments.task.strip():
        print("Setup error: task cannot be empty", file=sys.stderr)
        return 2

    try:
        settings = Settings.load(env_file=arguments.env_file)
        workspace = Workspace(
            arguments.workspace,
            protected_paths=(arguments.env_file,),
        )
        if (
            arguments.yes_all
            and arguments.env_file.exists()
            and _path_is_inside(arguments.env_file, workspace.root)
        ):
            raise ConfigurationError(
                "--yes-all requires the model credential file to be outside the workspace"
            )
        limits = AgentLimits(
            max_steps=arguments.max_steps,
            max_tool_calls=arguments.max_tool_calls,
            max_consecutive_identical_calls=arguments.max_identical_calls,
        )
        context_window = ContextWindow(
            ContextLimits(max_characters=arguments.context_characters)
        )
        retry_policy = RetryPolicy(max_attempts=arguments.model_retries + 1)
    except (ConfigurationError, WorkspaceError, ValueError) as error:
        print(f"Setup error: {error}", file=sys.stderr)
        return 2

    if arguments.yes_all:
        print("Warning: automatically approving all writes and commands.")
        approval_policy = AllowAllPolicy()
    elif arguments.yes:
        print("Automatically approving file writes; commands still require confirmation.")
        approval_policy = CallbackApprovalPolicy(_approve_writes_only)
    else:
        approval_policy = CallbackApprovalPolicy(_prompt_for_approval)

    agent = build_agent(
        settings,
        workspace,
        limits=limits,
        approval_policy=approval_policy,
        observer=_print_event,
        context_window=context_window,
        retry_policy=retry_policy,
    )
    try:
        result = agent.run(arguments.task)
    except KeyboardInterrupt:
        print("\nCancelled by user.", file=sys.stderr)
        return 130

    if arguments.save_session is not None:
        try:
            session_path = SessionStore(
                arguments.save_session,
                secrets=(settings.api_key,),
            ).save(result)
            print(f"\nSession saved: {session_path}")
        except OSError as error:
            print(f"\nWarning: could not save session: {error}", file=sys.stderr)

    if result.status is AgentStatus.COMPLETED:
        print("\nCompleted:\n")
        print(result.final_text)
        return 0

    print(f"\nStopped: {result.status.value}", file=sys.stderr)
    if result.error:
        print(result.error, file=sys.stderr)
    return 2


def _web(arguments: argparse.Namespace) -> int:
    try:
        from coding_agent.web import serve_workbench

        serve_workbench(
            arguments.workspace,
            arguments.env_file,
            port=arguments.port,
            open_browser=not arguments.no_open,
        )
    except (OSError, ValueError) as error:
        print(f"Setup error: {error}", file=sys.stderr)
        return 2
    return 0


def _prompt_for_approval(
    tool_name: str,
    risk: ToolRisk,
    arguments: Mapping[str, Any],
) -> bool:
    if risk is ToolRisk.READ:
        return True
    print(f"\nApproval required: {tool_name} ({risk.value})")
    print(_summarize_arguments(arguments))
    try:
        answer = input("Allow this action? [y/N] ").strip().casefold()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def _approve_writes_only(
    tool_name: str,
    risk: ToolRisk,
    arguments: Mapping[str, Any],
) -> bool:
    if risk in {ToolRisk.READ, ToolRisk.WRITE}:
        return True
    return _prompt_for_approval(tool_name, risk, arguments)


def _path_is_inside(path: Path, directory: Path) -> bool:
    expanded = path.expanduser()
    for candidate in (expanded.absolute(), expanded.resolve(strict=False)):
        try:
            candidate.relative_to(directory)
            return True
        except (OSError, RuntimeError, ValueError):
            continue
    return False


def _summarize_arguments(arguments: Mapping[str, Any]) -> str:
    safe_arguments = dict(arguments)
    for name in ("content", "old_text", "new_text"):
        value = safe_arguments.get(name)
        if isinstance(value, str):
            safe_arguments[name] = f"<{len(value)} characters>"
    rendered = json.dumps(safe_arguments, ensure_ascii=False)
    if len(rendered) > 1000:
        return rendered[:1000] + "..."
    return rendered


def _print_event(event: AgentEvent) -> None:
    if event.kind is AgentEventKind.MODEL_REQUEST:
        print(f"\n[step {event.step}] Asking model...")
        return
    if event.kind is AgentEventKind.TOOL_CALL and event.call is not None:
        try:
            arguments = json.loads(event.call.arguments)
        except json.JSONDecodeError:
            arguments = {"raw_arguments": "<invalid JSON>"}
        if not isinstance(arguments, dict):
            arguments = {"raw_arguments": "<expected a JSON object>"}
        print(f"  -> {event.call.name} {_summarize_arguments(arguments)}")
        return
    if (
        event.kind is AgentEventKind.TOOL_RESULT
        and event.call is not None
        and event.result is not None
    ):
        if event.result.ok:
            print(f"  <- {event.call.name}: ok{_result_details(event.result.data)}")
        else:
            error = event.result.error
            if error is None:
                print(f"  <- {event.call.name}: failed")
            else:
                print(f"  <- {event.call.name}: {error.code} - {error.message}")


def _result_details(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    details = []
    for name in ("path", "count", "replacements", "exit_code", "duration_ms"):
        if name in data:
            details.append(f"{name}={data[name]}")
    return f" ({', '.join(details)})" if details else ""
