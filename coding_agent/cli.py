"""Command-line entry point for the coding agent."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from coding_agent import __version__


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse command-line arguments and return a process exit code."""

    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0
