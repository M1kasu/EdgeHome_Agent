const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const state = {
  status: null,
  devices: [],
  rooms: [],
  roomFilter: "all",
  view: "assistant",
  proactive: null,
  conversation: null,
  lastMessageId: null,
  chatBusy: false,
  ruleDraftDirty: false,
  ruleDraftRevision: 0,
  sceneDraftDirty: false,
  sceneDraftRevision: 0,
  nightSafety: null,
  nightDraftDirty: false,
  nightDraftRevision: 0,
  nightBusy: false,
  nightPathOrder: [],
};

const typeMeta = {
  light: { label: "灯光", icon: "lightbulb", tone: "amber" },
  switch: { label: "智能开关", icon: "toggle-right", tone: "blue" },
  climate: { label: "空调", icon: "air-vent", tone: "cyan" },
  curtain: { label: "窗帘", icon: "blinds", tone: "green" },
  presence: { label: "存在传感器", icon: "user-round-check", tone: "green" },
  contact: { label: "门窗传感器", icon: "panel-top-open", tone: "amber" },
  illuminance: { label: "照度传感器", icon: "sun-medium", tone: "amber" },
};

const intentLabels = {
  device_control: "设备控制",
  query_state: "状态查询",
  proactive_service: "主动服务",
  general_chat: "普通对话",
  clarification: "需要澄清",
};

const actionLabels = {
  turn_on: "开启",
  turn_off: "关闭",
  set_brightness: "设置亮度",
  set_temperature: "设置温度",
  set_hvac_mode: "设置模式",
  set_position: "设置位置",
  read_state: "读取状态",
  analyze_proactive: "主动分析",
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
  $("#snapshot-time").textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  if (payload.proactive) renderProactive(payload.proactive);
  if (payload.night_safety) renderNightSafety(payload.night_safety);
  renderDecision(payload.last_response);
  renderMemory(payload.preferences || []);
  refreshIcons();
}

function renderConversation(payload) {
  state.conversation = payload;
  const modelBadge = $("#agent-model-badge");
  modelBadge.classList.toggle("fallback", !payload.llm_available);
  $("span", modelBadge).textContent = payload.llm_available ? "Edge LLM 在线" : "确定性降级";
  const conversationState = $("#conversation-state");
  const busy = Boolean(payload.busy || state.chatBusy);
  conversationState.className = `conversation-state${busy ? " busy" : ""}`;
  conversationState.textContent = busy ? "理解与规划中" : "准备就绪";

  const messages = payload.messages || [];
  const lastMessage = messages.at(-1);
  const shouldScroll = lastMessage?.message_id !== state.lastMessageId;
  const thread = $("#chat-thread");
  thread.innerHTML = messages.length
    ? messages.map((message) => `
      <article class="chat-message ${escapeHtml(message.role)}">
        <div class="chat-message-bubble">${escapeHtml(message.content)}</div>
        <div class="chat-message-meta">
          <span>${message.role === "user" ? "家庭成员" : "SpaceButler"}</span>
          <span>${formatTimestamp(message.created_at)}</span>
          ${message.plan_id ? `<code>${escapeHtml(message.plan_id)}</code>` : ""}
        </div>
      </article>`).join("")
    : `<div class="empty-state"><i data-lucide="messages-square"></i><span>新会话</span></div>`;
  if (payload.busy) {
    thread.insertAdjacentHTML("beforeend", `
      <div class="chat-thinking"><i data-lucide="loader-circle"></i><span>正在理解意图并生成任务计划</span></div>`);
  }
  if (shouldScroll) {
    requestAnimationFrame(() => {
      thread.scrollTop = thread.scrollHeight;
    });
  }
  state.lastMessageId = lastMessage?.message_id || null;
  renderTaskPlan(payload.active_plan);
  refreshIcons();
}

function setChatBusy(busy) {
  state.chatBusy = busy;
  $("#chat-input").disabled = busy;
  $("#chat-send").disabled = busy;
  $("#clear-chat").disabled = busy;
  $$(".prompt-strip [data-chat-prompt]").forEach((button) => {
    button.disabled = busy;
  });
  $("#confirm-chat-plan").disabled = busy;
  $("#cancel-chat-plan").disabled = busy;
  const conversationState = $("#conversation-state");
  conversationState.className = `conversation-state${busy ? " busy" : ""}`;
  conversationState.textContent = busy ? "理解与规划中" : "准备就绪";
}

