"use strict";

const tokenElement = document.querySelector('meta[name="workbench-token"]');
const workbenchToken = tokenElement ? tokenElement.content : "";

const ACTIVE_STATUSES = new Set(["running", "waiting_approval", "cancelling"]);
const TERMINAL_STATUSES = new Set(["completed", "cancelled", "stopped"]);
const STATUS_LABELS = {
  running: "运行中",
  waiting_approval: "等待批准",
  cancelling: "正在停止",
  completed: "已完成",
  cancelled: "已停止",
  stopped: "异常停止",
};
const STATUS_TONES = {
  running: "active",
  waiting_approval: "warning",
  cancelling: "warning",
  completed: "success",
  cancelled: "idle",
  stopped: "danger",
};
const TONE_LABELS = {
  active: "进行中",
  success: "已完成",
  warning: "需注意",
  danger: "未成功",
  muted: "已停止",
};
const DEMO_TASK =
  "请修复当前项目中的失败测试。业务要求：如果任何商品库存不足，整个预留必须失败，任何库存都不能变化。不要修改测试或降低断言。请先检查项目并运行测试，只做必要修改，最后再次运行测试确认全部通过。";

const elements = {
  workspaceName: document.getElementById("workspace-name"),
  configurationPill: document.getElementById("configuration-pill"),
  configurationStatus: document.getElementById("configuration-status"),
  configurationHint: document.getElementById("configuration-hint"),
  taskPanel: document.getElementById("task-panel"),
  taskInput: document.getElementById("task-input"),
  taskCount: document.getElementById("task-count"),
  startButton: document.getElementById("start-button"),
  cancelButton: document.getElementById("cancel-button"),
  runStatus: document.getElementById("run-status"),
  resultPanel: document.getElementById("result-panel"),
  resultMark: document.getElementById("result-mark"),
  resultHeading: document.getElementById("result-heading"),
  resultText: document.getElementById("result-text"),
  resultSteps: document.getElementById("result-steps"),
  resultTools: document.getElementById("result-tools"),
  resultDuration: document.getElementById("result-duration"),
  eventCount: document.getElementById("event-count"),
  timelineScroll: document.getElementById("timeline-scroll"),
  timelineEmpty: document.getElementById("timeline-empty"),
  eventList: document.getElementById("event-list"),
  jumpLatest: document.getElementById("jump-latest"),
  approvalPanel: document.getElementById("approval-panel"),
  approvalTitle: document.getElementById("approval-title"),
  approvalRisk: document.getElementById("approval-risk"),
  approvalSummary: document.getElementById("approval-summary"),
  approvalPreview: document.getElementById("approval-preview"),
  approveButton: document.getElementById("approve-button"),
  rejectButton: document.getElementById("reject-button"),
  detailEmpty: document.getElementById("detail-empty"),
  eventDetail: document.getElementById("event-detail"),
  detailTime: document.getElementById("detail-time"),
  detailTone: document.getElementById("detail-tone"),
  detailTool: document.getElementById("detail-tool"),
  detailTitle: document.getElementById("detail-title"),
  detailSummary: document.getElementById("detail-summary"),
  detailPreview: document.getElementById("detail-preview"),
  previewHint: document.getElementById("preview-hint"),
  notice: document.getElementById("notice"),
};

const state = {
  snapshot: null,
  selectedEventId: null,
  autoFollow: true,
  actionPending: false,
  taskTouched: false,
  submittedApprovalId: null,
  noticeTimer: null,
  stopped: false,
};

function setText(element, value) {
  element.textContent = value == null ? "" : String(value);
}

