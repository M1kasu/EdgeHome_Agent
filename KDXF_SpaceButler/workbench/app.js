const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const state = {
  status: null,
  devices: [],
  rooms: [],
  roomFilter: "all",
  view: "home",
};

const typeMeta = {
  light: { label: "灯光", icon: "lightbulb", tone: "amber" },
  switch: { label: "智能开关", icon: "toggle-right", tone: "blue" },
  climate: { label: "空调", icon: "air-vent", tone: "cyan" },
  curtain: { label: "窗帘", icon: "blinds", tone: "green" },
};

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

function escapeHtml(value) {
  return text(value, "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  }[character]));
}

function refreshIcons() {
  if (window.lucide) window.lucide.createIcons();
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
  $("#climate-value").textContent = climate === "cool" ? "制冷" : climate === "heat" ? "制热" : climate === "off" ? "关闭" : text(climate);
  $("#power-value").textContent = climate === "off" ? "0 W" : "1050 W";
  $("#minutes-value").textContent = `${payload.unoccupied_minutes ?? 0} 分钟`;
  $("#minutes-range").value = payload.unoccupied_minutes ?? 23;
  $("#minutes-output").textContent = payload.unoccupied_minutes ?? 23;
  $("#feedback-value").textContent = text(payload.feedback?.state, "未上报");
  $("#snapshot-time").textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  $("#presence-visual").innerHTML = `<i data-lucide="${occupied ? "user-round-check" : "user-round-x"}"></i><span>${occupied ? "有人" : "无人"}</span>`;
  $("#window-visual").classList.toggle("closed", !open);
  $("#ac-visual").classList.toggle("off", climate === "off");
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
  const actions = (plan?.actions || []).map((action) => (
    `<span class="action-chip">${escapeHtml(action.entity_id)} · ${escapeHtml(action.capability)}</span>`
  )).join("");
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

function renderInventory(payload) {
  state.devices = payload.devices || [];
  state.rooms = payload.rooms || [];
  const totalPower = state.rooms.reduce((sum, room) => sum + Number(room.power_w || 0), 0);
  const online = state.devices.filter((device) => device.online).length;
  $("#room-count").textContent = state.rooms.length;
  $("#device-count").textContent = state.devices.length;
  $("#online-count").textContent = `${online}/${state.devices.length}`;
  $("#total-power").textContent = `${Math.round(totalPower)} W`;
  $("#inventory-summary").textContent = `${state.devices.length} 台设备 · ${state.rooms.length} 个空间`;
  renderRoomTabs();
  renderDeviceGrid();
}

function renderRoomTabs() {
  const tabs = [
    { room: "all", name: "全部", devices: state.devices.length },
    ...state.rooms,
  ];
  if (state.roomFilter !== "all" && !state.rooms.some((room) => room.room === state.roomFilter)) {
    state.roomFilter = "all";
  }
  $("#room-tabs").innerHTML = tabs.map((room) => `
    <button class="room-tab ${state.roomFilter === room.room ? "active" : ""}" data-room="${escapeHtml(room.room)}">
      <span>${escapeHtml(room.name)}</span><strong>${escapeHtml(room.devices)}</strong>
    </button>`).join("");
}

function renderDeviceGrid() {
  const devices = state.roomFilter === "all"
    ? state.devices
    : state.devices.filter((device) => device.room === state.roomFilter);
  const grid = $("#device-grid");
  if (!devices.length) {
    grid.innerHTML = `
      <div class="empty-inventory">
        <i data-lucide="package-open"></i>
        <strong>这个空间还没有设备</strong>
        <button class="primary" data-open-add-device><i data-lucide="plus"></i><span>添加设备</span></button>
      </div>`;
    refreshIcons();
    return;
  }
  grid.innerHTML = devices.map(renderDeviceCard).join("");
  refreshIcons();
}

function renderDeviceCard(device) {
  const meta = typeMeta[device.type] || { label: device.type, icon: "cpu", tone: "blue" };
  const status = deviceStateSummary(device);
  const discovered = device.discovered ? "HA 已发现" : "等待 HA";
  return `
    <article class="device-card" data-device-id="${escapeHtml(device.device_id)}">
      <div class="device-card-heading">
        <div class="device-identity">
          <span class="device-icon ${meta.tone}"><i data-lucide="${meta.icon}"></i></span>
          <div>
            <h3>${escapeHtml(device.name)}</h3>
            <p>${escapeHtml(device.room_name)} · ${escapeHtml(meta.label)}</p>
          </div>
        </div>
        <div class="device-tools">
          <span class="device-online ${device.online ? "online" : "offline"}">${device.online ? "在线" : "离线"}</span>
          ${device.removable ? `<button class="icon-button danger-ghost" data-device-action="delete" title="删除设备"><i data-lucide="trash-2"></i></button>` : ""}
        </div>
      </div>
      <div class="device-state">
        <strong>${escapeHtml(status.primary)}</strong>
        <span>${escapeHtml(status.secondary)}</span>
      </div>
      ${renderDeviceControls(device)}
      <div class="device-footer">
        <span class="discovery-state ${device.discovered ? "ready" : ""}"><i data-lucide="${device.discovered ? "badge-check" : "loader-circle"}"></i>${discovered}</span>
        <code title="${escapeHtml(device.entity_id)}">${escapeHtml(device.entity_id)}</code>
      </div>
      <label class="fault-control">
        <span>故障模式</span>
        <select data-device-fault>
          ${faultOptions(device.fault_mode)}
        </select>
      </label>
    </article>`;
}

function renderDeviceControls(device) {
  const deviceState = device.state || {};
  if (device.type === "light") {
    const on = String(deviceState.power).toUpperCase() === "ON";
    const brightness = Math.round(Number(deviceState.brightness || 0) * 100 / 255);
    return `
      <div class="device-control-row">
        <button class="power-button ${on ? "on" : ""}" data-device-action="toggle" title="${on ? "关闭" : "开启"}">
          <i data-lucide="power"></i>
        </button>
        <label class="inline-slider">
          <span>亮度</span>
          <input type="range" min="1" max="100" value="${Math.max(brightness, 1)}" data-device-brightness>
          <output>${brightness}%</output>
        </label>
      </div>`;
  }
  if (device.type === "switch") {
    const on = String(deviceState.power).toUpperCase() === "ON";
    return `
      <div class="switch-control">
        <span>${on ? "设备运行中" : "设备已关闭"}</span>
        <button class="power-button ${on ? "on" : ""}" data-device-action="toggle" title="${on ? "关闭" : "开启"}">
          <i data-lucide="power"></i>
        </button>
      </div>`;
  }
  if (device.type === "curtain") {
    const position = Number(deviceState.position || 0);
    return `
      <label class="curtain-control">
        <span>开合位置</span>
        <input type="range" min="0" max="100" step="5" value="${position}" data-device-position>
        <output>${position}%</output>
      </label>`;
  }
  const mode = String(deviceState.mode || "off");
  const temperature = Number(deviceState.temperature || 24);
  return `
    <div class="climate-control">
      <div class="segmented" aria-label="空调模式">
        ${["off", "cool", "heat"].map((item) => `
          <button class="${mode === item ? "active" : ""}" data-device-action="climate-mode" data-mode="${item}">
            ${item === "off" ? "关闭" : item === "cool" ? "制冷" : "制热"}
          </button>`).join("")}
      </div>
      <div class="temperature-stepper">
        <button data-device-action="temperature-down" title="降低温度"><i data-lucide="minus"></i></button>
        <input type="number" min="16" max="30" step="0.5" value="${temperature}" data-device-temperature>
        <button data-device-action="temperature-up" title="提高温度"><i data-lucide="plus"></i></button>
        <button class="apply-temperature" data-device-action="apply-temperature" title="应用温度"><i data-lucide="check"></i></button>
      </div>
    </div>`;
}

function deviceStateSummary(device) {
  const value = device.state || {};
  if (!device.online) return { primary: "设备离线", secondary: `故障：${device.fault_mode}` };
  if (device.type === "light") {
    const on = String(value.power).toUpperCase() === "ON";
    const brightness = Math.round(Number(value.brightness || 0) * 100 / 255);
    return { primary: on ? "已开启" : "已关闭", secondary: on ? `亮度 ${brightness}%` : "功耗 0 W" };
  }
  if (device.type === "switch") {
    const on = String(value.power).toUpperCase() === "ON";
    return { primary: on ? "已开启" : "已关闭", secondary: `${Number(value.power_w || 0).toFixed(1)} W` };
  }
  if (device.type === "curtain") {
    return { primary: `${Number(value.position || 0)}%`, secondary: value.status === "open" ? "已打开" : value.status === "closed" ? "已关闭" : text(value.status) };
  }
  const mode = value.mode === "cool" ? "制冷" : value.mode === "heat" ? "制热" : "已关闭";
  return { primary: mode, secondary: `设定 ${Number(value.temperature || 24)}°C · 室温 ${Number(value.current_temperature || 0)}°C` };
}

function faultOptions(selected) {
  return [
    ["none", "正常"],
    ["offline", "离线"],
    ["reject", "拒绝命令"],
    ["delay", "响应延迟"],
    ["ack_without_state_change", "ACK 不变"],
    ["invalid_state", "无效状态"],
  ].map(([value, label]) => `<option value="${value}" ${selected === value ? "selected" : ""}>${label}</option>`).join("");
}

async function loadStatus() {
  try {
    renderStatus(await api("/api/status"));
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadDevices() {
  try {
    renderInventory(await api("/api/devices"));
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadEvents() {
  try {
    const payload = await api("/api/events");
    const events = payload.events || [];
    $("#event-count").textContent = `${events.length} 条`;
    $("#events-body").innerHTML = events.length ? events.map((event) => `
      <tr>
        <td>${escapeHtml(new Date(event.timestamp).toLocaleTimeString("zh-CN", { hour12: false }))}</td>
        <td><span class="event-type">${escapeHtml(event.event_type)}</span></td>
        <td>${escapeHtml(event.device_id)}</td>
        <td>${escapeHtml(event.payload?.command_id || event.payload?.mode || event.payload?.entity_id || "--")}</td>
      </tr>`).join("") : `<tr><td colspan="4">暂无事件</td></tr>`;
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadAll() {
  await Promise.all([loadStatus(), loadDevices()]);
  if (state.view === "events") await loadEvents();
}

async function post(path, body, successMessage) {
  try {
    const payload = await api(path, { method: "POST", body: JSON.stringify(body || {}) });
    if (payload.status) renderStatus(payload.status);
    else if (payload.healthy !== undefined) renderStatus(payload);
    if (payload.devices) renderInventory(payload);
    await loadEvents();
    if (successMessage) toast(successMessage);
    return payload;
  } catch (error) {
    toast(error.message, true);
    return null;
  }
}

async function controlDevice(device, body, message = "设备状态已更新") {
  const payload = await post(`/api/devices/${encodeURIComponent(device.device_id)}/control`, body, message);
  if (payload?.devices) renderInventory(payload);
}

function switchView(view) {
  state.view = view;
  $$(".view").forEach((element) => element.classList.toggle("active", element.dataset.view === view));
  $$(".rail-button[data-view-target]").forEach((button) => button.classList.toggle("active", button.dataset.viewTarget === view));
  if (view === "events") loadEvents();
}

async function handleDeviceClick(event) {
  if (event.target.closest("[data-open-add-device]")) {
    $("#add-device-dialog").showModal();
    return;
  }
  const actionButton = event.target.closest("[data-device-action]");
  if (!actionButton) return;
  const card = actionButton.closest("[data-device-id]");
  const device = state.devices.find((item) => item.device_id === card?.dataset.deviceId);
  if (!device) return;
  const action = actionButton.dataset.deviceAction;
  if (action === "delete") {
    if (!window.confirm(`删除“${device.name}”？`)) return;
    try {
      const payload = await api(`/api/devices/${encodeURIComponent(device.device_id)}`, { method: "DELETE" });
      renderInventory(payload);
      toast("设备已删除");
      await loadEvents();
    } catch (error) {
      toast(error.message, true);
    }
    return;
  }
  if (action === "toggle") {
    const on = String(device.state?.power).toUpperCase() === "ON";
    const body = { power: on ? "off" : "on" };
    if (device.type === "light" && !on) {
      body.brightness = Number($("[data-device-brightness]", card)?.value || 80);
    }
    await controlDevice(device, body);
    return;
  }
  if (action === "climate-mode") {
    const temperature = Number($("[data-device-temperature]", card)?.value || 24);
    await controlDevice(device, { mode: actionButton.dataset.mode, temperature });
    return;
  }
  const temperatureInput = $("[data-device-temperature]", card);
  if (!temperatureInput) return;
  if (action === "temperature-up" || action === "temperature-down") {
    const delta = action === "temperature-up" ? 0.5 : -0.5;
    temperatureInput.value = Math.min(30, Math.max(16, Number(temperatureInput.value) + delta));
    return;
  }
  if (action === "apply-temperature") {
    const mode = device.state?.mode === "off" ? "cool" : device.state?.mode;
    await controlDevice(device, { mode, temperature: Number(temperatureInput.value) });
  }
}

async function handleDeviceChange(event) {
  const card = event.target.closest("[data-device-id]");
  const device = state.devices.find((item) => item.device_id === card?.dataset.deviceId);
  if (!device) return;
  if (event.target.matches("[data-device-brightness]")) {
    await controlDevice(device, { power: "on", brightness: Number(event.target.value) }, "亮度已更新");
  }
  if (event.target.matches("[data-device-position]")) {
    await controlDevice(device, { position: Number(event.target.value) }, "窗帘位置已更新");
  }
  if (event.target.matches("[data-device-fault]")) {
    const mode = event.target.value;
    const payload = await post(
      `/api/devices/${encodeURIComponent(device.device_id)}/fault`,
      { mode, delay_ms: mode === "delay" ? 3000 : 0 },
      "故障模式已更新",
    );
    if (payload?.devices) renderInventory(payload);
  }
}

async function addDevice(event) {
  event.preventDefault();
  const name = $("#new-device-name").value.trim();
  const type = $("#new-device-type").value;
  const room = $("#new-device-room").value;
  const power = $("#new-device-power").value;
  if (!name) return;
  const initialState = type === "light"
    ? { power: power.toUpperCase(), brightness: power === "on" ? 204 : 0 }
    : type === "switch"
      ? { power: power.toUpperCase(), power_w: power === "on" ? 95 : 1.5 }
      : type === "climate"
        ? { mode: power === "on" ? "cool" : "off", temperature: 24, current_temperature: 26 }
        : { position: power === "on" ? 100 : 0, target_position: power === "on" ? 100 : 0, status: power === "on" ? "open" : "closed" };
  $("#submit-add-device").disabled = true;
  try {
    const payload = await api("/api/devices", {
      method: "POST",
      body: JSON.stringify({ name, type, room, initial_state: initialState }),
    });
    renderInventory(payload);
    $("#add-device-dialog").close();
    $("#add-device-form").reset();
    toast(`${name} 已注册并进入 Home Assistant`);
    await loadEvents();
  } catch (error) {
    toast(error.message, true);
  } finally {
    $("#submit-add-device").disabled = false;
  }
}

function toast(message, error = false) {
  const element = $("#toast");
  element.textContent = message;
  element.className = `toast show${error ? " error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    element.className = "toast";
  }, 2800);
}

document.addEventListener("DOMContentLoaded", () => {
  $$(".rail-button[data-view-target]").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.viewTarget));
  });
  $("#refresh-all").addEventListener("click", loadAll);
  $("#devices-reload").addEventListener("click", loadDevices);
  $("#events-reload").addEventListener("click", loadEvents);
  $("#open-add-device").addEventListener("click", () => $("#add-device-dialog").showModal());
  $("#add-device-dialog .dialog-heading button").addEventListener("click", () => $("#add-device-dialog").close());
  $("#add-device-dialog .dialog-actions .secondary").addEventListener("click", () => $("#add-device-dialog").close());
  $("#add-device-form").addEventListener("submit", addDevice);
  $("#room-tabs").addEventListener("click", (event) => {
    const tab = event.target.closest("[data-room]");
    if (!tab) return;
    state.roomFilter = tab.dataset.room;
    renderRoomTabs();
    renderDeviceGrid();
  });
  $("#device-grid").addEventListener("click", handleDeviceClick);
  $("#device-grid").addEventListener("change", handleDeviceChange);
  $("#minutes-range").addEventListener("input", (event) => {
    $("#minutes-output").textContent = event.target.value;
  });
  $("#reset-scene").addEventListener("click", () => post(
    "/api/scene/reset",
    { unoccupied_minutes: Number($("#minutes-range").value) },
    "节能条件已重置",
  ));
  $("#observe-scene").addEventListener("click", () => post(
    "/api/observe",
    { unoccupied_minutes: Number($("#minutes-range").value) },
    "主动分析完成",
  ));
  $("#confirm-action").addEventListener("click", () => post("/api/message", { text: "确认" }, "确认已发送"));
  $("#clear-memory").addEventListener("click", () => post("/api/preferences/clear", {}, "偏好已清空"));
  $("#fault-mode").addEventListener("change", (event) => post(
    "/api/fault",
    { mode: event.target.value, delay_ms: event.target.value === "delay" ? 3000 : 0 },
    "空调故障模式已更新",
  ));
  $("#feedback-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("#feedback-input");
    if (!input.value.trim()) return;
    post("/api/message", { text: input.value }, "消息已处理");
    input.value = "";
  });
  refreshIcons();
  loadAll();
  loadEvents();
  setInterval(loadAll, 5000);
});
