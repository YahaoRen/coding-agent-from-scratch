"""Single-run state management for the local browser workbench."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from coding_agent.agent import AgentEvent, AgentEventKind, AgentStatus
from coding_agent.config import ConfigurationError, Settings
from coding_agent.model import ModelClient
from coding_agent.policy import CallbackApprovalPolicy, ToolRisk
from coding_agent.redaction import normalized_secrets, redact_text
from coding_agent.runtime import build_agent
from coding_agent.tools import ToolResult
from coding_agent.workspace import Workspace, WorkspaceError


MAX_TASK_CHARACTERS = 8_000
MAX_PREVIEW_CHARACTERS = 4_000
MAX_FINAL_TEXT_CHARACTERS = 20_000
MAX_TITLE_CHARACTERS = 120
MAX_SUMMARY_CHARACTERS = 500
MAX_TOOL_NAME_CHARACTERS = 80
ACTIVE_STATUSES = {"running", "waiting_approval", "cancelling"}
TERMINAL_STATUSES = {"completed", "cancelled", "stopped"}

TOOL_LABELS = {
    "list_files": "扫描项目文件",
    "read_file": "读取文件",
    "search_text": "搜索代码",
    "write_file": "新建文件",
    "edit_file": "修改文件",
    "run_command": "运行命令",
}


class WorkbenchError(ValueError):
    """A safe API-facing error with an HTTP status and stable code."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class RunManager:
    """Own at most one active run for one fixed local workspace."""

    def __init__(
        self,
        workspace_path: Path,
        env_file: Path,
        *,
        settings_loader: Callable[[], Settings] | None = None,
        model_factory: Callable[[Settings], ModelClient] | None = None,
        approval_timeout_s: float = 600.0,
    ) -> None:
        if approval_timeout_s <= 0:
            raise ValueError("approval_timeout_s must be positive")
        self._workspace_path = workspace_path
        self._env_file = env_file
        self._settings_loader = settings_loader or (
            lambda: Settings.load(env_file=self._env_file)
        )
        self._model_factory = model_factory
        self._approval_timeout_s = approval_timeout_s
        self._lock = threading.Lock()
        self._run: ManagedRun | None = None
        self._closed = False

        try:
            self._workspace = Workspace(
                workspace_path,
                protected_paths=(env_file,),
            )
        except WorkspaceError as error:
            raise WorkbenchError(400, error.code, str(error)) from None

    def snapshot(
        self,
        *,
        after_revision: int | None = None,
        wait_s: float = 0.0,
    ) -> dict[str, Any]:
        settings, configuration_error = self._load_settings()
        with self._lock:
            current = self._run

        run_snapshot = (
            current.snapshot(after_revision=after_revision, wait_s=wait_s)
            if current is not None
            else None
        )
        return {
            "configuration": {
                "ready": settings is not None,
                "model": (
                    _bounded_line(settings.model, MAX_TITLE_CHARACTERS)
                    if settings is not None
                    else None
                ),
                "message": configuration_error,
            },
            "workspace": self._workspace.root.name,
            "run": run_snapshot,
        }

    def start(self, task: str) -> str:
        if not isinstance(task, str) or not task.strip():
            raise WorkbenchError(400, "INVALID_TASK", "任务不能为空")
        task = task.strip()
        if len(task) > MAX_TASK_CHARACTERS:
            raise WorkbenchError(
                400,
                "TASK_TOO_LONG",
                f"任务不能超过 {MAX_TASK_CHARACTERS} 个字符",
            )

        settings, _ = self._load_settings()
        if settings is None:
            raise WorkbenchError(
                409,
                "CONFIGURATION_REQUIRED",
                "模型尚未配置，请先填写本地 .env 文件",
            )

        with self._lock:
            if self._closed:
                raise WorkbenchError(409, "WORKBENCH_CLOSED", "工作台已经关闭")
            if self._run is not None and self._run.status in ACTIVE_STATUSES:
                raise WorkbenchError(409, "RUN_ACTIVE", "已有任务正在运行")
            model_client = (
                self._model_factory(settings)
                if self._model_factory is not None
                else None
            )
            managed_run = ManagedRun(
                task,
                settings,
                self._workspace,
                model_client=model_client,
                approval_timeout_s=self._approval_timeout_s,
            )
            self._run = managed_run
            managed_run.start()
            return managed_run.id

    def approve(self, run_id: str, approval_id: str, approved: bool) -> None:
        self._get_run(run_id).resolve_approval(approval_id, approved)

    def cancel(self, run_id: str) -> None:
        self._get_run(run_id).cancel()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            current = self._run
        if current is None:
            return
        try:
            current.cancel()
        except WorkbenchError as error:
            if error.code != "RUN_FINISHED":
                raise
        current.join(timeout_s=5.0)

    def _get_run(self, run_id: str) -> "ManagedRun":
        with self._lock:
            current = self._run
        if current is None or current.id != run_id:
            raise WorkbenchError(404, "RUN_NOT_FOUND", "没有找到该任务")
        return current

    def _load_settings(self) -> tuple[Settings | None, str | None]:
        try:
            return self._settings_loader(), None
        except (ConfigurationError, OSError, UnicodeError):
            return None, "模型尚未配置，请在本地 .env 中填写接口信息"