function renderTaskPlan(plan) {
  const status = $("#plan-status");
  const body = $("#task-plan-body");
  const actions = $("#task-plan-actions");
  if (!plan) {
    status.className = "plan-status idle";
    status.textContent = "暂无计划";
    body.innerHTML = `<div class="empty-state"><i data-lucide="workflow"></i><span>等待对话任务</span></div>`;
    actions.hidden = true;
    return;
  }
  const statusMeta = {
    ready: ["idle", "准备执行"],
    awaiting_confirmation: ["pending", "等待确认"],
    executing: ["executing", "执行中"],
    completed: ["success", "执行完成"],
    partial_success: ["failure", "部分成功"],
    failed: ["failure", "执行失败"],
    cancelled: ["idle", "已取消"],
    clarification: ["pending", "需要澄清"],
    interrupted: ["failure", "执行中断"],
  };
  const [statusClass, statusLabel] = statusMeta[plan.status] || ["idle", text(plan.status)];
  status.className = `plan-status ${statusClass}`;
  status.textContent = statusLabel;
  const steps = plan.steps || [];
  body.innerHTML = `
    <div class="plan-overview">
      <div class="plan-intent-row">
        <span class="intent-badge">${escapeHtml(intentLabels[plan.intent] || plan.intent)}</span>
        <span class="route-badge">${escapeHtml(plan.route || "--")}</span>
      </div>
      <h4>${escapeHtml(plan.summary)}</h4>
      <p>${escapeHtml(plan.reasoning)}</p>
      <div class="plan-id-row">
        <code>${escapeHtml(plan.plan_id)}</code>
        <span>${steps.length} 个步骤</span>
      </div>
    </div>
    <div class="task-step-list">
      ${steps.map((step, index) => renderTaskStep(step, index)).join("")}
    </div>`;
  actions.hidden = plan.status !== "awaiting_confirmation";
}

function renderTaskStep(step, index) {
  const icon = {
    pending: "circle-dashed",
    running: "loader-circle",
    success: "circle-check-big",
    failed: "circle-x",
    skipped: "circle-slash",
  }[step.status] || "circle-dashed";
  const value = taskValue(step);
  const afterState = evidenceState(step.after);
  return `
    <article class="task-step ${escapeHtml(step.status)}">
      <span class="task-step-index">${index + 1}</span>
      <div class="task-step-copy">
        <div class="task-step-title">
          <strong>${escapeHtml(step.device_name || "主动服务")}</strong>
          <span>${escapeHtml(actionLabels[step.action] || step.action)}${value ? ` · ${escapeHtml(value)}` : ""}</span>
        </div>
        <p>${escapeHtml(step.reason)}</p>
        ${step.entity_id ? `<code title="${escapeHtml(step.entity_id)}">${escapeHtml(step.entity_id)}</code>` : ""}
        ${afterState ? `<div class="task-step-evidence">回读：${escapeHtml(afterState)} · ${step.verified ? "PASS" : "FAIL"}</div>` : ""}
        ${step.error ? `<div class="task-step-evidence task-step-error">${escapeHtml(step.error)}</div>` : ""}
      </div>
      <i data-lucide="${icon}"></i>
    </article>`;
}

function taskValue(step) {
  if (step.action === "set_brightness" || step.action === "set_position") return `${step.value}%`;
  if (step.action === "set_temperature") return `${step.value}°C${step.mode ? ` / ${step.mode}` : ""}`;
  if (step.action === "set_hvac_mode") return text(step.value, "");
  return "";
}

function evidenceState(evidence) {
  const value = evidence?.state;
  if (!value) {
    if (evidence?.response?.status) return evidence.response.message || evidence.response.status;
    return "";
  }
  if ("power" in value) {
    const on = String(value.power).toUpperCase() === "ON";
    const brightness = Number.isFinite(Number(value.brightness))
      ? `，亮度 ${Math.round(Number(value.brightness) * 100 / 255)}%`
      : "";
    return `${on ? "开启" : "关闭"}${on ? brightness : ""}`;
  }
  if ("mode" in value) {
    const mode = value.mode === "cool" ? "制冷" : value.mode === "heat" ? "制热" : "关闭";
    return `${mode}，设定 ${value.temperature}°C`;
  }
  if ("position" in value) return `位置 ${value.position}%`;
  if ("occupied" in value) return value.occupied ? "有人" : "无人";
  if ("open" in value) return value.open ? "打开" : "关闭";
  return JSON.stringify(value);
}

