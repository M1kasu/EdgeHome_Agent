const $ = (selector) => document.querySelector(selector);

const state = { status: null };

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function text(value, fallback = "--") {
  return value === null || value === undefined || value === "" ? fallback : String(value);
}

function renderStatus(payload) {
  state.status = payload;
  const healthy = Boolean(payload.healthy);
  $("#health-dot").className = `status-dot ${healthy ? "healthy" : "error"}`;
  $("#health-label").textContent = healthy ? "链路正常" : "链路异常";
  const occupied = payload.presence?.state === "on";
  const open = payload.window?.state === "on";
  const climate = payload.climate?.state;
  $("#presence-value").textContent = occupied ? "有人" : "无人";
  $("#window-value").textContent = open ? "打开" : "关闭";
  $("#climate-value").textContent = climate === "cool" ? "制冷" : climate === "off" ? "关闭" : text(climate);
  $("#power-value").textContent = climate === "off" ? "0 W" : "1050 W";
  $("#minutes-value").textContent = `${payload.unoccupied_minutes ?? 0} 分钟`;
  $("#minutes-range").value = payload.unoccupied_minutes ?? 23;
  $("#minutes-output").textContent = payload.unoccupied_minutes ?? 23;
  $("#feedback-value").textContent = text(payload.feedback?.state, "未上报");
  $("#snapshot-time").textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  $("#presence-visual").innerHTML = `<i data-lucide="${occupied ? "user-round-check" : "user-round-x"}"></i><span>${occupied ? "有人" : "无人"}</span>`;
  $("#window-visual").classList.toggle("closed", !open);
  $("#ac-visual").style.opacity = climate === "off" ? ".45" : "1";
  renderDecision(payload.last_response);
  renderMemory(payload.preferences || []);
  refreshIcons();
}

function renderDecision(response) {
  const status = $("#decision-status");
  const body = $("#decision-body");
  if (!response) {
    status.className = "decision-status idle";
    status.textContent = "等待分析";
    body.innerHTML = `<div class="empty-state"><i data-lucide="sparkles"></i><span>尚无主动服务计划</span></div>`;
    return;
  }
  const map = {
    needs_confirmation: ["pending", "等待确认"],
    executed: ["success", "执行成功"],
    learned: ["success", "偏好已学习"],
    execution_failed: ["failure", "执行未验证"],
    cancelled: ["idle", "已取消"],
    ignored: ["idle", "未触发"],
    unhandled: ["idle", "未识别"],
  };
  const [className, label] = map[response.status] || ["idle", text(response.status)];
  status.className = `decision-status ${className}`;
  status.textContent = label;
  const plan = response.plan;
  const report = response.report;
  const actions = (plan?.actions || []).map((action) => `<span class="action-chip">${escapeHtml(action.entity_id)} · ${escapeHtml(action.capability)}</span>`).join("");
  body.innerHTML = `
    <div class="decision-title">${escapeHtml(plan?.title || response.learned || "SpaceButler")}</div>
    <div class="decision-copy">${escapeHtml(plan?.explanation || response.message || "")}</div>
    ${actions ? `<div class="action-row">${actions}</div>` : ""}
    <div class="report-grid">
      <div><span>路由</span><strong>${escapeHtml(response.route || "deterministic")}</strong></div>
      <div><span>执行状态</span><strong>${escapeHtml(report?.status || response.status)}</strong></div>
      <div><span>回读验证</span><strong>${report ? (report.verified ? "PASS" : "FAIL") : "--"}</strong></div>
    </div>`;
}

function renderMemory(preferences) {
  $("#memory-count").textContent = preferences.length;
  const list = $("#memory-list");
  if (!preferences.length) {
    list.innerHTML = `<div class="empty-state compact"><span>暂无偏好</span></div>`;
    return;
  }
  list.innerHTML = preferences.map((item) => `
    <div class="memory-item">
      <strong>${escapeHtml(item.preference_type)} = ${escapeHtml(item.value)}</strong>
      <div class="memory-meta">
        <span>${escapeHtml(item.condition)}</span>
        <span>${escapeHtml(item.source)}</span>
        <span>confidence ${escapeHtml(item.confidence)}</span>
        <span>samples ${escapeHtml(item.sample_count)}</span>
      </div>
    </div>`).join("");
}

async function loadStatus() {
  try { renderStatus(await api("/api/status")); }
  catch (error) { toast(error.message, true); }
}

async function loadEvents() {
  try {
    const payload = await api("/api/events");
    const events = payload.events || [];
    $("#events-body").innerHTML = events.length ? events.map((event) => `
      <tr>
        <td>${escapeHtml(new Date(event.timestamp).toLocaleTimeString("zh-CN", { hour12: false }))}</td>
        <td>${escapeHtml(event.event_type)}</td>
        <td>${escapeHtml(event.device_id)}</td>
        <td>${escapeHtml(event.payload?.command_id || event.payload?.mode || "--")}</td>
      </tr>`).join("") : `<tr><td colspan="4">暂无事件</td></tr>`;
  } catch (error) { toast(error.message, true); }
}

async function post(path, body, successMessage) {
  try {
    const payload = await api(path, { method: "POST", body: JSON.stringify(body || {}) });
    renderStatus(payload.status || payload);
    await loadEvents();
    if (successMessage) toast(successMessage);
  } catch (error) { toast(error.message, true); }
}

function toast(message, error = false) {
  const element = $("#toast");
  element.textContent = message;
  element.className = `toast show${error ? " error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.className = "toast", 2600);
}

function escapeHtml(value) {
  return text(value, "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  }[character]));
}

function refreshIcons() {
  if (window.lucide) window.lucide.createIcons();
}

document.addEventListener("DOMContentLoaded", () => {
  $("#minutes-range").addEventListener("input", (event) => $("#minutes-output").textContent = event.target.value);
  $("#reset-scene").addEventListener("click", () => post("/api/scene/reset", { unoccupied_minutes: Number($("#minutes-range").value) }, "场景已重置"));
  $("#observe-scene").addEventListener("click", () => post("/api/observe", { unoccupied_minutes: Number($("#minutes-range").value) }, "分析完成"));
  $("#confirm-action").addEventListener("click", () => post("/api/message", { text: "确认" }, "确认已发送"));
  $("#clear-memory").addEventListener("click", () => post("/api/preferences/clear", {}, "偏好已清空"));
  $("#fault-mode").addEventListener("change", (event) => post("/api/fault", { mode: event.target.value, delay_ms: event.target.value === "delay" ? 3000 : 0 }, "故障状态已更新"));
  $("#feedback-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("#feedback-input");
    if (!input.value.trim()) return;
    post("/api/message", { text: input.value }, "消息已处理");
    input.value = "";
  });
  $("#refresh-status").addEventListener("click", loadStatus);
  $("#refresh-events").addEventListener("click", loadEvents);
  $("#events-reload").addEventListener("click", loadEvents);
  refreshIcons();
  loadStatus();
  loadEvents();
  setInterval(loadStatus, 5000);
});