class ManagedRun:
    """Run one Agent in a worker thread and expose a safe public snapshot."""

    def __init__(
        self,
        task: str,
        settings: Settings,
        workspace: Workspace,
        *,
        model_client: ModelClient | None,
        approval_timeout_s: float,
    ) -> None:
        self.id = uuid4().hex
        self._settings = settings
        self._secrets = normalized_secrets((settings.api_key,))
        self._task = redact_text(task, self._secrets)
        self._workspace = workspace
        self._model_client = model_client
        self._approval_timeout_s = approval_timeout_s
        self._condition = threading.Condition()
        self._cancel_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._status = "running"
        self._revision = 0
        self._events: list[dict[str, Any]] = []
        self._pending_approval: dict[str, Any] | None = None
        self._approval_decision: bool | None = None
        self._final_text = ""
        self._error: str | None = None
        self._model_steps = 0
        self._tool_calls = 0
        self._started_at = time.monotonic()
        self._duration_ms: int | None = None
        with self._condition:
            self._add_event_locked(
                kind="run_started",
                title="任务已开始",
                summary="正在准备模型和本地工具",
                tone="active",
            )

    @property
    def status(self) -> str:
        with self._condition:
            return self._status

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run_agent,
            name=f"coding-agent-web-{self.id[:8]}",
            daemon=True,
        )
        self._thread.start()

    def join(self, *, timeout_s: float) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, timeout_s))

    def snapshot(
        self,
        *,
        after_revision: int | None = None,
        wait_s: float = 0.0,
    ) -> dict[str, Any]:
        wait_s = max(0.0, min(wait_s, 25.0))
        with self._condition:
            if (
                after_revision is not None
                and self._revision <= after_revision
                and self._status not in TERMINAL_STATUSES
                and wait_s > 0
            ):
                self._condition.wait_for(
                    lambda: self._revision > after_revision
                    or self._status in TERMINAL_STATUSES,
                    timeout=wait_s,
                )
            return {
                "id": self.id,
                "task": self._task,
                "status": self._status,
                "revision": self._revision,
                "events": [dict(event) for event in self._events],
                "approval": (
                    dict(self._pending_approval)
                    if self._pending_approval is not None
                    else None
                ),
                "final_text": self._final_text,
                "error": self._error,
                "stats": {
                    "model_steps": self._model_steps,
                    "tool_calls": self._tool_calls,
                    "duration_ms": self._duration_ms
                    if self._duration_ms is not None
                    else round((time.monotonic() - self._started_at) * 1000),
                },
            }

    def resolve_approval(self, approval_id: str, approved: bool) -> None:
        if not isinstance(approved, bool):
            raise WorkbenchError(400, "INVALID_DECISION", "审批结果必须为布尔值")
        with self._condition:
            pending = self._pending_approval
            if pending is None or pending["id"] != approval_id:
                raise WorkbenchError(409, "APPROVAL_NOT_PENDING", "该审批已失效")
            if self._status != "waiting_approval":
                raise WorkbenchError(409, "RUN_NOT_WAITING", "任务当前不在等待审批")
            if self._approval_decision is not None:
                raise WorkbenchError(409, "APPROVAL_ALREADY_RESOLVED", "该审批已提交")
            self._approval_decision = approved
            self._condition.notify_all()

    def cancel(self) -> None:
        with self._condition:
            if self._status in TERMINAL_STATUSES:
                raise WorkbenchError(409, "RUN_FINISHED", "任务已经结束")
            if self._status == "cancelling":
                return
            self._cancel_event.set()
            self._status = "cancelling"
            self._approval_decision = False
            self._add_event_locked(
                kind="run_cancelling",
                title="正在停止任务",
                summary="已通知 Agent，正在安全结束当前步骤",
                tone="warning",
            )
            self._condition.notify_all()

    def _run_agent(self) -> None:
        try:
            approval_policy = CallbackApprovalPolicy(self._request_approval)
            agent = build_agent(
                self._settings,
                self._workspace,
                approval_policy=approval_policy,
                observer=self._observe,
                stop_requested=self._cancel_event.is_set,
                model_client=self._model_client,
            )
            result = agent.run(self._task)
        except Exception:
            with self._condition:
                self._status = "stopped"
                self._duration_ms = round(
                    (time.monotonic() - self._started_at) * 1000
                )
                self._error = "工作台运行时发生内部错误"
                self._pending_approval = None
                self._add_event_locked(
                    kind="run_finished",
                    title="任务已停止",
                    summary=self._error,
                    tone="danger",
                )
                self._condition.notify_all()
            return

        with self._condition:
            self._model_steps = result.model_steps
            self._tool_calls = result.tool_calls
            self._duration_ms = round((time.monotonic() - self._started_at) * 1000)
            self._final_text = _bounded_text(
                result.final_text,
                MAX_FINAL_TEXT_CHARACTERS,
            )
            self._error = (
                _bounded_line(result.error, MAX_SUMMARY_CHARACTERS)
                if result.error
                else None
            )
            self._pending_approval = None
            if self._cancel_event.is_set() or result.status is AgentStatus.CANCELLED:
                self._status = "cancelled"
                self._final_text = ""
                self._error = None
                title = "任务已停止"
                tone = "muted"
                summary = "用户取消了本次运行"
            elif result.status is AgentStatus.COMPLETED:
                self._status = "completed"
                title = "任务已完成"
                tone = "success"
                summary = "模型已给出最终结果"
            else:
                self._status = "stopped"
                title = "任务已停止"
                tone = "danger"
                summary = result.error or result.status.value
            self._add_event_locked(
                kind="run_finished",
                title=title,
                summary=summary,
                preview=self._final_text or self._error or "",
                tone=tone,
            )
            self._condition.notify_all()

    def _request_approval(
        self,
        tool_name: str,
        risk: ToolRisk,
        arguments: Mapping[str, Any],
    ) -> bool:
        if risk is ToolRisk.READ:
            return True

        approval_id = uuid4().hex
        title = (
            "允许本次文件修改？"
            if risk is ToolRisk.WRITE
            else "允许本次命令运行？"
        )
        summary, preview = _approval_content(tool_name, arguments)
        deadline = time.monotonic() + self._approval_timeout_s
        with self._condition:
            if self._cancel_event.is_set():
                return False
            self._pending_approval = {
                "id": approval_id,
                "tool_name": _bounded_line(tool_name, MAX_TOOL_NAME_CHARACTERS),
                "risk": risk.value,
                "title": _bounded_line(title, MAX_TITLE_CHARACTERS),
                "summary": _bounded_line(summary, MAX_SUMMARY_CHARACTERS),
                "preview": _bounded_text(preview),
            }
            self._approval_decision = None
            self._status = "waiting_approval"
            self._add_event_locked(
                kind="approval_requested",
                title=title,
                summary=summary,
                preview=preview,
                tone="warning",
                tool=tool_name,
            )
            while self._approval_decision is None and not self._cancel_event.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)

            approved = bool(self._approval_decision) and not self._cancel_event.is_set()
            timed_out = (
                self._approval_decision is None and not self._cancel_event.is_set()
            )
            self._pending_approval = None
            self._approval_decision = None
            if not self._cancel_event.is_set():
                self._status = "running"
            self._add_event_locked(
                kind="approval_resolved",
                title="操作已允许" if approved else "操作未执行",
                summary=(
                    "审批等待超时，已默认拒绝"
                    if timed_out
                    else "用户允许了本次操作"
                    if approved
                    else "用户拒绝了本次操作"
                ),
                tone="success" if approved else "muted",
                tool=tool_name,
            )
            self._condition.notify_all()
            return approved

    def _observe(self, event: AgentEvent) -> None:
        with self._condition:
            if event.kind is AgentEventKind.MODEL_REQUEST:
                self._add_event_locked(
                    kind="model_request",
                    title="分析下一步",
                    summary=f"第 {event.step} 轮模型请求",
                    tone="active",
                    step=event.step,
                )
            elif event.kind is AgentEventKind.TOOL_CALL and event.call is not None:
                summary, preview = _tool_call_content(
                    event.call.name,
                    event.call.arguments,
                )
                self._add_event_locked(
                    kind="tool_call",
                    title=TOOL_LABELS.get(event.call.name, event.call.name),
                    summary=summary,
                    preview=preview,
                    tone="active",
                    step=event.step,
                    tool=event.call.name,
                )
            elif (
                event.kind is AgentEventKind.TOOL_RESULT
                and event.call is not None
                and event.result is not None
            ):
                summary, preview = _tool_result_content(event.result)
                self._add_event_locked(
                    kind="tool_result",
                    title="执行成功" if event.result.ok else "执行未成功",
                    summary=summary,
                    preview=preview,
                    tone="success" if event.result.ok else "danger",
                    step=event.step,
                    tool=event.call.name,
                )
            self._condition.notify_all()

    def _add_event_locked(
        self,
        *,
        kind: str,
        title: str,
        summary: str,
        tone: str,
        preview: str = "",
        step: int | None = None,
        tool: str | None = None,
    ) -> None:
        self._revision += 1
        self._events.append(
            {
                "id": self._revision,
                "kind": kind,
                "title": _bounded_line(title, MAX_TITLE_CHARACTERS),
                "summary": _bounded_line(summary, MAX_SUMMARY_CHARACTERS),
                "preview": _bounded_text(preview),
                "tone": tone,
                "time": datetime.now().astimezone().strftime("%H:%M:%S"),
                "step": step,
                "tool": (
                    _bounded_line(tool, MAX_TOOL_NAME_CHARACTERS)
                    if tool is not None
                    else None
                ),
            }
        )
        self._condition.notify_all()