function renderProactive(payload) {
  state.proactive = payload;
  const sources = payload.sources || {};
  const config = payload.config || {};
  $("#active-rule-title").textContent = payload.rule_name || "主动规则信号";
  $("#active-room-name").textContent = payload.room?.name || "未绑定";
  $("#active-data-source").textContent = payload.data_source || "--";
  $("#active-refresh-time").textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  const badge = $("#rule-health-badge");
  badge.className = `source-badge ${payload.ready ? "ready" : "warning"}`;
  badge.textContent = !config.enabled ? "规则已停用" : payload.ready ? "实时监听" : "绑定不完整";

  const sourceMeta = {
    presence: { label: "存在信号", icon: "user-round-check", value: sourceValue("presence", sources.presence) },
    contact: { label: "门窗信号", icon: "panel-top-open", value: sourceValue("contact", sources.contact) },
    climate: { label: "执行设备", icon: "air-vent", value: sourceValue("climate", sources.climate) },
  };
  $("#proactive-sources").innerHTML = Object.entries(sourceMeta).map(([role, meta]) => {
    const source = sources[role];
    if (!source) {
      return `
        <article class="signal-card missing">
          <div class="signal-card-icon"><i data-lucide="${meta.icon}"></i></div>
          <div><span>${meta.label}</span><strong>未绑定设备</strong><code>请在右侧选择来源</code></div>
        </article>`;
    }
    const connected = source.online && source.discovered;
    return `
      <article class="signal-card ${connected ? "connected" : "missing"}">
        <div class="signal-card-icon"><i data-lucide="${meta.icon}"></i></div>
        <div class="signal-card-copy">
          <div class="signal-card-title">
            <span>${meta.label}</span>
            <b>${connected ? "在线" : "异常"}</b>
          </div>
          <strong>${escapeHtml(meta.value)}</strong>
          <p>${escapeHtml(source.name)}</p>
          <code title="${escapeHtml(source.entity_id)}">${escapeHtml(source.entity_id)}</code>
          <small>${formatTimestamp(source.updated_at)} · ${source.discovered ? "HA 已发现" : "HA 未发现"}</small>
        </div>
      </article>`;
  }).join("");

  const conditions = payload.conditions || [];
  const metCount = conditions.filter((item) => item.met).length;
  $("#condition-summary").textContent = `${metCount}/${conditions.length} 条满足`;
  $("#proactive-conditions").innerHTML = conditions.map((condition, index) => `
    <div class="condition-row ${condition.met ? "met" : "unmet"}">
      <span class="condition-index">${index + 1}</span>
      <div>
        <strong>${escapeHtml(condition.label)}</strong>
        <code>${escapeHtml(condition.entity_id || "无数据源")}</code>
      </div>
      <div class="condition-reading">
        <span>当前 ${escapeHtml(condition.actual)}</span>
        <small>要求 ${escapeHtml(condition.required)}</small>
      </div>
      <i data-lucide="${condition.met ? "circle-check" : "circle-x"}"></i>
    </div>`).join("");

  renderRuleBinding(payload);
  if (!state.sceneDraftDirty) {
    $("#test-occupied").checked = Boolean(sources.presence?.state?.occupied);
    $("#test-window-open").checked = Boolean(sources.contact?.state?.open);
    $("#test-climate-mode").value = sources.climate?.state?.mode || "off";
    if (!sources.presence?.state?.occupied) {
      $("#minutes-range").value = payload.unoccupied_minutes ?? 0;
      $("#minutes-output").textContent = payload.unoccupied_minutes ?? 0;
    }
  }
}

function sourceValue(role, source) {
  if (!source) return "无数据";
  if (role === "presence") return source.state?.occupied ? "有人" : `无人 ${state.proactive?.unoccupied_minutes ?? 0} 分钟`;
  if (role === "contact") return source.state?.open ? "窗户打开" : "窗户关闭";
  const mode = source.state?.mode;
  return mode === "cool" ? "制冷运行" : mode === "heat" ? "制热运行" : mode === "off" ? "已关闭" : text(mode);
}

