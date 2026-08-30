"""Approval policies for tools with local side effects."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol


class ToolRisk(str, Enum):
    """The kind of local effect a tool may have."""

    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"


class ApprovalPolicy(Protocol):
    """Decide whether one already-validated tool request may execute."""

    def approve(
        self,
        tool_name: str,
        risk: ToolRisk,
        arguments: Mapping[str, Any],
    ) -> bool:
        """Return True only when the requested local action is allowed."""


class DenySideEffectsPolicy:
    """Safe default: inspect freely, but never mutate or execute."""

    def approve(
        self,
        tool_name: str,
        risk: ToolRisk,
        arguments: Mapping[str, Any],
    ) -> bool:
        return risk is ToolRisk.READ


class AllowAllPolicy:
    """Explicit trusted mode used by tests and the future --yes CLI flag."""

    def approve(
        self,
        tool_name: str,
        risk: ToolRisk,
        arguments: Mapping[str, Any],
    ) -> bool:
        return True


ApprovalCallback = Callable[[str, ToolRisk, Mapping[str, Any]], bool]


@dataclass(frozen=True)
class CallbackApprovalPolicy:
    """Delegate decisions to a CLI or other user interface callback."""

    callback: ApprovalCallback

    def approve(
        self,
        tool_name: str,
        risk: ToolRisk,
        arguments: Mapping[str, Any],
    ) -> bool:
        return bool(self.callback(tool_name, risk, arguments))