def _tool_call_content(tool_name: str, raw_arguments: str) -> tuple[str, str]:
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError:
        return "模型给出的参数不是有效 JSON", ""
    if not isinstance(arguments, dict):
        return "工具参数格式不正确", ""
    return _approval_content(tool_name, arguments)


def _approval_content(
    tool_name: str,
    arguments: Mapping[str, Any],
) -> tuple[str, str]:
    path = arguments.get("path")
    if tool_name == "run_command":
        argv = arguments.get("argv")
        if isinstance(argv, list) and argv:
            command = json.dumps(argv, ensure_ascii=False, indent=2)
            return f"运行 {str(argv[0])!r}（{len(argv)} 个参数）", command
        return "运行本地命令", "命令参数格式不正确"
    if tool_name == "search_text":
        query = arguments.get("query", "")
        target = path or "."
        return f"在 {target} 中搜索 {query!r}", ""
    if tool_name == "list_files":
        return f"查看 {path or '.'} 下的项目文件", ""
    if tool_name == "read_file":
        return f"读取 {path}", ""
    if tool_name == "edit_file":
        old_text = str(arguments.get("old_text", ""))
        new_text = str(arguments.get("new_text", ""))
        preview = (
            f"文件: {path}\n\n"
            f"原内容 ({len(old_text)} 字符)\n{_bounded_text(old_text, 900)}\n\n"
            f"新内容 ({len(new_text)} 字符)\n{_bounded_text(new_text, 900)}"
        )
        return f"精确修改 {path}", preview
    if tool_name == "write_file":
        content = str(arguments.get("content", ""))
        preview = (
            f"文件: {path}\n内容长度: {len(content)} 字符\n\n"
            f"{_bounded_text(content, 1_600)}"
        )
        return f"新建或覆盖 {path}", preview
    rendered = json.dumps(dict(arguments), ensure_ascii=False, indent=2)
    return tool_name, _bounded_text(rendered)


