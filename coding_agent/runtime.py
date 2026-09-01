"""Build one agent runtime shared by the CLI and local web workbench."""

from __future__ import annotations

from collections.abc import Callable

from coding_agent.agent import Agent, AgentLimits, AgentObserver
from coding_agent.config import Settings
from coding_agent.context import ContextWindow
from coding_agent.model import ModelClient, RetryPolicy, RetryingModelClient
from coding_agent.policy import ApprovalPolicy
from coding_agent.providers.openai_compatible import OpenAICompatibleClient
from coding_agent.tools import (
    ToolRegistry,
    create_command_tool,
    create_read_only_tools,
    create_write_tools,
)
from coding_agent.workspace import Workspace


def build_agent(
    settings: Settings,
    workspace: Workspace,
    *,
    approval_policy: ApprovalPolicy,
    limits: AgentLimits | None = None,
    observer: AgentObserver | None = None,
    context_window: ContextWindow | None = None,
    retry_policy: RetryPolicy | None = None,
    stop_requested: Callable[[], bool] | None = None,
    model_client: ModelClient | None = None,
) -> Agent:
    """Assemble the model, tools and loop with one consistent safety setup."""

    tools = ToolRegistry(
        create_read_only_tools(workspace)
        + create_write_tools(workspace)
        + (
            create_command_tool(
                workspace,
                secrets=(settings.api_key,),
                stop_requested=stop_requested,
            ),
        )
    )
    model = (
        model_client
        if model_client is not None
        else RetryingModelClient(
            OpenAICompatibleClient(settings),
            retry_policy or RetryPolicy(),
        )
    )
    return Agent(
        model,
        tools,
        limits=limits,
        approval_policy=approval_policy,
        observer=observer,
        context_window=context_window,
        secrets=(settings.api_key,),
        stop_requested=stop_requested,
    )