function renderNightSafety(payload) {
  state.nightSafety = payload;
  const config = payload.config || {};
  const pathLights = payload.path_lights || [];
  const badge = $("#night-health-badge");
  badge.className = `source-badge ${payload.ready ? "ready" : "warning"}`;
  badge.classList.toggle("warning", Boolean(payload.listener?.error) || !payload.listener?.active);
  badge.textContent = !config.enabled
    ? "规则已停用"
    : payload.listener?.error
      ? "监听异常"
      : !payload.listener?.active
        ? "监听未启动"
        : payload.trigger_ready
          ? "条件已满足"
          : payload.ready
            ? "实体监听中"
            : "来源不完整";
  badge.title = payload.listener?.error || "后台监听 Home Assistant 实体上升沿";
  $("#night-path-summary").textContent = `${pathLights.length} 路 · ${payload.manual_lights || 0} 手动`;
  renderNightConditions(payload.conditions || []);
  renderNightResult(payload.last_response);
  if (state.nightDraftDirty) return;

  state.nightPathOrder = [...(config.path_light_device_ids || [])];
  $("#night-enabled").checked = Boolean(config.enabled);
  $("#night-member-name").value = config.member_name || "爷爷";
  populateSelect(
    $("#night-origin-room"),
    (payload.available_rooms || []).map((room) => ({ value: room.room, label: room.name })),
    config.origin_room,
  );
  populateSelect(
    $("#night-presence-device"),
    (payload.available_presence_sensors || []).map((source) => ({ value: source.device_id, label: `${source.name} · ${source.room_name}` })),
    config.presence_device_id,
  );
  populateSelect(
    $("#night-illuminance-device"),
    (payload.available_illuminance_sensors || []).map((source) => ({ value: source.device_id, label: `${source.name} · ${source.room_name}` })),
    config.illuminance_device_id,
  );
  $("#night-brightness-range").value = config.brightness ?? 18;
  $("#night-brightness-output").textContent = config.brightness ?? 18;
  $("#night-threshold-range").value = config.max_illuminance ?? 50;
  $("#night-threshold-output").textContent = config.max_illuminance ?? 50;
  $("#night-lux-range").value = payload.current_illuminance ?? 8;
  $("#night-lux-output").textContent = payload.current_illuminance ?? 8;
  const selected = new Set(state.nightPathOrder);
  const availableLights = payload.available_lights || [];
  const lightOrder = [
    ...state.nightPathOrder
      .map((deviceId) => availableLights.find((light) => light.device_id === deviceId))
      .filter(Boolean),
    ...availableLights.filter((light) => !selected.has(light.device_id)),
  ];
  $("#night-path-lights").innerHTML = (payload.available_lights || []).length
    ? lightOrder.map((light) => `
      <label class="night-path-item ${selected.has(light.device_id) ? "selected" : ""}">
        <input type="checkbox" data-night-path-id="${escapeHtml(light.device_id)}" ${selected.has(light.device_id) ? "checked" : ""}>
        <span class="night-path-order">${selected.has(light.device_id) ? state.nightPathOrder.indexOf(light.device_id) + 1 : "–"}</span>
        <span class="night-path-copy">
          <strong>${escapeHtml(light.name)}</strong>
          <small>${escapeHtml(light.room_name)} · ${light.online && light.discovered ? "在线" : "异常"}${light.manual_override ? ` · 人工接管 ${Math.max(1, Math.ceil(Number(light.manual_remaining_seconds || 0) / 60))} 分钟` : ""}</small>
        </span>
        <i data-lucide="${light.manual_override ? "hand" : "lightbulb"}"></i>
      </label>`).join("")
    : `<div class="empty-state compact"><span>暂无可用灯光</span></div>`;
  populateNightManualSelect();
  refreshIcons();
}

function renderNightConditions(conditions) {
  $("#night-condition-strip").innerHTML = conditions.map((condition) => `
    <div class="night-condition ${condition.met ? "met" : "waiting"}">
      <i data-lucide="${condition.met ? "circle-check" : "circle-dashed"}"></i>
      <span>${escapeHtml(condition.label)}</span>
      <strong>${escapeHtml(condition.actual)}</strong>
    </div>`).join("");
}

function populateNightManualSelect() {
  const select = $("#night-manual-light");
  const previous = select.value;
  const selectedIds = $$('[data-night-path-id]:checked').map((input) => input.dataset.nightPathId);
  const available = state.nightSafety?.available_lights || [];
  const options = [
    { value: "", label: "无手动接管" },
    ...selectedIds.map((deviceId) => {
      const light = available.find((item) => item.device_id === deviceId);
      return { value: deviceId, label: light?.name || deviceId };
    }),
  ];
  populateSelect(select, options, options.some((item) => item.value === previous) ? previous : "");
}