function delay(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function errorMessage(payload, fallback) {
  if (payload && typeof payload.error === "object" && payload.error) {
    if (typeof payload.error.message === "string") {
      return payload.error.message;
    }
  }
  if (payload && typeof payload.error === "string") {
    return payload.error;
  }
  return fallback;
}

async function request(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  headers.set("X-Workbench-Token", workbenchToken);
  if (options.body !== undefined) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(path, {
    method: options.method || "GET",
    headers,
    body: options.body,
    cache: "no-store",
    credentials: "same-origin",
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch (_error) {
    payload = null;
  }
  if (!response.ok) {
    const message = errorMessage(payload, `请求失败（${response.status}）`);
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return payload;
}

function post(path, body) {
  return request(path, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

function showNotice(message, isError = false) {
  if (state.noticeTimer !== null) {
    window.clearTimeout(state.noticeTimer);
  }
  setText(elements.notice, message);
  elements.notice.classList.toggle("is-error", isError);
  elements.notice.hidden = false;
  state.noticeTimer = window.setTimeout(() => {
    elements.notice.hidden = true;
    state.noticeTimer = null;
  }, isError ? 5200 : 2800);
}

function updateTaskCount() {
  setText(elements.taskCount, `${elements.taskInput.value.length} / 8000`);
  updateControls();
}

function updateControls() {
  const snapshot = state.snapshot;
  const configurationReady = Boolean(
    snapshot && snapshot.configuration && snapshot.configuration.ready,
  );
  const run = snapshot ? snapshot.run : null;
  const active = Boolean(run && ACTIVE_STATUSES.has(run.status));
  const hasTask = elements.taskInput.value.trim().length > 0;

  elements.startButton.disabled =
    state.actionPending || active || !configurationReady || !hasTask;
  elements.cancelButton.hidden = !active;
  elements.cancelButton.disabled = state.actionPending || Boolean(run && run.status === "cancelling");
  elements.taskInput.disabled = active || state.actionPending;
  elements.taskPanel.classList.toggle("is-active", active);

  if (state.actionPending && !active) {
    setText(elements.startButton, "正在启动…");
  } else {
    setText(elements.startButton, TERMINAL_STATUSES.has(run && run.status) ? "运行新任务" : "开始任务");
  }
  setText(elements.cancelButton, run && run.status === "cancelling" ? "正在停止…" : "停止任务");
}

function renderConfiguration(snapshot) {
  setText(elements.workspaceName, snapshot.workspace || "未命名工作区");
  const configuration = snapshot.configuration || {};
  elements.configurationHint.hidden = Boolean(configuration.ready);
  elements.configurationPill.classList.remove(
    "status-pill--checking",
    "status-pill--ready",
    "status-pill--error",
  );
  if (configuration.ready) {
    elements.configurationPill.classList.add("status-pill--ready");
    const model = configuration.model ? ` · ${configuration.model}` : "";
    setText(elements.configurationStatus, `模型已配置${model}`);
    elements.configurationPill.title = "模型配置已就绪";
  } else {
    elements.configurationPill.classList.add("status-pill--error");
    setText(elements.configurationStatus, "模型未配置");
    elements.configurationPill.title = configuration.message || "请填写本地 .env 文件";
  }

  if (
    !state.taskTouched &&
    !snapshot.run &&
    snapshot.workspace === "inventory_reservation" &&
    !elements.taskInput.value
  ) {
    elements.taskInput.value = DEMO_TASK;
    updateTaskCount();
  }
}

function renderRunStatus(run) {
  elements.runStatus.className = "run-status";
  if (!run) {
    elements.runStatus.classList.add("run-status--idle");
    setText(elements.runStatus, "待开始");
    elements.resultPanel.hidden = true;
    return;
  }

  const tone = STATUS_TONES[run.status] || "idle";
  elements.runStatus.classList.add(`run-status--${tone}`);
  setText(elements.runStatus, STATUS_LABELS[run.status] || run.status);

  if (ACTIVE_STATUSES.has(run.status) && elements.taskInput.value !== run.task) {
    elements.taskInput.value = run.task || "";
    updateTaskCount();
  }
  renderResult(run);
}

function renderResult(run) {
  const terminal = TERMINAL_STATUSES.has(run.status);
  elements.resultPanel.hidden = !terminal;
  if (!terminal) {
    return;
  }

  elements.resultPanel.classList.toggle("is-stopped", run.status === "stopped");
  elements.resultPanel.classList.toggle("is-cancelled", run.status === "cancelled");
  if (run.status === "completed") {
    setText(elements.resultMark, "✓");
    setText(elements.resultHeading, "任务已完成");
  } else if (run.status === "cancelled") {
    setText(elements.resultMark, "■");
    setText(elements.resultHeading, "任务已停止");
  } else {
    setText(elements.resultMark, "!");
    setText(elements.resultHeading, "任务未能完成");
  }

  setText(
    elements.resultText,
    run.final_text || run.error || (run.status === "cancelled" ? "用户取消了本次运行。" : "没有返回补充说明。"),
  );
  const stats = run.stats || {};
  setText(elements.resultSteps, numberOrZero(stats.model_steps));
  setText(elements.resultTools, numberOrZero(stats.tool_calls));
  setText(elements.resultDuration, formatDuration(stats.duration_ms));
}

function numberOrZero(value) {
  return Number.isFinite(value) ? String(value) : "0";
}

function formatDuration(milliseconds) {
  if (!Number.isFinite(milliseconds)) {
    return "—";
  }
  if (milliseconds < 1000) {
    return `${Math.max(0, Math.round(milliseconds))} ms`;
  }
  const seconds = milliseconds / 1000;
  if (seconds < 60) {
    return `${seconds.toFixed(seconds < 10 ? 1 : 0)} 秒`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return `${minutes}分${remainder}秒`;
}

function eventMarker(event) {
  if (event.tone === "success") {
    return "✓";
  }
  if (event.tone === "warning") {
    return "!";
  }
  if (event.tone === "danger") {
    return "×";
  }
  if (event.kind === "run_cancelling") {
    return "■";
  }
  return event.step == null ? "·" : String(event.step);
}

function renderEvents(run, previousRun) {
  const events = Array.isArray(run && run.events) ? run.events : [];
  const previousEvents = Array.isArray(previousRun && previousRun.events)
    ? previousRun.events
    : [];
  const oldLastId = previousEvents.length ? previousEvents[previousEvents.length - 1].id : null;
  const newLastId = events.length ? events[events.length - 1].id : null;
  const selectedWasLatest = state.selectedEventId == null || state.selectedEventId === oldLastId;
  const runChanged = Boolean(previousRun && run && previousRun.id !== run.id);

  if (runChanged) {
    state.selectedEventId = null;
    state.autoFollow = true;
  }

  if (run && run.approval) {
    const approvalEvent = [...events].reverse().find((event) => event.kind === "approval_requested");
    state.selectedEventId = approvalEvent ? approvalEvent.id : newLastId;
    state.autoFollow = true;
  } else if (newLastId !== oldLastId && (selectedWasLatest || state.autoFollow)) {
    state.selectedEventId = newLastId;
  } else if (
    state.selectedEventId != null &&
    !events.some((event) => event.id === state.selectedEventId)
  ) {
    state.selectedEventId = newLastId;
  }

  setText(elements.eventCount, `${events.length} 项`);
  elements.timelineEmpty.hidden = events.length > 0;
  elements.eventList.hidden = events.length === 0;
  elements.eventList.replaceChildren();

  const fragment = document.createDocumentFragment();
  for (const event of events) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    const marker = document.createElement("span");
    const main = document.createElement("span");
    const title = document.createElement("span");
    const summary = document.createElement("span");
    const time = document.createElement("time");

    button.type = "button";
    button.className = "event-button";
    button.dataset.eventId = String(event.id);
    button.dataset.tone = event.tone || "muted";
    button.setAttribute("aria-current", event.id === state.selectedEventId ? "true" : "false");
    button.setAttribute("aria-label", `${event.title || "步骤"}：${event.summary || ""}`);

    marker.className = "event-dot";
    marker.setAttribute("aria-hidden", "true");
    setText(marker, eventMarker(event));
    main.className = "event-main";
    title.className = "event-title";
    summary.className = "event-summary";
    time.className = "event-time";
    time.dateTime = event.time || "";
    setText(title, event.title || "未命名步骤");
    setText(summary, event.summary || "没有补充说明");
    setText(time, event.time || "");

    main.append(title, summary);
    button.append(marker, main, time);
    button.addEventListener("click", () => selectEvent(event.id));
    item.append(button);
    fragment.append(item);
  }
  elements.eventList.append(fragment);

  if (state.autoFollow && events.length > previousEvents.length) {
    window.requestAnimationFrame(scrollToLatest);
  } else {
    updateJumpButton();
  }
}

function selectEvent(eventId) {
  const run = state.snapshot && state.snapshot.run;
  if (!run || !Array.isArray(run.events)) {
    return;
  }
  const lastEvent = run.events[run.events.length - 1];
  state.selectedEventId = eventId;
  state.autoFollow = Boolean(lastEvent && lastEvent.id === eventId);
  renderEventSelection(run);
  for (const button of elements.eventList.querySelectorAll(".event-button")) {
    button.setAttribute(
      "aria-current",
      Number(button.dataset.eventId) === eventId ? "true" : "false",
    );
  }
  updateJumpButton();
}

function renderEventSelection(run) {
  const events = Array.isArray(run && run.events) ? run.events : [];
  const event = events.find((candidate) => candidate.id === state.selectedEventId) || null;
  if (!event) {
    elements.detailEmpty.hidden = false;
    elements.eventDetail.hidden = true;
    setText(elements.detailTime, "");
    return;
  }

  elements.detailEmpty.hidden = true;
  elements.eventDetail.hidden = false;
  setText(elements.detailTime, event.time || "");
  setText(elements.detailTone, TONE_LABELS[event.tone] || "记录");
  elements.detailTone.dataset.tone = event.tone || "muted";
  setText(elements.detailTool, event.tool || (event.step ? `第 ${event.step} 轮` : "运行状态"));
  elements.detailTool.hidden = !event.tool && !event.step;
  setText(elements.detailTitle, event.title || "未命名步骤");
  setText(elements.detailSummary, event.summary || "没有补充说明。");
  setText(elements.previewHint, previewHint(event));
  renderPreview(event.preview || "", event);
}

function previewHint(event) {
  if (event.tool === "edit_file") {
    return "红色删除 · 绿色新增";
  }
  if (event.tool === "run_command") {
    return "本地命令输出";
  }
  return "只读显示";
}

function renderPreview(preview, event) {
  elements.detailPreview.replaceChildren();
  if (!preview) {
    const placeholder = document.createElement("span");
    placeholder.className = "preview-placeholder";
    setText(placeholder, "此步骤没有额外输出。");
    elements.detailPreview.append(placeholder);
    return;
  }

  const isDiff = event.tool === "edit_file" && (
    preview.includes("@@") || preview.startsWith("---") || preview.includes("\n+++")
  );
  if (!isDiff) {
    const line = document.createElement("span");
    line.className = "preview-line";
    setText(line, preview);
    elements.detailPreview.append(line);
    return;
  }

  const lines = preview.split("\n");
  for (const content of lines) {
    const line = document.createElement("span");
    line.className = "preview-line";
    if (content.startsWith("@@")) {
      line.classList.add("preview-line--hunk");
    } else if (content.startsWith("+++") || content.startsWith("---")) {
      line.classList.add("preview-line--header");
    } else if (content.startsWith("+")) {
      line.classList.add("preview-line--added");
    } else if (content.startsWith("-")) {
      line.classList.add("preview-line--removed");
    }
    setText(line, content || " ");
    elements.detailPreview.append(line);
  }
}

function renderApproval(run) {
  const approval = run && run.approval;
  const alreadySubmitted = Boolean(
    approval && approval.id === state.submittedApprovalId,
  );
  elements.approvalPanel.hidden = !approval || alreadySubmitted;
  if (!approval || alreadySubmitted) {
    return;
  }
  setText(elements.approvalTitle, approval.title || "允许本次操作？");
  setText(elements.approvalRisk, approval.risk === "write" ? "文件修改" : "命令执行");
  setText(elements.approvalSummary, approval.summary || "请检查本次操作的详细内容。" );
  setText(elements.approvalPreview, approval.preview || "");
  const busy = state.actionPending;
  elements.approveButton.disabled = busy;
  elements.rejectButton.disabled = busy;
  setText(
    elements.approveButton,
    approval.risk === "write" ? "允许本次修改" : "允许本次运行",
  );
}

function renderSnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== "object") {
    return;
  }
  const previousRun = state.snapshot ? state.snapshot.run : null;
  const incomingRun = snapshot.run;
  if (
    previousRun &&
    incomingRun &&
    previousRun.id === incomingRun.id &&
    Number(incomingRun.revision) < Number(previousRun.revision)
  ) {
    snapshot.run = previousRun;
  }
  const incomingApprovalId =
    snapshot.run && snapshot.run.approval ? snapshot.run.approval.id : null;
  if (
    state.submittedApprovalId !== null &&
    incomingApprovalId !== state.submittedApprovalId
  ) {
    state.submittedApprovalId = null;
  }
  state.snapshot = snapshot;

  renderConfiguration(snapshot);
  renderRunStatus(snapshot.run);
  renderEvents(snapshot.run, previousRun);
  renderEventSelection(snapshot.run);
  renderApproval(snapshot.run);
  updateControls();
}

function scrollToLatest() {
  elements.timelineScroll.scrollTop = elements.timelineScroll.scrollHeight;
  state.autoFollow = true;
  updateJumpButton();
}

function updateJumpButton() {
  const distance =
    elements.timelineScroll.scrollHeight -
    elements.timelineScroll.scrollTop -
    elements.timelineScroll.clientHeight;
  const run = state.snapshot && state.snapshot.run;
  const hasEvents = Boolean(run && Array.isArray(run.events) && run.events.length);
  elements.jumpLatest.hidden = !hasEvents || distance < 56 || state.autoFollow;
}

async function startRun() {
  const task = elements.taskInput.value.trim();
  if (!task || state.actionPending) {
    return;
  }
  state.actionPending = true;
  updateControls();
  try {
    await post("/api/runs", { task });
    state.selectedEventId = null;
    state.autoFollow = true;
    state.submittedApprovalId = null;
    showNotice("任务已启动");
    const snapshot = await request("/api/status");
    renderSnapshot(snapshot);
  } catch (error) {
    showNotice(error instanceof Error ? error.message : "无法启动任务", true);
  } finally {
    state.actionPending = false;
    if (state.snapshot && state.snapshot.run) {
      renderApproval(state.snapshot.run);
    }
    updateControls();
  }
}

async function cancelRun() {
  const run = state.snapshot && state.snapshot.run;
  if (!run || !ACTIVE_STATUSES.has(run.status) || state.actionPending) {
    return;
  }
  state.actionPending = true;
  updateControls();
  try {
    await post(`/api/runs/${encodeURIComponent(run.id)}/cancel`, {});
    showNotice("正在安全停止任务");
  } catch (error) {
    showNotice(error instanceof Error ? error.message : "无法停止任务", true);
  } finally {
    state.actionPending = false;
    updateControls();
  }
}

async function decideApproval(approved) {
  const run = state.snapshot && state.snapshot.run;
  const approval = run && run.approval;
  if (!run || !approval || state.actionPending) {
    return;
  }
  state.actionPending = true;
  renderApproval(run);
  updateControls();
  try {
    await post(`/api/runs/${encodeURIComponent(run.id)}/approval`, {
      approval_id: approval.id,
      approved,
    });
    state.submittedApprovalId = approval.id;
    showNotice(approved ? "已允许本次操作" : "已拒绝本次操作");
  } catch (error) {
    showNotice(error instanceof Error ? error.message : "无法提交审批结果", true);
  } finally {
    state.actionPending = false;
    if (state.snapshot && state.snapshot.run) {
      renderApproval(state.snapshot.run);
    }
    updateControls();
  }
}

async function pollingLoop() {
  while (!state.stopped) {
    try {
      const run = state.snapshot && state.snapshot.run;
      let path = "/api/status";
      if (run && ACTIVE_STATUSES.has(run.status)) {
        const revision = Number.isFinite(run.revision) ? run.revision : -1;
        path += `?after=${encodeURIComponent(revision)}&wait=20`;
      }
      const snapshot = await request(path);
      renderSnapshot(snapshot);
      const updatedRun = snapshot.run;
      if (!updatedRun || !ACTIVE_STATUSES.has(updatedRun.status)) {
        await delay(2500);
      }
    } catch (error) {
      showNotice(
        error instanceof Error ? `本地服务连接中断：${error.message}` : "本地服务连接中断",
        true,
      );
      await delay(2500);
    }
  }
}

elements.taskInput.addEventListener("input", () => {
  state.taskTouched = true;
  updateTaskCount();
});
elements.taskInput.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    event.preventDefault();
    startRun();
  }
});
elements.startButton.addEventListener("click", startRun);
elements.cancelButton.addEventListener("click", cancelRun);
elements.approveButton.addEventListener("click", () => decideApproval(true));
elements.rejectButton.addEventListener("click", () => decideApproval(false));
elements.jumpLatest.addEventListener("click", () => {
  const run = state.snapshot && state.snapshot.run;
  if (run && Array.isArray(run.events) && run.events.length) {
    selectEvent(run.events[run.events.length - 1].id);
  }
  scrollToLatest();
});
elements.timelineScroll.addEventListener("scroll", () => {
  const distance =
    elements.timelineScroll.scrollHeight -
    elements.timelineScroll.scrollTop -
    elements.timelineScroll.clientHeight;
  if (distance > 56) {
    state.autoFollow = false;
  } else if (distance < 12) {
    state.autoFollow = true;
  }
  updateJumpButton();
});
window.addEventListener("beforeunload", () => {
  state.stopped = true;
});

updateTaskCount();
pollingLoop();
