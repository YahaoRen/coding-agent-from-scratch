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
    Agent,
    AgentEvent,
    AgentEventKind,
    AgentLimits,
    AgentStatus,
)
from coding_agent.config import ConfigurationError, Settings
from coding_agent.context import ContextLimits, ContextWindow
from coding_agent.policy import (
    AllowAllPolicy,
    CallbackApprovalPolicy,
    ToolRisk,
)
from coding_agent.providers.openai_compatible import OpenAICompatibleClient
from coding_agent.tools import (
    ToolRegistry,
    create_command_tool,
    create_read_only_tools,
    create_write_tools,
)
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
        "--context-characters",
        type=int,
        default=120_000,
        help="Conservative total context character budget (default: 120000).",
    )
    run.add_argument(
        "--yes",
        action="store_true",
        help="Automatically approve every write and command (trusted workspaces only).",
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
    try:
        settings = Settings.load(env_file=arguments.env_file)
        workspace = Workspace(arguments.workspace)
        limits = AgentLimits(
            max_steps=arguments.max_steps,
            max_tool_calls=arguments.max_tool_calls,
        )
        context_window = ContextWindow(
            ContextLimits(max_characters=arguments.context_characters)
        )
    except (ConfigurationError, WorkspaceError, ValueError) as error:
        print(f"Setup error: {error}", file=sys.stderr)
        return 2

    tools = ToolRegistry(
        create_read_only_tools(workspace)
        + create_write_tools(workspace)
        + (create_command_tool(workspace, secrets=(settings.api_key,)),)
    )
    if arguments.yes:
        print("Warning: automatically approving all writes and commands.")
        approval_policy = AllowAllPolicy()
    else:
        approval_policy = CallbackApprovalPolicy(_prompt_for_approval)

    agent = Agent(
        OpenAICompatibleClient(settings),
        tools,
        limits=limits,
        approval_policy=approval_policy,
        observer=_print_event,
        context_window=context_window,
    )
    try:
        result = agent.run(arguments.task)
    except KeyboardInterrupt:
        print("\nCancelled by user.", file=sys.stderr)
        return 130

    if result.status is AgentStatus.COMPLETED:
        print("\nCompleted:\n")
        print(result.final_text)
        return 0

    print(f"\nStopped: {result.status.value}", file=sys.stderr)
    if result.error:
        print(result.error, file=sys.stderr)
    return 2


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