function syncNightPathUi() {
  const container = $("#night-path-lights");
  const items = $$('[data-night-path-id]', container).map((input) => [input.dataset.nightPathId, input.closest(".night-path-item")]);
  const byId = new Map(items);
  const availableOrder = (state.nightSafety?.available_lights || []).map((light) => light.device_id);
  [...state.nightPathOrder, ...availableOrder.filter((deviceId) => !state.nightPathOrder.includes(deviceId))]
    .forEach((deviceId) => {
      const item = byId.get(deviceId);
      if (!item) return;
      const input = $("[data-night-path-id]", item);
      const order = $(".night-path-order", item);
      const index = state.nightPathOrder.indexOf(deviceId);
      item.classList.toggle("selected", index >= 0);
      input.checked = index >= 0;
      order.textContent = index >= 0 ? String(index + 1) : "–";
      container.append(item);
    });
  populateNightManualSelect();
}

function renderNightResult(response) {
  const container = $("#night-result");
  if (!response) {
    container.innerHTML = `<div class="empty-state compact"><span>等待夜间事件</span></div>`;
    return;
  }
  const report = response.report;
  const actionCount = response.plan?.actions?.length || 0;
  const status = response.status === "executed" && report?.verified
    ? "回读通过"
    : response.status === "ignored"
      ? "保持现状"
      : response.status === "disabled"
        ? "规则停用"
        : "执行异常";
  container.innerHTML = `
    <div class="night-result-heading">
      <strong>${escapeHtml(response.plan?.title || status)}</strong>
      <span class="${report?.verified ? "success" : ""}">${escapeHtml(status)}</span>
    </div>
    <p>${escapeHtml(response.plan?.explanation || response.message || "")}</p>
    <div class="night-result-metrics">
      <span>动作 <strong>${actionCount}</strong></span>
      <span>执行 <strong>${escapeHtml(report?.status || response.status)}</strong></span>
      <span>验证 <strong>${report ? (report.verified ? "PASS" : "FAIL") : "--"}</strong></span>
    </div>`;
}

function formatTimestamp(value) {
  if (!value) return "未上报";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf())
    ? text(value)
    : parsed.toLocaleTimeString("zh-CN", { hour12: false });
}

function renderRuleBinding(payload) {
  if (state.ruleDraftDirty) return;
  const config = payload.config || {};
  $("#rule-enabled").checked = Boolean(config.enabled);
  $("#save-rule").classList.remove("attention");
  populateSelect(
    $("#rule-room"),
    (payload.available_rooms || []).map((room) => ({ value: room.room, label: `${room.name} · ${room.devices} 台设备` })),
    config.room,
  );
  populateRuleDeviceSelects(config.room, config);
}

function populateRuleDeviceSelects(room, config = {}) {
  const available = state.proactive?.available_devices || {};
  [
    ["#rule-presence-device", "presence", config.presence_device_id],
    ["#rule-contact-device", "contact", config.contact_device_id],
    ["#rule-climate-device", "climate", config.climate_device_id],
  ].forEach(([selector, type, selected]) => {
    const options = (available[type] || [])
      .filter((device) => device.room === room)
      .map((device) => ({
        value: device.device_id,
        label: `${device.name}${device.discovered ? "" : " · 未发现"}`,
      }));
    populateSelect($(selector), options, selected);
  });
}