def _tool_result_content(result: ToolResult) -> tuple[str, str]:
    if not result.ok:
        error = result.error
        summary = error.message if error is not None else "工具执行失败"
    else:
        summary = "工具已完成"

    data = result.data
    if not isinstance(data, dict):
        return summary, _bounded_text(str(data or ""))

    details: list[str] = []
    for name, label in (
        ("path", "文件"),
        ("count", "数量"),
        ("replacements", "替换"),
        ("exit_code", "退出码"),
        ("duration_ms", "耗时(ms)"),
    ):
        if name in data:
            details.append(f"{label}: {data[name]}")
    if details:
        summary = " · ".join(details)

    preview_parts: list[str] = []
    for name in ("diff", "stdout", "stderr", "content"):
        value = data.get(name)
        if isinstance(value, str) and value.strip():
            preview_parts.append(value.strip())
    if not preview_parts and "matches" in data:
        preview_parts.append(
            json.dumps(data["matches"], ensure_ascii=False, indent=2)
        )
    return summary, _bounded_text("\n\n".join(preview_parts))


def _bounded_text(text: str, limit: int = MAX_PREVIEW_CHARACTERS) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit]}\n\n… 已省略 {omitted} 个字符"


def _bounded_line(text: str, limit: int) -> str:
    normalized = " ".join(str(text).split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: max(0, limit - 1)]}…"
