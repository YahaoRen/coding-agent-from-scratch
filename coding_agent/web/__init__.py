"""Local browser workbench for the coding agent."""

from __future__ import annotations

from typing import Any


def serve_workbench(*args: Any, **kwargs: Any) -> Any:
    """Import the HTTP server lazily so state-management tests stay lightweight."""

    from coding_agent.web.server import serve_workbench as serve

    return serve(*args, **kwargs)


__all__ = ["serve_workbench"]