function populateSelect(element, options, selected) {
  const requested = document.activeElement === element ? element.value : selected;
  element.innerHTML = options.length
    ? options.map((option) => `<option value="${escapeHtml(option.value)}">${escapeHtml(option.label)}</option>`).join("")
    : `<option value="">当前空间无可用设备</option>`;
  if (options.some((option) => option.value === requested)) element.value = requested;
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
    disabled: ["idle", "规则已停用"],
    no_pending_plan: ["idle", "没有待执行计划"],
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
  const grid = $("#device-grid");
  if (!grid.contains(document.activeElement)) renderDeviceGrid();
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
          ${faultOptions(device.fault_mode, ["presence", "contact", "illuminance"].includes(device.type))}
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
  if (device.type === "presence") {
    const occupied = Boolean(deviceState.occupied);
    return `
      <div class="sensor-control">
        <div>
          <span>空间占用上报</span>
          <strong>${occupied ? "有人" : "无人"}</strong>
        </div>
        <button class="sensor-state-button ${occupied ? "active" : ""}" data-device-action="sensor-toggle">
          <i data-lucide="${occupied ? "user-round-check" : "user-round-x"}"></i>
          <span>${occupied ? "切换为无人" : "切换为有人"}</span>
        </button>
      </div>`;
  }
  if (device.type === "contact") {
    const open = Boolean(deviceState.open);
    return `
      <div class="sensor-control">
        <div>
          <span>门窗状态上报</span>
          <strong>${open ? "打开" : "关闭"}</strong>
        </div>
        <button class="sensor-state-button ${open ? "active" : ""}" data-device-action="sensor-toggle">
          <i data-lucide="${open ? "panel-top-open" : "panel-top-close"}"></i>
          <span>${open ? "上报关闭" : "上报打开"}</span>
        </button>
      </div>`;
  }
  if (device.type === "illuminance") {
    const illuminance = Number(deviceState.illuminance || 0);
    return `
      <label class="curtain-control">
        <span>照度上报</span>
        <input type="range" min="0" max="500" step="1" value="${illuminance}" data-device-illuminance>
        <output>${illuminance} lux</output>
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
  if (device.type === "presence") {
    return {
      primary: value.occupied ? "检测到有人" : "空间无人",
      secondary: value.occupied ? "占用状态 ON" : `起始 ${formatTimestamp(value.unoccupied_since)}`,
    };
  }
  if (device.type === "contact") {
    return { primary: value.open ? "窗户打开" : "窗户关闭", secondary: value.open ? "状态 ON" : "状态 OFF" };
  }
  if (device.type === "illuminance") {
    return { primary: `${Number(value.illuminance || 0)} lux`, secondary: "实时环境照度" };
  }
  const mode = value.mode === "cool" ? "制冷" : value.mode === "heat" ? "制热" : "已关闭";
  return { primary: mode, secondary: `设定 ${Number(value.temperature || 24)}°C · 室温 ${Number(value.current_temperature || 0)}°C` };
}

function faultOptions(selected, sensor = false) {
  const options = sensor ? [
    ["none", "正常"],
    ["offline", "离线"],
  ] : [
    ["none", "正常"],
    ["offline", "离线"],
    ["reject", "拒绝命令"],
    ["delay", "响应延迟"],
    ["ack_without_state_change", "ACK 不变"],
    ["invalid_state", "无效状态"],
  ];
  return options.map(([value, label]) => `<option value="${value}" ${selected === value ? "selected" : ""}>${label}</option>`).join("");
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

async function loadConversation() {
  try {
    renderConversation(await api("/api/chat"));
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadAll() {
  await Promise.all([loadStatus(), loadDevices(), loadConversation()]);
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
  if (view === "assistant") loadConversation();
}

async function sendChat(textValue) {
  if (state.chatBusy) return;
  const input = $("#chat-input");
  const textValueNormalized = textValue.trim();
  if (!textValueNormalized) return;
  setChatBusy(true);
  $("#chat-thread").insertAdjacentHTML("beforeend", `
    <div class="chat-thinking chat-pending-local">
      <i data-lucide="loader-circle"></i><span>正在读取设备上下文</span>
    </div>`);
  refreshIcons();
  try {
    const payload = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ text: textValueNormalized }),
    });
    renderConversation(payload);
    input.value = "";
    await Promise.all([loadStatus(), loadDevices(), loadEvents()]);
  } catch (error) {
    $(".chat-pending-local")?.remove();
    toast(error.message, true);
  } finally {
    setChatBusy(false);
    input.focus();
  }
}

async function chatPlanAction(path) {
  if (state.chatBusy) return;
  setChatBusy(true);
  try {
    const payload = await api(path, { method: "POST", body: "{}" });
    renderConversation(payload);
    await Promise.all([loadStatus(), loadDevices(), loadEvents()]);
  } catch (error) {
    toast(error.message, true);
  } finally {
    setChatBusy(false);
  }
}

async function clearConversation() {
  if (state.chatBusy) return;
  setChatBusy(true);
  try {
    const payload = await api("/api/chat/clear", { method: "POST", body: "{}" });
    state.lastMessageId = null;
    renderConversation(payload);
  } catch (error) {
    toast(error.message, true);
  } finally {
    setChatBusy(false);
  }
}

function markRuleDraftDirty() {
  state.ruleDraftDirty = true;
  state.ruleDraftRevision += 1;
  $("#save-rule").classList.add("attention");
}

async function saveRuleBinding() {
  const submittedRevision = state.ruleDraftRevision;
  const payload = await post(
    "/api/proactive/config",
    {
      enabled: $("#rule-enabled").checked,
      room: $("#rule-room").value,
      presence_device_id: $("#rule-presence-device").value,
      contact_device_id: $("#rule-contact-device").value,
      climate_device_id: $("#rule-climate-device").value,
    },
    "主动规则设备绑定已保存",
  );
  if (!payload || state.ruleDraftRevision !== submittedRevision) return;
  state.ruleDraftDirty = false;
  if (state.proactive) renderRuleBinding(state.proactive);
}

function markSceneDraftDirty() {
  state.sceneDraftDirty = true;
  state.sceneDraftRevision += 1;
}

function markNightDraftDirty() {
  state.nightDraftDirty = true;
  state.nightDraftRevision += 1;
  $("#save-night-safety").classList.add("attention");
}

function nightSafetyConfig() {
  return {
    enabled: $("#night-enabled").checked,
    member_name: $("#night-member-name").value.trim(),
    origin_room: $("#night-origin-room").value,
    presence_device_id: $("#night-presence-device").value,
    illuminance_device_id: $("#night-illuminance-device").value,
    path_light_device_ids: [...state.nightPathOrder],
    brightness: Number($("#night-brightness-range").value),
    max_illuminance: Number($("#night-threshold-range").value),
  };
}

function setNightBusy(busy) {
  state.nightBusy = busy;
  [
    "#night-enabled",
    "#night-member-name",
    "#night-origin-room",
    "#night-presence-device",
    "#night-illuminance-device",
    "#night-brightness-range",
    "#night-threshold-range",
    "#night-lux-range",
    "#night-manual-light",
    "#save-night-safety",
    "#prepare-night-safety",
    "#run-night-safety",
  ].forEach((selector) => {
    $(selector).disabled = busy;
  });
  $$('[data-night-path-id]').forEach((input) => {
    input.disabled = busy;
  });
}

async function persistNightSafety(successMessage = "夜间安全路径已保存") {
  const submittedRevision = state.nightDraftRevision;
  try {
    const payload = await api("/api/night-safety/config", {
      method: "POST",
      body: JSON.stringify(nightSafetyConfig()),
    });
    if (payload.status) renderStatus(payload.status);
    if (state.nightDraftRevision === submittedRevision) {
      state.nightDraftDirty = false;
      $("#save-night-safety").classList.remove("attention");
      if (payload.night_safety) renderNightSafety(payload.night_safety);
    }
    if (successMessage) toast(successMessage);
    return payload;
  } catch (error) {
    toast(error.message, true);
    return null;
  }
}

async function saveNightSafety() {
  if (state.nightBusy) return;
  setNightBusy(true);
  try {
    await persistNightSafety();
  } finally {
    setNightBusy(false);
  }
}

async function prepareNightSafety() {
  if (state.nightBusy) return;
  setNightBusy(true);
  try {
    if (state.nightDraftDirty && !await persistNightSafety(null)) return;
    const payload = await api("/api/night-safety/prepare", {
      method: "POST",
      body: JSON.stringify({
        manual_device_id: $("#night-manual-light").value,
        illuminance: Number($("#night-lux-range").value),
      }),
    });
    if (payload.status) renderStatus(payload.status);
    if (payload.devices) renderInventory(payload);
    if (payload.night_safety) renderNightSafety(payload.night_safety);
    await loadEvents();
    toast("夜间测试状态已准备，并完成设备回读");
  } catch (error) {
    toast(error.message, true);
  } finally {
    setNightBusy(false);
  }
}

async function runNightSafety() {
  if (state.nightBusy) return;
  setNightBusy(true);
  try {
    if (state.nightDraftDirty && !await persistNightSafety(null)) return;
    const payload = await api("/api/night-safety/trigger", {
      method: "POST",
      body: "{}",
    });
    if (payload.status) renderStatus(payload.status);
    if (payload.response) renderNightResult(payload.response);
    await Promise.all([loadDevices(), loadEvents()]);
    toast(payload.response?.status === "executed" ? "起身事件已触发，路径执行并完成回读" : "起身事件已分析，设备保持现状");
  } catch (error) {
    toast(error.message, true);
  } finally {
    setNightBusy(false);
  }
}

async function writeTestScene() {
  const submittedRevision = state.sceneDraftRevision;
  const payload = await post(
    "/api/scene/reset",
    {
      occupied: $("#test-occupied").checked,
      window_open: $("#test-window-open").checked,
      climate_mode: $("#test-climate-mode").value,
      unoccupied_minutes: Number($("#minutes-range").value),
    },
    "测试状态已写入设备侧并完成回读",
  );
  if (!payload || state.sceneDraftRevision !== submittedRevision) return;
  state.sceneDraftDirty = false;
  if (state.proactive) renderProactive(state.proactive);
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
  if (action === "sensor-toggle") {
    const body = device.type === "presence"
      ? { occupied: !Boolean(device.state?.occupied) }
      : { open: !Boolean(device.state?.open) };
    await controlDevice(device, body, "传感器状态已上报");
    await loadStatus();
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
  if (event.target.matches("[data-device-illuminance]")) {
    await controlDevice(device, { illuminance: Number(event.target.value) }, "照度已上报");
    await loadStatus();
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
        : type === "curtain"
          ? { position: power === "on" ? 100 : 0, target_position: power === "on" ? 100 : 0, status: power === "on" ? "open" : "closed" }
          : type === "presence"
            ? { occupied: power === "on" }
            : type === "contact"
              ? { open: power === "on" }
              : { illuminance: power === "on" ? 100 : 8 };
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
  $("#clear-chat").addEventListener("click", clearConversation);
  $("#chat-form").addEventListener("submit", (event) => {
    event.preventDefault();
    sendChat($("#chat-input").value);
  });
  $("#chat-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $("#chat-form").requestSubmit();
    }
  });
  $$(".prompt-strip [data-chat-prompt]").forEach((button) => {
    button.addEventListener("click", () => sendChat(button.dataset.chatPrompt));
  });
  $("#confirm-chat-plan").addEventListener("click", () => chatPlanAction("/api/chat/confirm"));
  $("#cancel-chat-plan").addEventListener("click", () => chatPlanAction("/api/chat/cancel"));
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
    markSceneDraftDirty();
    $("#minutes-output").textContent = event.target.value;
  });
  $("#rule-room").addEventListener("change", (event) => {
    populateRuleDeviceSelects(event.target.value, {});
    markRuleDraftDirty();
  });
  ["#rule-enabled", "#rule-presence-device", "#rule-contact-device", "#rule-climate-device"].forEach((selector) => {
    $(selector).addEventListener("change", markRuleDraftDirty);
  });
  $("#save-rule").addEventListener("click", saveRuleBinding);
  ["#test-occupied", "#test-window-open", "#test-climate-mode"].forEach((selector) => {
    $(selector).addEventListener("change", markSceneDraftDirty);
  });
  $("#reset-scene").addEventListener("click", writeTestScene);
  ["#night-enabled", "#night-origin-room", "#night-presence-device", "#night-illuminance-device"].forEach((selector) => {
    $(selector).addEventListener("change", markNightDraftDirty);
  });
  $("#night-member-name").addEventListener("input", markNightDraftDirty);
  [["#night-brightness-range", "#night-brightness-output"], ["#night-threshold-range", "#night-threshold-output"]]
    .forEach(([rangeSelector, outputSelector]) => {
      $(rangeSelector).addEventListener("input", (event) => {
        $(outputSelector).textContent = event.target.value;
        markNightDraftDirty();
      });
    });
  $("#night-lux-range").addEventListener("input", (event) => {
    $("#night-lux-output").textContent = event.target.value;
  });
  $("#night-path-lights").addEventListener("change", (event) => {
    const input = event.target.closest("[data-night-path-id]");
    if (!input) return;
    const deviceId = input.dataset.nightPathId;
    state.nightPathOrder = state.nightPathOrder.filter((item) => item !== deviceId);
    if (input.checked) state.nightPathOrder.push(deviceId);
    markNightDraftDirty();
    syncNightPathUi();
  });
  $("#save-night-safety").addEventListener("click", saveNightSafety);
  $("#prepare-night-safety").addEventListener("click", prepareNightSafety);
  $("#run-night-safety").addEventListener("click", runNightSafety);
  $("#observe-scene").addEventListener("click", () => post(
    "/api/observe",
    {},
    "已读取当前设备状态并完成主动分析",
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
