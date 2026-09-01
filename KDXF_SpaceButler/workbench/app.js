const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const state = {
  status: null,
  devices: [],
  deviceDiagnostics: null,
  rooms: [],
  dashboard: null,
  favoriteDeviceIds: null,
  roomFilter: "all",
  view: "home",
  floorSelectedId: null,
  floorRoomFocus: "all",
  energyHours: 6,
  deviceBusyIds: new Set(),
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
  floorLongPressTimer: null,
  floorLongPressTriggered: false,
};
let voiceInput = null;

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
  const proactiveCopy = $("#dashboard-proactive-copy");
  if (proactiveCopy && payload.proactive) {
    const passed = (payload.proactive.conditions || []).filter((item) => item.met).length;
    const total = (payload.proactive.conditions || []).length;
    const targetName = payload.proactive.sources?.climate?.name || "执行设备";
    $("#dashboard-proactive-title").textContent = payload.proactive.rule_name || "主动服务建议";
    proactiveCopy.textContent = payload.proactive.complete
      ? `已满足 ${passed}/${total} 条件，建议关闭${targetName}`
      : `当前满足 ${passed}/${total} 条件，持续守护中`;
    $("#advice-count").textContent = payload.proactive.trigger_ready ? "1 条主动建议" : "暂无待处理建议";
    const climate = payload.proactive.sources?.climate;
    const climateState = climate?.state || {};
    $("#indoor-temperature").textContent = Number.isFinite(Number(climateState.current_temperature))
      ? `${Number(climateState.current_temperature)}°C`
      : "--°C";
  }
  if (payload.night_safety) {
    // 首页夜间守护摘要读真实配置，不写死开启状态。
    const config = payload.night_safety.config || {};
    const room = (payload.night_safety.available_rooms || []).find((item) => item.room === config.origin_room);
    const nameNode = $("#night-service-name");
    const stateNode = $("#night-service-state");
    if (nameNode) {
      nameNode.textContent = `${config.member_name || "家庭成员"}夜间起身守护`;
    }
    if (stateNode) {
      const enabled = config.enabled !== false;
      const where = room?.name || config.origin_room || "";
      stateNode.textContent = enabled ? `已开启${where ? ` · ${where}` : ""}` : "已停用";
    }
  }
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
  const composerBusy = busy || Boolean(voiceInput?.active);
  $("#chat-input").disabled = composerBusy;
  $("#chat-send").disabled = composerBusy;
  $("#clear-chat").disabled = composerBusy;
  $("#voice-hold").disabled = busy;
  $$(".prompt-strip [data-chat-prompt]").forEach((button) => {
    button.disabled = composerBusy;
  });
  $("#confirm-chat-plan").disabled = composerBusy;
  $("#cancel-chat-plan").disabled = composerBusy;
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

function deviceIsActive(device) {
  return Boolean(device.ui?.active);
}

function dashboardDeviceName(device) {
  return device.ui?.name || device.name;
}

function dashboardDeviceState(device) {
  return device.ui?.state_label || (device.online ? "状态未知" : "设备离线");
}

function dashboardDeviceTone(device) {
  if (!device.online) return "offline";
  if (device.ui?.warning) return "warning";
  if (device.ui?.tone === "cyan") return "climate";
  return deviceIsActive(device) ? "active" : "idle";
}

function controlPayloadAttribute(payload) {
  return escapeHtml(JSON.stringify(payload || {}));
}

function renderFavoriteDevices() {
  const grid = $("#favorite-devices");
  if (!grid) return;
  const configuredIds = state.favoriteDeviceIds || state.dashboard?.favorite_device_ids || [];
  const devices = configuredIds
    .map((deviceId) => state.devices.find((item) => item.device_id === deviceId))
    .filter(Boolean);
  grid.innerHTML = devices.map((device) => renderHaTile(device)).join("");
}

function roomTemperature(devices) {
  const sensor = devices.find((device) => device.type === "temperature");
  if (Number.isFinite(Number(sensor?.state?.temperature))) return Number(sensor.state.temperature);
  const climate = devices.find((device) => device.type === "climate");
  return Number.isFinite(Number(climate?.state?.current_temperature)) ? Number(climate.state.current_temperature) : null;
}

function renderRoomStatus() {
  const grid = $("#room-status-grid");
  if (!grid) return;
  const priority = (room) => {
    const key = String(room.room || "").toLowerCase();
    if (key.includes("living")) return 0;
    if (key.includes("bed")) return 1;
    return 2;
  };
  const rooms = [...state.rooms].sort((left, right) => priority(left) - priority(right)).slice(0, 2);
  if (!rooms.length) {
    grid.innerHTML = `<div class="empty-state compact"><i data-lucide="house"></i><span>暂无空间状态</span></div>`;
    return;
  }
  grid.innerHTML = rooms.map((room, index) => {
    const devices = state.devices.filter((device) => device.room === room.room);
    const presence = devices.find((device) => device.type === "presence");
    const contact = devices.find((device) => device.type === "contact");
    const illuminance = devices.find((device) => device.type === "illuminance");
    const occupied = presence ? Boolean(presence.state?.occupied) : null;
    const temperature = roomTemperature(devices);
    const lux = Number.isFinite(Number(illuminance?.state?.illuminance))
      ? Number(illuminance.state.illuminance)
      : Number.isFinite(Number(illuminance?.state?.value)) ? Number(illuminance.state.value) : null;
    const roomKey = String(room.room || "").toLowerCase();
    const bedroom = roomKey.includes("bed");
    const stateLines = [];
    if (contact) stateLines.push({ icon: "panel-top-open", value: contact.state?.open ? "窗户打开" : "窗户关闭", tone: "cyan" });
    if (temperature !== null) stateLines.push({ icon: "thermometer", value: `${Number(temperature.toFixed(1))}°C`, tone: "cyan" });
    if (lux !== null) stateLines.push({ icon: "lightbulb", value: `${Math.round(lux)} lux`, tone: "amber" });
    if (Number(room.power_w || 0) > 0 || stateLines.length < 2) stateLines.push({ icon: "zap", value: `${Math.round(Number(room.power_w || 0))} W`, tone: "amber" });
    return `
      <button class="room-status-card" data-room-card="${escapeHtml(room.room)}" type="button">
        <span class="room-status-icon ${bedroom ? "bedroom" : "living"}"><i data-lucide="${bedroom ? "bed-double" : "sofa"}"></i></span>
        <span class="room-status-title"><strong>${escapeHtml(room.name)}</strong><small>${occupied === null ? `${room.online}/${room.devices} 台在线` : occupied ? "有人" : "无人"}</small></span>
        <i class="room-card-chevron" data-lucide="chevron-right"></i>
        <span class="room-signal-list">
          ${stateLines.slice(0, 3).map((item) => `<span><i class="${item.tone}" data-lucide="${item.icon}"></i>${escapeHtml(item.value)}</span>`).join("")}
        </span>
        <span class="room-mini-plan ${bedroom ? "bedroom" : "living"}" aria-hidden="true">
          <span class="plan-wall"></span><span class="plan-rug"></span><span class="plan-plant"></span>
          <i data-lucide="${bedroom ? "bed-double" : "sofa"}"></i>
        </span>
      </button>`;
  }).join("");
}

function floorplanPosition(device, index) {
  return state.dashboard?.device_positions?.[device.device_id] || {
    left: 12 + (index % 4) * 24,
    top: 28 + Math.floor(index / 4) * 30,
  };
}

function renderFloorplanRoomTabs() {
  const tabs = $("#floorplan-room-tabs");
  if (!tabs) return;
  const options = [{ room: "all", name: "全屋" }, ...state.rooms];
  if (!options.some((item) => item.room === state.floorRoomFocus)) state.floorRoomFocus = "all";
  tabs.innerHTML = options.map((room) => `
    <button type="button" role="tab" data-floor-room="${escapeHtml(room.room)}"
      aria-selected="${state.floorRoomFocus === room.room}"
      class="${state.floorRoomFocus === room.room ? "active" : ""}">${escapeHtml(room.name)}</button>`).join("");
}

function renderFloorplanRooms() {
  const roomLayer = $("#floorplan-rooms");
  if (!roomLayer) return;
  const rooms = state.dashboard?.rooms || [];
  roomLayer.innerHTML = rooms.map((room) => `
    <section class="floorplan-space ${state.floorRoomFocus !== "all" && state.floorRoomFocus !== room.room ? "dimmed" : ""}"
      data-theme="${Number(room.theme || 0)}" data-room-id="${escapeHtml(room.room)}"
      style="left:${Number(room.left || 0)}%;top:${Number(room.top || 0)}%;width:${Number(room.width || 100)}%;height:${Number(room.height || 100)}%"
      aria-label="${escapeHtml(room.name)}">
      <div class="room-title"><strong>${escapeHtml(room.name)}</strong><span>${Number(room.online || 0)}/${Number(room.devices || 0)} 台在线 · ${Math.round(Number(room.power_w || 0))} W</span></div>
    </section>`).join("");
}

function renderFloorControlPanel(device) {
  const panel = $("#floor-device-panel");
  if (!panel) return;
  if (!device) {
    panel.hidden = true;
    panel.innerHTML = "";
    return;
  }
  panel.hidden = false;
  panel.dataset.deviceId = device.device_id;
  panel.innerHTML = `
    <div class="floor-control-heading">
      <div><strong>${escapeHtml(dashboardDeviceName(device))}</strong><span>${escapeHtml(dashboardDeviceState(device))}</span></div>
      <button class="icon-button" data-floor-close title="关闭控制面板"><i data-lucide="x"></i></button>
    </div>
    ${renderDeviceControls(device)}
    <button class="floor-detail-link" data-floor-detail>更多详情 <i data-lucide="arrow-right"></i></button>`;
}

function renderFloorplan() {
  const layer = $("#floorplan-devices");
  if (!layer) return;
  renderFloorplanRooms();
  if (state.floorSelectedId && !state.devices.some((item) => item.device_id === state.floorSelectedId)) {
    state.floorSelectedId = null;
  }
  layer.innerHTML = state.devices.map((device, index) => {
    const icon = device.ui?.icon || "cpu";
    const floorVisual = device.ui?.floorplan || {};
    const floorShape = floorVisual.shape || "device";
    const floorReadout = floorVisual.readout || "";
    const position = floorplanPosition(device, index);
    const selected = state.floorSelectedId === device.device_id;
    const dimmed = state.floorRoomFocus !== "all" && state.floorRoomFocus !== device.room;
    const busy = state.deviceBusyIds.has(device.device_id);
    return `
      <button class="floor-device ${dashboardDeviceTone(device)} ${selected ? "selected" : ""} ${dimmed ? "dimmed" : ""} ${busy ? "device-busy" : ""}"
        style="left:${position.left}%;top:${position.top}%" data-floor-device-id="${escapeHtml(device.device_id)}"
        data-floor-shape="${escapeHtml(floorShape)}"
        aria-label="${escapeHtml(dashboardDeviceName(device))}，${escapeHtml(dashboardDeviceState(device))}" aria-pressed="${selected}" aria-busy="${busy}">
        <span class="floor-device-visual" aria-hidden="true">
          <span class="floor-device-icon"><i data-lucide="${escapeHtml(icon)}"></i></span>
          ${floorReadout ? `<strong class="floor-device-readout">${escapeHtml(floorReadout)}</strong>` : ""}
        </span>
        <span class="floor-device-copy"><strong>${escapeHtml(dashboardDeviceName(device))}</strong><span>${escapeHtml(dashboardDeviceState(device))}</span></span>
      </button>`;
  }).join("");
  const selected = state.devices.find((item) => item.device_id === state.floorSelectedId);
  renderFloorControlPanel(selected);
}

function renderSpatialDashboard() {
  renderFavoriteDevices();
  renderRoomStatus();
  renderFloorplanRoomTabs();
  renderFloorplan();
  refreshIcons();
}

function formatPowerLabel(value) {
  return value >= 1000 ? `${(value / 1000).toFixed(1)} kW` : `${Math.round(value)} W`;
}

// 能耗曲线来自 Home Assistant recorder 真实历史（/api/energy-history），
// 不再用前端采样或按当前功率外推。功率本身是额定模型估算，界面明确标注。
async function loadEnergyHistory() {
  const plot = $("#energy-plot");
  if (!plot) return;
  try {
    const payload = await api(`/api/energy-history?hours=${state.energyHours}`);
    renderEnergyHistory(payload);
  } catch (error) {
    plot.innerHTML = `<div class="ha-energy-empty">${escapeHtml(error.message)}</div>`;
    const note = $("#energy-model-note span");
    if (note) note.textContent = "历史读取失败";
  }
}

function renderEnergyHistory(payload) {
  const plot = $("#energy-plot");
  if (!plot) return;
  const points = payload.points || [];
  $("#energy-period-kwh").textContent = text(payload.period_kwh, "--");
  const note = $("#energy-model-note span");
  if (note) note.textContent = `${payload.model_label} · 近 ${payload.hours} 小时`;
  $("#energy-devices").innerHTML = (payload.tracked_devices || []).map((device) => `
    <span class="ha-energy-device ${device.recorded ? "" : "missing"}">
      ${escapeHtml(device.name)}${device.recorded ? "" : " · 无历史记录"}
    </span>`).join("");

  if (points.length < 2) {
    plot.innerHTML = `<div class="ha-energy-empty">该区间没有可用的历史记录</div>`;
    return;
  }
  const values = points.map((point) => Number(point.power_w) || 0);
  const maximum = Math.max(100, Math.ceil(Math.max(...values) * 1.15 / 100) * 100);
  const left = 44;
  const right = 512;
  const top = 12;
  const bottom = 178;
  const coords = points.map((point, index) => ({
    x: Number((left + index * (right - left) / (points.length - 1)).toFixed(1)),
    y: Number((bottom - (Number(point.power_w) || 0) / maximum * (bottom - top)).toFixed(1)),
  }));
  const line = coords.map((point) => `${point.x},${point.y}`).join(" ");
  const area = `M${coords[0].x},${bottom} L${line.split(" ").join(" L")} L${coords.at(-1).x},${bottom} Z`;
  plot.innerHTML = `
    <svg viewBox="0 0 524 196" role="img" aria-label="按额定功率模型换算的历史功率曲线">
      <defs>
        <linearGradient id="ha-energy-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#03a9f4" stop-opacity=".28"/>
          <stop offset="1" stop-color="#03a9f4" stop-opacity=".02"/>
        </linearGradient>
      </defs>
      <g stroke="#eceff4" stroke-width="1">
        ${[top, top + (bottom - top) / 3, top + (bottom - top) * 2 / 3, bottom].map((y) => `<line x1="${left}" y1="${y}" x2="${right}" y2="${y}"/>`).join("")}
      </g>
      <g fill="#6b7280" font-size="11">
        <text x="2" y="${top + 4}">${formatPowerLabel(maximum)}</text>
        <text x="2" y="${top + (bottom - top) / 3 + 4}">${formatPowerLabel(maximum * 2 / 3)}</text>
        <text x="2" y="${top + (bottom - top) * 2 / 3 + 4}">${formatPowerLabel(maximum / 3)}</text>
        <text x="2" y="${bottom + 4}">0 W</text>
      </g>
      <path d="${area}" fill="url(#ha-energy-fill)"/>
      <polyline points="${line}" fill="none" stroke="#03a9f4" stroke-width="2" stroke-linejoin="round"/>
      <circle cx="${coords.at(-1).x}" cy="${coords.at(-1).y}" r="4" fill="#03a9f4"/>
    </svg>`;
  const time = (value) => new Date(value).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  $("#energy-chart-start").textContent = time(points[0].at);
  $("#energy-chart-end").textContent = time(points.at(-1).at);
}

// 问候语按真实时间段 + 真实设备状态生成，不写死"一切正常"。
function renderHomeHeadline(online) {
  const node = $("#home-headline");
  if (!node) return;
  const hour = new Date().getHours();
  const greeting = hour < 6 ? "凌晨好" : hour < 11 ? "早上好" : hour < 14 ? "中午好" : hour < 18 ? "下午好" : "晚上好";
  const total = state.devices.length;
  if (!total) {
    node.textContent = `${greeting}，尚未发现已接入设备`;
    return;
  }
  const offline = total - online;
  const warnings = state.devices.filter((device) => device.online && device.ui?.warning);
  if (offline) {
    node.textContent = `${greeting}，${offline} 台设备离线需要检查`;
    return;
  }
  if (warnings.length) {
    node.textContent = `${greeting}，${warnings.map((device) => device.ui?.name || device.name).join("、")}需要关注`;
    return;
  }
  node.textContent = `${greeting}，${total} 台设备均在线运行`;
}

function renderSourceCount() {
  const node = $("#sidebar-source-count");
  if (!node) return;
  const esphome = state.devices.filter((device) => device.source === "esphome").length;
  const mqtt = state.devices.filter((device) => device.source === "mqtt").length;
  node.textContent = `${esphome + mqtt}`;
  node.title = `已接入 ${esphome} 台 ESPHome 设备、${mqtt} 台 MQTT 设备`;
}

function renderInventory(payload) {
  state.devices = payload.devices || [];
  state.deviceDiagnostics = payload.diagnostics || null;
  state.rooms = payload.rooms || [];
  state.dashboard = payload.dashboard || { favorite_device_ids: [], rooms: [], device_positions: {}, energy: {} };
  const availableIds = new Set(state.devices.map((item) => item.device_id));
  if (state.favoriteDeviceIds === null) {
    try {
      const saved = JSON.parse(localStorage.getItem("spacebutler.favoriteDeviceIds") || "null");
      state.favoriteDeviceIds = Array.isArray(saved) ? saved : [...(state.dashboard.favorite_device_ids || [])];
    } catch {
      state.favoriteDeviceIds = [...(state.dashboard.favorite_device_ids || [])];
    }
  }
  state.favoriteDeviceIds = state.favoriteDeviceIds.filter((deviceId) => availableIds.has(deviceId));
  const totalPower = state.rooms.reduce((sum, room) => sum + Number(room.power_w || 0), 0);
  const online = state.devices.filter((device) => device.online).length;
  $("#room-count").textContent = state.rooms.length;
  $("#device-count").textContent = state.devices.length;
  $("#online-count").textContent = `${online}/${state.devices.length}`;
  $("#total-power").textContent = `${Math.round(totalPower)} W`;
  $("#energy-online-devices").textContent = `${online}/${state.devices.length}`;
  const missingCount = state.deviceDiagnostics?.missing_devices?.length || 0;
  $("#household-device-status").textContent = !state.devices.length
    ? "尚未匹配到 HA 设备"
    : missingCount
      ? `${online} 台在线 · ${missingCount} 项待匹配`
      : online === state.devices.length
        ? `全屋 ${online} 台设备在线`
        : `${state.devices.length - online} 台设备需关注`;
  const livingDevices = state.devices.filter((device) => device.room === "living_room");
  const liveTemperature = roomTemperature(livingDevices.length ? livingDevices : state.devices);
  $("#indoor-temperature").textContent = liveTemperature !== null ? `${Number(liveTemperature.toFixed(1))}°C` : "--°C";
  renderHomeHeadline(online);
  renderSourceCount();
  $("#inventory-summary").textContent = `${state.devices.length} 台设备 · ${state.rooms.length} 个空间`;
  renderRoomTabs();
  const grid = $("#device-grid");
  if (!grid.contains(document.activeElement)) renderDeviceGrid();
  const floorplan = $("#floorplan-stage");
  if (!floorplan?.contains(document.activeElement)) renderSpatialDashboard();
  if (typeof wfRefreshDevices === "function") wfRefreshDevices();
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
    const unmatched = !state.devices.length;
    const haCount = state.deviceDiagnostics?.ha_entity_count;
    grid.innerHTML = `
      <div class="empty-inventory">
        <i data-lucide="package-open"></i>
        <strong>${unmatched ? "尚未匹配到 HA 设备" : "这个空间还没有设备"}</strong>
        <p>${unmatched && Number.isFinite(haCount)
          ? `HA 返回 ${haCount} 个实体，但尚未匹配到唯一的客厅设备。请检查实体名称或配置绑定。`
          : "设备需先接入 Home Assistant，再由工作台读取。"}</p>
        <a href="/api/devices/diagnostics" target="_blank" rel="noopener noreferrer">查看设备匹配诊断</a>
        <button class="secondary" data-open-esphome-nodes><i data-lucide="radio-tower"></i><span>查看接入状态</span></button>
      </div>`;
    refreshIcons();
    return;
  }
  // 按空间分组渲染 HA sections；单空间筛选时不重复显示分组标题。
  if (state.roomFilter !== "all") {
    grid.innerHTML = `<div class="ha-grid">${devices.map(renderDeviceDetailTile).join("")}</div>`;
    refreshIcons();
    return;
  }
  const byRoom = new Map();
  for (const device of devices) {
    const key = device.room || "home";
    if (!byRoom.has(key)) byRoom.set(key, { name: device.room_name || key, items: [] });
    byRoom.get(key).items.push(device);
  }
  grid.innerHTML = `<div class="ha-sections">${[...byRoom.values()].map((group) => `
    <section>
      <h3 class="ha-section-heading">
        <i data-lucide="layout-grid"></i>
        <span>${escapeHtml(group.name)}</span>
        <span class="ha-section-count">${group.items.length} 台</span>
      </h3>
      <div class="ha-grid">${group.items.map(renderDeviceDetailTile).join("")}</div>
    </section>`).join("")}</div>`;
  refreshIcons();
}

// 设备页的 tile 额外显示实体 id 和故障注入选项（若后端提供）。
function renderDeviceDetailTile(device) {
  const faultOptions = device.ui?.fault_options || [];
  const extras = `
    <div class="ha-features">
      <div class="ha-feature-readonly"><i data-lucide="tag"></i><span>${escapeHtml(device.entity_id)}</span></div>
      ${faultOptions.length && !device.readonly ? `
        <label class="fault-control">
          <span>故障模式</span>
          <select data-device-fault>${faultOptions.map((option) => `
            <option value="${escapeHtml(option.value)}" ${device.fault_mode === option.value ? "selected" : ""}>${escapeHtml(option.label)}</option>`).join("")}
          </select>
        </label>` : ""}
    </div>`;
  return renderHaTile(device).replace("</article>", `${extras}</article>`);
}

function renderDeviceControls(device) {
  const controls = device.ui?.controls || [];
  if (!controls.length) return `<div class="readonly-control"><i data-lucide="eye"></i><span>只读设备</span></div>`;
  return `<div class="configured-controls">${controls.map(renderControlDescriptor).join("")}</div>`;
}

function renderControlDescriptor(control) {
  const payload = controlPayloadAttribute(control.payload);
  if (control.kind === "toggle") {
    return `
      <div class="switch-control ${control.test_control ? "test-control" : ""}">
        <span>${escapeHtml(control.status || control.label)}</span>
        <button class="power-button ${control.active ? "on" : ""}" data-control-payload="${payload}" title="${escapeHtml(control.label)}">
          <i data-lucide="power"></i>
        </button>
      </div>`;
  }
  if (control.kind === "range") {
    return `
      <label class="curtain-control ${control.test_control ? "test-control" : ""}">
        <span>${escapeHtml(control.label)}</span>
        <input type="range" min="${Number(control.min)}" max="${Number(control.max)}" step="${Number(control.step)}"
          value="${Number(control.value)}" data-control-range data-control-field="${escapeHtml(control.field)}" data-control-payload="${payload}">
        <output>${escapeHtml(control.value)}${escapeHtml(control.unit || "")}</output>
      </label>`;
  }
  if (control.kind === "segmented") {
    return `
      <div class="segmented configured-segmented" aria-label="${escapeHtml(control.label)}">
        ${(control.options || []).map((option) => `
          <button class="${option.active ? "active" : ""}" data-control-payload="${controlPayloadAttribute(option.payload)}">
            ${escapeHtml(option.label)}
          </button>`).join("")}
      </div>`;
  }
  if (control.kind === "stepper") {
    return `
      <div class="temperature-stepper" aria-label="${escapeHtml(control.label)}">
        <button data-control-step="-${Number(control.step)}" title="减少"><i data-lucide="minus"></i></button>
        <input type="number" min="${Number(control.min)}" max="${Number(control.max)}" step="${Number(control.step)}"
          value="${Number(control.value)}" data-control-value data-control-field="${escapeHtml(control.field)}" data-control-payload="${payload}">
        <button data-control-step="${Number(control.step)}" title="增加"><i data-lucide="plus"></i></button>
        <button class="apply-temperature" data-control-apply title="应用"><i data-lucide="check"></i></button>
      </div>`;
  }
  return "";
}

// ============================================================
// Home Assistant tile 卡片。所有控件都由后端 ui.controls 描述符驱动，
// 复用既有 data-control-* 事件管线，前端不预置任何设备行为。
// ============================================================

const HA_TONE_BY_TYPE = {
  light: "on-light",
  switch: "on-switch",
  curtain: "on-cover",
  climate: "on-cool",
  presence: "on-cover",
  contact: "on-light",
  illuminance: "on-light",
  temperature: "on-cool",
};

function haTileIconClass(device) {
  if (!device.online) return "offline";
  if (device.ui?.warning) return "warning";
  if (!device.ui?.active) return "";
  if (device.type === "climate") {
    return device.state?.mode === "heat" ? "on-heat" : "on-cool";
  }
  return HA_TONE_BY_TYPE[device.type] || "on-switch";
}

function haSourceLabel(device) {
  // 全部设备都经 ESPHome 接入，来源徽标已无信息量；改为展示品牌生态。
  return device.brand?.name || "";
}

function brandAccent(device) {
  const accent = device.brand?.accent;
  return /^#[0-9a-f]{6}$/i.test(accent || "") ? accent : "";
}

function renderHaTile(device, { showBadge = true } = {}) {
  const toggle = (device.ui?.controls || []).find((control) => control.kind === "toggle" && !control.test_control);
  const busy = state.deviceBusyIds.has(device.device_id);
  const clickable = Boolean(toggle) && device.online;
  const badge = showBadge ? haSourceLabel(device) : "";
  return `
    <article class="ha-tile" data-device-id="${escapeHtml(device.device_id)}" aria-busy="${busy}">
      <button class="ha-tile-main" ${clickable ? `data-control-payload="${controlPayloadAttribute(toggle.payload)}"` : "disabled"}
        title="${escapeHtml(clickable ? toggle.label : device.ui?.state_label || device.name)}">
        <span class="ha-tile-icon ${haTileIconClass(device)}"><i data-lucide="${escapeHtml(device.ui?.icon || "cpu")}"></i></span>
        <span class="ha-tile-copy">
          <strong>${escapeHtml(device.ui?.name || device.name)}</strong>
          <span>${escapeHtml(device.online ? device.ui?.state_label || "状态未知" : "不可用")}</span>
        </span>
        ${badge ? `<span class="ha-tile-badge is-brand"${brandAccent(device) ? ` style="--brand-accent:${brandAccent(device)}"` : ""}>${escapeHtml(badge)}</span>` : ""}
      </button>
      ${renderHaFeatures(device)}
    </article>`;
}

function renderHaFeatures(device) {
  if (!device.online) return "";
  // 只展示真实可用的控制项；测试注入类控件不进 tile。
  const controls = (device.ui?.controls || []).filter((control) => !control.test_control);
  const features = controls.map(renderHaFeature).filter(Boolean);
  if (!features.length) {
    if (!device.readonly) return "";
    return `<div class="ha-features"><div class="ha-feature-readonly"><i data-lucide="activity"></i><span>${escapeHtml(device.ui?.state_secondary || "只读传感器")}</span></div></div>`;
  }
  return `<div class="ha-features">${features.join("")}</div>`;
}

function renderHaFeature(control) {
  if (control.kind === "range") {
    const value = Number(control.value);
    const min = Number(control.min);
    const max = Number(control.max);
    const percent = max > min ? Math.round(((value - min) / (max - min)) * 100) : 0;
    const isCover = control.field === "position";
    return `
      <div class="ha-slider ${isCover ? "cover-slider" : ""}">
        <div class="ha-slider-fill" style="width: ${percent}%"></div>
        <div class="ha-slider-label">
          <i data-lucide="${isCover ? "blinds" : "sun"}"></i>
          <span>${escapeHtml(value)}${escapeHtml(control.unit || "")}</span>
        </div>
        <input type="range" min="${min}" max="${max}" step="${Number(control.step)}" value="${value}"
          aria-label="${escapeHtml(control.label)}"
          data-control-range data-control-field="${escapeHtml(control.field)}"
          data-control-payload="${controlPayloadAttribute(control.payload)}">
      </div>`;
  }
  if (control.kind === "segmented") {
    return `
      <div class="ha-modes" role="group" aria-label="${escapeHtml(control.label)}">
        ${(control.options || []).map((option) => `
          <button class="${option.active ? "active" : ""} mode-${escapeHtml(option.value)}"
            data-control-payload="${controlPayloadAttribute(option.payload)}">${escapeHtml(option.label)}</button>`).join("")}
      </div>`;
  }
  if (control.kind === "stepper") {
    const value = Number(control.value);
    const step = Number(control.step);
    const min = Number(control.min);
    const max = Number(control.max);
    const lower = { ...(control.payload || {}), [control.field]: Math.max(min, value - step) };
    const upper = { ...(control.payload || {}), [control.field]: Math.min(max, value + step) };
    return `
      <div class="ha-stepper" aria-label="${escapeHtml(control.label)}">
        <button data-control-payload="${controlPayloadAttribute(lower)}" title="降低"><i data-lucide="minus"></i></button>
        <output>${escapeHtml(value)}${escapeHtml(control.unit || "")}</output>
        <button data-control-payload="${controlPayloadAttribute(upper)}" title="升高"><i data-lucide="plus"></i></button>
      </div>`;
  }
  return "";
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

// --- 演示控制台 ---------------------------------------------------------
// 录制脚本集中在这里：每个场景有确定的提问、预期现象和旁白，避免临场回忆。
const DEMO_SCENARIOS = [
  {
    id: "planning",
    title: "AI 动态规划",
    prompt: "我准备在客厅看会电影，有点热，灯别太亮。",
    expect: "按当前状态生成「关窗帘 + 调灯 + 开空调」，逐项 HA 回读验证",
    narration: "一句自然语言，模型先把它编译成环境约束，再生成候选计划并预测未来状态评分选优。执行后每一步都回读真实设备状态。",
    action: "chat",
  },
  {
    id: "satisfied",
    title: "已满足则不动",
    prompt: "现在适合看电影了吗？",
    expect: "判定目标已满足，零设备命令",
    narration: "同样的目标再问一次，它重新观察状态后输出零动作。这是 Agent 和 Chatbot 的分界：不会因为你又说了一遍就重复执行。",
    action: "chat",
  },
  {
    id: "override",
    title: "人工纠正学习",
    prompt: "",
    expect: "时间线出现 Human Override，偏好置信度上升",
    narration: "我手动把灯调到 45%。系统把它识别为人工纠正，而不是自己的动作或环境漂移，写入偏好观测。重复几次之后，以后的规划会自动偏向这个亮度。",
    action: "override",
  },
  {
    id: "disturbance",
    title: "扰动自治重规划",
    prompt: "",
    expect: "目标偏离 → 自动重规划 → 重新执行验证",
    narration: "现在我通过 ESPHome 固件服务注入热量，制造真实的物理扰动。接下来我不说话——它自己感知到目标偏离，重新规划并执行。",
    action: "disturbance",
  },
];

function renderDemoScenarios() {
  const container = $("#demo-scenario-list");
  if (!container) return;
  container.innerHTML = DEMO_SCENARIOS.map((scenario, index) => `
    <article class="demo-scenario">
      <div class="demo-scenario-head">
        <span class="demo-scenario-index">${index + 1}</span>
        <strong>${escapeHtml(scenario.title)}</strong>
        <button class="secondary compact-button" data-demo-scenario="${escapeHtml(scenario.id)}">
          <i data-lucide="play"></i><span>${scenario.action === "chat" ? "发送提问" : "触发"}</span>
        </button>
      </div>
      ${scenario.prompt ? `<p class="demo-scenario-prompt">「${escapeHtml(scenario.prompt)}」</p>` : ""}
      <p class="demo-scenario-expect"><i data-lucide="target"></i>${escapeHtml(scenario.expect)}</p>
      <p class="demo-scenario-narration">${escapeHtml(scenario.narration)}</p>
    </article>`).join("");
  refreshIcons();
}

function renderDemoBrands(brands) {
  const container = $("#demo-brand-list");
  if (!container) return;
  if (!Array.isArray(brands) || !brands.length) {
    container.innerHTML = `<div class="empty-state compact"><span>读取设备后显示</span></div>`;
    return;
  }
  container.innerHTML = brands.map((brand) => {
    const accent = /^#[0-9a-f]{6}$/i.test(brand.accent || "") ? brand.accent : "";
    return `
      <div class="demo-brand-row"${accent ? ` style="--brand-accent:${accent}"` : ""}>
        <span class="demo-brand-dot"></span>
        <strong>${escapeHtml(brand.name || "")}</strong>
        <span class="demo-brand-proto">${escapeHtml(brand.protocol || "")}</span>
        <span class="demo-brand-count">${Number(brand.device_count) || 0} 台</span>
      </div>`;
  }).join("");
}

function renderDemoReadiness(payload) {
  const pill = $("#demo-ready-pill");
  const list = $("#demo-check-list");
  if (!pill || !list) return;
  const ready = Boolean(payload?.ready);
  pill.textContent = ready ? "可以开拍" : "尚未就绪";
  pill.className = `demo-ready-pill ${ready ? "is-ready" : "is-blocked"}`;
  const checks = Array.isArray(payload?.checks) ? payload.checks : [];
  list.innerHTML = checks.map((check) => `
    <li class="demo-check ${check.ok ? "is-ok" : "is-bad"}">
      <i data-lucide="${check.ok ? "circle-check" : "circle-alert"}"></i>
      <span>${escapeHtml(check.name || "")}</span>
      ${check.detail ? `<em>${escapeHtml(check.detail)}</em>` : ""}
    </li>`).join("");
  renderDemoBrands(payload?.brands);
  refreshIcons();
}

async function runDemoSelfCheck() {
  const button = $("#demo-selfcheck");
  if (button) button.disabled = true;
  try {
    renderDemoReadiness(await api("/api/demo/readiness"));
  } catch (error) {
    toast(error.message, true);
  } finally {
    if (button) button.disabled = false;
  }
}

async function runDemoReset() {
  const button = $("#demo-reset");
  const log = $("#demo-reset-log");
  if (button) button.disabled = true;
  if (log) {
    log.hidden = false;
    log.innerHTML = `<div class="demo-reset-line is-busy"><i data-lucide="loader-circle"></i><span>正在通过真实设备链路复位，窗帘全行程约十几秒…</span></div>`;
    refreshIcons();
  }
  try {
    const payload = await api("/api/demo/reset", { method: "POST", body: "{}" });
    if (payload.devices) renderInventory(payload);
    if (log) {
      const steps = Array.isArray(payload.reset) ? payload.reset : [];
      log.innerHTML = steps.map((step) => `
        <div class="demo-reset-line ${step.ok ? "is-ok" : "is-bad"}">
          <i data-lucide="${step.ok ? "check" : "x"}"></i>
          <span>${escapeHtml(step.step || "")}</span>
          ${step.error ? `<em>${escapeHtml(step.error)}</em>` : ""}
        </div>`).join("");
      refreshIcons();
    }
    toast(payload.ok ? "已复位到开拍状态" : "复位有步骤失败，请查看明细", !payload.ok);
    await Promise.all([loadConversation(), runDemoSelfCheck()]);
  } catch (error) {
    toast(error.message, true);
    if (log) log.innerHTML = `<div class="demo-reset-line is-bad"><i data-lucide="x"></i><span>${escapeHtml(error.message)}</span></div>`;
    refreshIcons();
  } finally {
    if (button) button.disabled = false;
  }
}

async function runDemoScenario(id) {
  const scenario = DEMO_SCENARIOS.find((item) => item.id === id);
  if (!scenario) return;
  if (scenario.action === "chat") {
    switchView("assistant");
    await sendChat(scenario.prompt);
    return;
  }
  if (scenario.action === "override") {
    switchView("agent");
    await post("/api/agent/demo-override", { brightness: 45 },
      "已通过 Home Assistant 调整主灯，等待智能体识别");
    return;
  }
  switchView("agent");
  await post("/api/agent/demo-disturbance", { temperature: 31 },
    "已注入热扰动，智能体将自动重规划");
}

async function loadAgentActivity() {
  try {
    renderAgentActivity(await api("/api/agent"));
  } catch (error) {
    // Agent 面板失败不打断其他视图。
  }
}

function renderAgentActivity(data) {
  const plannerBadge = $("#agent-planner-badge span");
  if (plannerBadge) {
    const llmAvailable = Boolean(data.llm_available);
    const mode = data.planner_mode === "llm" ? "LLM 规划" : `${data.planner_mode || "fallback"} 规划`;
    plannerBadge.textContent = `${mode} · ${llmAvailable ? "LLM 在线" : "LLM 离线"}`;
    $("#agent-planner-badge")?.classList.toggle("planner-llm", data.planner_mode === "llm");
  }

  const statusLabel = $("#agent-goal-status");
  const goalBody = $("#agent-goal-body");
  const goal = data.active_goal;
  if (statusLabel) {
    statusLabel.textContent = goal ? `${goal.title} · ${data.goal_status}` : "无活动目标";
    statusLabel.classList.toggle("idle", !goal);
    statusLabel.classList.toggle("active", data.goal_status === "ACTIVE" || data.goal_status === "REPLANNING");
    statusLabel.classList.toggle("satisfied", data.goal_status === "SATISFIED");
  }
  if (goalBody) {
    if (!goal) {
      goalBody.innerHTML = `<div class="empty-state compact"><span>说出一个目标即可启动</span></div>`;
    } else {
      const constraints = (goal.constraints || [])
        .map((item) => {
          const lower = item.lower ?? "∞";
          const upper = item.upper ?? "∞";
          return `<span class="constraint-chip">${escapeHtml(metricLabel(item.metric))} ${lower}~${upper}</span>`;
        })
        .join("");
      goalBody.innerHTML = `
        <p class="goal-title">🎬 ${escapeHtml(goal.title)}</p>
        <div class="constraint-list">${constraints}</div>
        <p class="goal-state-line">室温 <strong>${fmt(agentState(data).temperature)}</strong>°C · 灯 <strong>${fmt(agentState(data).brightness_pct)}%</strong> · 窗帘 <strong>${fmt(agentState(data).curtain_position)}%</strong> · 空调 <strong>${escapeHtml(agentState(data).ac_mode || "off")}</strong></p>`;
      const interpretation = goal.semantic_view?.intent_summary;
      if (interpretation) {
        goalBody.insertAdjacentHTML(
          "beforeend",
          `<p class="model-interpretation-line">模型理解：${escapeHtml(String(interpretation))}</p>`,
        );
      }
      const contribution = data.llm_contribution;
      if (contribution) {
        goalBody.insertAdjacentHTML(
          "beforeend",
          `<p class="llm-contribution-line">模型参与度：目标理解 ${contribution.goal_compiled_by_llm} · 候选 ${contribution.llm_candidates_valid}/${contribution.llm_candidates_generated} · 选中模型候选 ${contribution.selected_llm_candidate} · 评估 ${contribution.goal_evaluation_by_llm} · 重规划 ${contribution.replan_decision_by_llm}</p>`,
        );
      }
    }
  }

  const candidatesBody = $("#agent-candidates-body");
  if (candidatesBody) {
    const outcome = data.last_outcome;
    if (!outcome || !outcome.candidates || !outcome.candidates.length) {
      candidatesBody.innerHTML = `<div class="empty-state compact"><span>等待规划</span></div>`;
    } else {
      const rows = outcome.candidates
        .map((candidate) => {
          const scores = candidate.scores || {};
          const actions = (candidate.actions || [])
            .map((action) => `<li>${escapeHtml(action.device_name || action.device_id)} · ${escapeHtml(actionLabel(action))}${action.verified === true ? " ✓" : action.verified === false ? " ✗" : ""}</li>`)
            .join("");
          const rejected = (candidate.label || "").includes("拒绝");
          const sourceBadge = candidate.source === "llm"
            ? `<span class="source-tag source-llm">LLM</span>`
            : `<span class="source-tag source-fallback">fallback</span>`;
          return `
          <div class="candidate-card ${candidate.selected ? "selected" : ""} ${rejected ? "rejected" : ""}">
            <div class="candidate-head">
              <strong>${escapeHtml(candidate.label)} ${sourceBadge}</strong>
              <span class="candidate-score">${candidate.selected ? "★ " : ""}${candidate.total_score ?? "--"}</span>
            </div>
            ${candidate.summary ? `<p class="candidate-summary">${escapeHtml(candidate.summary)}</p>` : ""}
            <div class="score-chips">
              <span>目标 ${(scores.goal_satisfaction ?? 0).toFixed(2)}</span>
              <span>舒适 ${(scores.comfort ?? 0).toFixed(2)}</span>
              <span>偏好 ${(scores.preference_match ?? 0).toFixed(2)}</span>
              <span>能耗 ${(scores.energy_cost ?? 0).toFixed(2)}</span>
            </div>
            ${actions ? `<ul class="candidate-actions">${actions}</ul>` : ""}
            <p class="candidate-prediction">预测 5 分钟后 ${fmt(candidate.predicted?.temperature)}°C（${escapeHtml(candidate.predicted?.temperature_trend || "-")}）</p>
          </div>`;
        })
        .join("");
      const errorLine = data.last_prediction?.prediction_error !== undefined && data.last_prediction?.prediction_error !== null
        ? `<p class="prediction-error-line">预测误差（预测 vs 回读）：<strong>${data.last_prediction.prediction_error}°C</strong></p>`
        : "";
      candidatesBody.innerHTML = `${rows}${errorLine}<p class="selection-reason">${escapeHtml(outcome.reason || "")}</p>`;
    }
  }

  const feedbackBody = $("#agent-feedback-body");
  if (feedbackBody) {
    const preference = data.preference;
    const overrides = (data.timeline || []).filter((event) => event.kind === "human_override").slice(-2);
    if (!preference && !overrides.length) {
      feedbackBody.innerHTML = `<div class="empty-state compact"><span>暂无人工纠正</span></div>`;
    } else {
      const preferenceHtml = preference
        ? `<p class="preference-line">观影亮度偏好：<strong>${fmt(preference.mean)}%</strong>（观测 ${preference.count} 次，置信 ${preference.confidence}）</p>`
        : "";
      const overrideHtml = overrides
        .map((event) => `<p class="override-line">🖐 ${escapeHtml(event.message)}</p>`)
        .join("");
      feedbackBody.innerHTML = `${preferenceHtml}${overrideHtml}`;
    }
  }

  const timeline = $("#agent-timeline");
  if (timeline) {
    const events = (data.timeline || []).slice(-12).reverse();
    timeline.innerHTML = events.length
      ? events
          .map(
            (event) => `
        <div class="timeline-row kind-${escapeHtml(event.kind)}">
          <span class="timeline-time">${escapeHtml(event.time)}</span>
          <span class="timeline-kind">${escapeHtml(kindLabel(event.kind))}</span>
          <span class="timeline-msg">${escapeHtml(event.message)}</span>
        </div>`
          )
          .join("")
      : `<div class="empty-state compact"><span>暂无事件</span></div>`;
  }
}

function agentState(data) {
  return data.current_state || {};
}

function metricLabel(metric) {
  return { brightness: "亮度%", temperature: "温度°C", curtain_position: "窗帘%" }[metric] || metric;
}

function kindLabel(kind) {
  return {
    goal_received: "目标",
    goal_compiled: "编译",
    world_snapshot: "感知",
    candidates_generated: "候选",
    plan_selected: "选优",
    policy_check: "校验",
    execution: "执行",
    verification: "回读",
    no_action: "无需动作",
    goal_evaluated: "评估",
    human_override: "人工纠正",
    attribution: "归因",
    goal_cancelled: "取消",
    replan: "重规划",
  }[kind] || kind;
}

function actionLabel(action) {
  const map = {
    set_brightness: `亮度 → ${action.value}%`,
    set_position: `窗帘 → ${action.value}%`,
    set_temperature: `温度 → ${action.value}°C 制冷`,
    set_hvac_mode: `模式 → ${action.value}`,
    turn_off: "关闭",
  };
  return map[action.capability] || action.capability;
}

function fmt(value) {
  return value === null || value === undefined ? "--" : `${Number(value).toFixed(Number.isInteger(value) ? 0 : 1)}`;
}

async function loadAll() {
  await Promise.all([loadStatus(), loadDevices(), loadConversation(), loadAgentActivity(), loadEnergyHistory()]);
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
  if (state.deviceBusyIds.has(device.device_id)) return null;
  setDeviceBusy(device.device_id, true);
  try {
    const payload = await post(`/api/devices/${encodeURIComponent(device.device_id)}/control`, body, message);
    if (payload?.devices) renderInventory(payload);
    return payload;
  } finally {
    setDeviceBusy(device.device_id, false);
  }
}

function setDeviceBusy(deviceId, busy) {
  if (busy) state.deviceBusyIds.add(deviceId);
  else state.deviceBusyIds.delete(deviceId);
  const selectors = [
    `[data-device-id="${CSS.escape(deviceId)}"]`,
    `[data-floor-device-id="${CSS.escape(deviceId)}"]`,
  ];
  $$(selectors.join(",")).forEach((element) => {
    element.classList.toggle("device-busy", busy);
    element.setAttribute("aria-busy", String(busy));
    if (element.matches("button")) element.disabled = busy;
    $$(`button, input, select`, element).forEach((control) => { control.disabled = busy; });
  });
}

function switchView(view) {
  if (view !== "assistant" && voiceInput?.active) voiceInput.cancel();
  state.view = view;
  $$(".view").forEach((element) => element.classList.toggle("active", element.dataset.view === view));
  $$(".rail-button[data-view-target]").forEach((button) => {
    const active = button.dataset.viewTarget === view && !(view === "home" && button.dataset.homeSection);
    button.classList.toggle("active", active);
  });
  if (view === "events") loadEvents();
  if (view === "assistant") loadConversation();
}

function selectFloorDevice(deviceId) {
  state.floorSelectedId = deviceId;
  renderFloorplan();
  refreshIcons();
}

function showDeviceDetails(device) {
  if (!device) return;
  switchView("devices");
  state.roomFilter = device.room;
  renderRoomTabs();
  renderDeviceGrid();
  requestAnimationFrame(() => {
    const card = $(`[data-device-id="${CSS.escape(device.device_id)}"]`, $("#device-grid"));
    card?.scrollIntoView({ behavior: "smooth", block: "center" });
    card?.classList.add("attention");
    setTimeout(() => card?.classList.remove("attention"), 1600);
  });
}

async function activateFloorDevice(device) {
  if (!device?.online) {
    toast(`${device?.name || "设备"}当前离线`, true);
    return;
  }
  const action = device.ui?.primary_action;
  if (!action?.payload) return;
  await controlDevice(device, action.payload, `${device.name}${action.label || "状态已更新"}`);
}

async function handleFloorplanClick(event) {
  if (state.floorLongPressTriggered) {
    state.floorLongPressTriggered = false;
    event.preventDefault();
    return;
  }
  const button = event.target.closest("[data-floor-device-id]");
  if (!button) return;
  const device = state.devices.find((item) => item.device_id === button.dataset.floorDeviceId);
  if (!device) return;
  await activateFloorDevice(device);
}

function beginFloorLongPress(event) {
  const button = event.target.closest("[data-floor-device-id]");
  if (!button) return;
  clearTimeout(state.floorLongPressTimer);
  state.floorLongPressTriggered = false;
  state.floorLongPressTimer = setTimeout(() => {
    const device = state.devices.find((item) => item.device_id === button.dataset.floorDeviceId);
    if (!device) return;
    state.floorLongPressTriggered = true;
    selectFloorDevice(device.device_id);
    showDeviceDetails(device);
    setTimeout(() => { state.floorLongPressTriggered = false; }, 900);
  }, 560);
}

function cancelFloorLongPress() {
  clearTimeout(state.floorLongPressTimer);
  state.floorLongPressTimer = null;
}

async function handleFloorPanelClick(event) {
  if (event.target.closest("[data-floor-close]")) {
    state.floorSelectedId = null;
    renderFloorplan();
    refreshIcons();
    return;
  }
  if (event.target.closest("[data-floor-detail]")) {
    showDeviceDetails(state.devices.find((item) => item.device_id === state.floorSelectedId));
    return;
  }
  await handleDeviceClick(event);
}

async function handleFavoriteClick(event) {
  const card = event.target.closest("[data-device-id]");
  if (!card) return;
  const device = state.devices.find((item) => item.device_id === card.dataset.deviceId);
  if (!device) return;
  if (event.target.closest("[data-favorite-open]")) {
    selectFloorDevice(device.device_id);
    $("#floorplan-stage")?.scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }
  await handleDeviceClick(event);
}

function handleRoomStatusClick(event) {
  const card = event.target.closest("[data-room-card]");
  if (!card) return;
  state.roomFilter = card.dataset.roomCard;
  renderRoomTabs();
  renderDeviceGrid();
  switchView("devices");
}

function handleFloorplanRoomTab(event) {
  const button = event.target.closest("[data-floor-room]");
  if (!button) return;
  state.floorRoomFocus = button.dataset.floorRoom;
  state.floorSelectedId = null;
  renderFloorplanRoomTabs();
  renderFloorplan();
  refreshIcons();
}

async function confirmDashboardRecommendation() {
  const button = $("#dashboard-proactive-confirm");
  button.disabled = true;
  try {
    const observation = await post("/api/observe", {}, null);
    if (!observation) return;
    await post("/api/message", { text: "确认" }, "主动节能建议已确认执行");
    await Promise.all([loadStatus(), loadDevices()]);
  } finally {
    button.disabled = false;
  }
}

async function sendChat(textValue) {
  if (state.chatBusy || voiceInput?.active) return;
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
  if (event.target.closest("[data-open-esphome-nodes]")) {
    await openEsphomeNodes();
    return;
  }
  const favoriteButton = event.target.closest("[data-favorite-toggle]");
  if (favoriteButton) {
    const card = favoriteButton.closest("[data-device-id]");
    const deviceId = card?.dataset.deviceId;
    if (!deviceId) return;
    state.favoriteDeviceIds = state.favoriteDeviceIds.includes(deviceId)
      ? state.favoriteDeviceIds.filter((item) => item !== deviceId)
      : [...state.favoriteDeviceIds, deviceId];
    localStorage.setItem("spacebutler.favoriteDeviceIds", JSON.stringify(state.favoriteDeviceIds));
    renderDeviceGrid();
    renderFavoriteDevices();
    refreshIcons();
    toast(state.favoriteDeviceIds.includes(deviceId) ? "已加入收藏" : "已取消收藏");
    return;
  }
  const actionButton = event.target.closest("[data-device-action], [data-control-payload], [data-control-step], [data-control-apply]");
  if (!actionButton) return;
  const card = actionButton.closest("[data-device-id]");
  const device = state.devices.find((item) => item.device_id === card?.dataset.deviceId);
  if (!device) return;
  if (actionButton.dataset.deviceAction === "delete") {
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
  if (actionButton.matches("[data-control-payload]")) {
    const payload = JSON.parse(actionButton.dataset.controlPayload || "{}");
    actionButton.blur();
    await controlDevice(device, payload);
    return;
  }
  const valueInput = $("[data-control-value]", card);
  if (!valueInput) return;
  if (actionButton.matches("[data-control-step]")) {
    const nextValue = Number(valueInput.value) + Number(actionButton.dataset.controlStep);
    valueInput.value = Math.min(Number(valueInput.max), Math.max(Number(valueInput.min), nextValue));
    return;
  }
  if (actionButton.matches("[data-control-apply]")) {
    const payload = JSON.parse(valueInput.dataset.controlPayload || "{}");
    payload[valueInput.dataset.controlField] = Number(valueInput.value);
    actionButton.blur();
    await controlDevice(device, payload);
  }
}

async function handleDeviceChange(event) {
  const fromInventory = Boolean(event.target.closest("#device-grid"));
  const card = event.target.closest("[data-device-id]");
  const device = state.devices.find((item) => item.device_id === card?.dataset.deviceId);
  if (!device) return;
  if (event.target.matches("[data-control-range]")) {
    const payload = JSON.parse(event.target.dataset.controlPayload || "{}");
    payload[event.target.dataset.controlField] = Number(event.target.value);
    await controlDevice(device, payload, `${device.name}已更新`);
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
  event.target.blur();
  if (fromInventory) renderDeviceGrid();
  renderSpatialDashboard();
}

// 设备由节点固件和 MQTT 容器定义，工作台不能创建设备。这里如实展示
// 各 ESPHome 节点端点与 Home Assistant 的解析状态。
async function openEsphomeNodes() {
  const dialog = $("#esphome-nodes-dialog");
  const list = $("#esphome-node-list");
  dialog.showModal();
  list.innerHTML = `<div class="loading-state compact"><i data-lucide="loader-circle"></i><span>正在读取节点状态</span></div>`;
  refreshIcons();
  try {
    const payload = await api("/api/esphome-nodes");
    list.innerHTML = (payload.nodes || []).map((node) => `
      <div class="ha-node ${node.connected ? "connected" : ""}">
        <span class="ha-node-dot"></span>
        <span class="ha-node-copy">
          <strong>${escapeHtml(node.label)}</strong>
          <span>${escapeHtml(node.host)}:${escapeHtml(node.port)} · ESPHome Native API</span>
        </span>
        <span class="ha-node-state">${node.connected ? "已解析" : "未发现实体"}</span>
      </div>`).join("");
    const projected = payload.projected || {};
    $("#esphome-node-note").textContent =
      `当前已接入 ${projected.esphome || 0} 台 ESPHome 设备、${projected.mqtt || 0} 台 MQTT 设备。`
      + "新增设备需在节点侧注册后由 Home Assistant 自动发现，工作台不创建设备。";
    refreshIcons();
  } catch (error) {
    list.innerHTML = `<div class="ha-energy-empty">${escapeHtml(error.message)}</div>`;
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
  voiceInput = new window.PushToTalk({
    input: $("#chat-input"), button: $("#voice-hold"), cancelButton: $("#voice-cancel"),
    panel: $("#voice-panel"), status: $("#voice-status"), transcript: $("#voice-transcript"), timer: $("#voice-timer"),
    isBusy: () => state.chatBusy || Boolean(state.conversation?.busy),
    onStart: () => switchView("assistant"),
    onActive: () => setChatBusy(state.chatBusy),
    onSubmit: sendChat,
  });
  $("#welcome-date").textContent = new Intl.DateTimeFormat("zh-CN", {
    month: "long",
    day: "numeric",
    weekday: "long",
  }).format(new Date());
  $$(".rail-button[data-view-target]").forEach((button) => {
    button.addEventListener("click", () => {
      switchView(button.dataset.viewTarget);
      if (button.dataset.homeSection === "inventory") {
        requestAnimationFrame(() => $("#home-inventory")?.scrollIntoView({ behavior: "smooth", block: "start" }));
      }
    });
  });
  $$('[data-view-jump]').forEach((button) => button.addEventListener("click", () => switchView(button.dataset.viewJump)));
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
  $("#demo-selfcheck")?.addEventListener("click", runDemoSelfCheck);
  $("#demo-reset")?.addEventListener("click", runDemoReset);
  $("#demo-scenario-list")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-demo-scenario]");
    if (button) runDemoScenario(button.dataset.demoScenario);
  });
  renderDemoScenarios();
  $("#agent-demo-override")?.addEventListener("click", async () => {
    await post("/api/agent/demo-override", { brightness: 45 }, "已通过 Home Assistant 调整主灯，等待智能体识别");
    await loadAgentActivity();
  });
  $("#agent-demo-disturbance")?.addEventListener("click", async () => {
    await post("/api/agent/demo-disturbance", { temperature: 31 }, "已注入热扰动，智能体将自动重规划");
    await loadAgentActivity();
  });
  $("#devices-reload").addEventListener("click", loadDevices);
  $("#events-reload").addEventListener("click", loadEvents);
  $("#open-esphome-nodes").addEventListener("click", openEsphomeNodes);
  $("#close-esphome-nodes").addEventListener("click", () => $("#esphome-nodes-dialog").close());
  $("#energy-range").addEventListener("click", (event) => {
    const button = event.target.closest("[data-energy-hours]");
    if (!button) return;
    state.energyHours = Number(button.dataset.energyHours);
    $$("#energy-range button").forEach((item) => item.classList.toggle("active", item === button));
    loadEnergyHistory();
  });
  $("#room-tabs").addEventListener("click", (event) => {
    const tab = event.target.closest("[data-room]");
    if (!tab) return;
    state.roomFilter = tab.dataset.room;
    renderRoomTabs();
    renderDeviceGrid();
  });
  $("#device-grid").addEventListener("click", handleDeviceClick);
  $("#device-grid").addEventListener("change", handleDeviceChange);
  $("#favorite-devices").addEventListener("click", handleFavoriteClick);
  $("#favorite-devices").addEventListener("change", handleDeviceChange);
  $("#room-status-grid")?.addEventListener("click", handleRoomStatusClick);
  $("#floorplan-room-tabs").addEventListener("click", handleFloorplanRoomTab);
  $("#floorplan-devices").addEventListener("click", handleFloorplanClick);
  $("#floorplan-devices").addEventListener("pointerdown", beginFloorLongPress);
  ["pointerup", "pointercancel", "pointerleave"].forEach((eventName) => {
    $("#floorplan-devices").addEventListener(eventName, cancelFloorLongPress);
  });
  $("#floor-device-panel").addEventListener("click", handleFloorPanelClick);
  $("#floor-device-panel").addEventListener("change", handleDeviceChange);
  $("#dashboard-proactive-review").addEventListener("click", () => switchView("agent"));
  $("#dashboard-proactive-confirm").addEventListener("click", confirmDashboardRecommendation);
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

  /* ---- 工作流视图入口 ---- */
  initWorkflowView();
});

/* ================================================================
   Workflow View — 独立 JS 模块
   不修改已有任何函数/状态，全部通过 wf 前缀命名空间隔离
   ================================================================ */

let WF_DEVICES = [];

const wfState = {
  workflow: { name: "新的工作流", phrase: "", runMode: "sequence", steps: [] },
  saved: [],
  activeIndex: 0,
  draggedIndex: null,
  running: false,
  wfTab: "builder",
  aiGenBusy: false,
  deviceSignature: null,
};

function wfClone(value) {
  return JSON.parse(JSON.stringify(value));
}

function wfSyncDevices() {
  WF_DEVICES = state.devices
    .filter((device) => Array.isArray(device.ui?.workflow_actions) && device.ui.workflow_actions.length)
    .map((device) => ({
      id: device.device_id,
      name: device.name,
      room: device.room_name,
      type: device.ui?.type_label || device.type,
      state: device.ui?.state_label || dashboardDeviceState(device),
      actions: device.ui.workflow_actions,
      online: Boolean(device.online),
      source: device,
    }));
}

function wfDevice(id) {
  return WF_DEVICES.find((device) => device.id === id) || null;
}

function wfAction(device, actionId) {
  if (!device) return null;
  if (!actionId) return device.actions[0] || null;
  return device.actions.find((action) => action.id === actionId) || null;
}

function wfDefault(action) {
  return action?.parameter?.default ?? "";
}

function wfValueLabel(action, value) {
  if (!action?.parameter) return action?.label || "--";
  if (action.parameter.type === "select") {
    return action.parameter.options?.find((option) => option.value === value)?.label || String(value);
  }
  return `${value}${action.parameter.unit || ""}`;
}

function wfPayload(step) {
  const device = wfDevice(step.deviceId);
  const action = wfAction(device, step.action);
  if (!device || !action) throw new Error("工作流引用的设备或动作已不可用");
  const payload = { ...(action.payload || {}) };
  const parameter = action.parameter;
  if (parameter) {
    payload[parameter.field] = parameter.type === "number" ? Number(step.value) : step.value;
  }
  return payload;
}

function wfSanitizeWorkflow(candidate) {
  if (!candidate || typeof candidate !== "object") return null;
  const steps = (Array.isArray(candidate.steps) ? candidate.steps : []).map((step) => {
    const device = wfDevice(String(step?.deviceId || ""));
    const action = wfAction(device, String(step?.action || ""));
    if (!device || !action) return null;
    let value = step.value ?? wfDefault(action);
    if (action.parameter?.type === "number") {
      value = Number(value);
      if (!Number.isFinite(value)) value = Number(wfDefault(action));
      value = Math.min(Number(action.parameter.max), Math.max(Number(action.parameter.min), value));
    } else if (action.parameter?.type === "select") {
      const allowed = new Set((action.parameter.options || []).map((option) => option.value));
      if (!allowed.has(value)) value = wfDefault(action);
    }
    return {
      deviceId: device.id,
      action: action.id,
      value,
      delay: Math.min(60, Math.max(0, Number(step.delay) || 0)),
    };
  }).filter(Boolean);
  if (!steps.length) return null;
  return {
    name: String(candidate.name || "未命名工作流").slice(0, 40),
    phrase: String(candidate.phrase || "").slice(0, 60),
    runMode: "sequence",
    steps,
    ...(candidate.planning && typeof candidate.planning === "object"
      ? { planning: wfClone(candidate.planning) }
      : {}),
  };
}

function wfPersistSaved() {
  try { localStorage.setItem("spacebutler.workflows", JSON.stringify(wfState.saved)); } catch {}
}

function wfRenderDevicePicker() {
  const picker = $("#wf-device-picker");
  const addButton = $("#wf-add-device");
  if (!picker) return;
  picker.innerHTML = WF_DEVICES.length
    ? WF_DEVICES.map((device) => `<option value="${escapeHtml(device.id)}">${escapeHtml(device.room)} · ${escapeHtml(device.name)} · ${escapeHtml(device.type)}${device.online ? "" : " · 离线"}</option>`).join("")
    : '<option value="">没有已注册且可控制的设备</option>';
  picker.disabled = !WF_DEVICES.length;
  if (addButton) addButton.disabled = !WF_DEVICES.length;
}

function wfRefreshDevices() {
  wfSyncDevices();
  const signature = JSON.stringify(WF_DEVICES.map((device) => ({
    id: device.id,
    online: device.online,
    actions: device.actions,
  })));
  if (signature === wfState.deviceSignature) {
    if (!wfState.running) {
      wfRenderStatePreview();
      wfRenderStateList();
    }
    return;
  }
  wfState.deviceSignature = signature;
  wfRenderDevicePicker();
  if (wfState.running) return;
  if (!WF_DEVICES.length) {
    wfRenderAll();
    return;
  }
  const sanitized = wfState.saved.map(wfSanitizeWorkflow).filter(Boolean);
  const changed = JSON.stringify(sanitized) !== JSON.stringify(wfState.saved);
  wfState.saved = sanitized;
  if (changed) wfPersistSaved();
  const current = wfSanitizeWorkflow(wfState.workflow);
  if (current) {
    wfState.workflow = current;
  } else if (Array.isArray(wfState.workflow?.steps) && !wfState.workflow.steps.length) {
    wfState.workflow = {
      ...wfState.workflow,
      name: String(wfState.workflow.name || "新的工作流").slice(0, 40),
      phrase: String(wfState.workflow.phrase || "").slice(0, 60),
      runMode: "sequence",
      steps: [],
    };
  } else if (wfState.activeIndex >= 0 && wfState.saved[wfState.activeIndex]) {
    wfState.workflow = wfClone(wfState.saved[wfState.activeIndex]);
  } else {
    wfState.workflow = { name: "新的工作流", phrase: "", runMode: "sequence", steps: [] };
  }
  wfRenderAll();
}

/* ---- AI 生成工作流 ---- */
async function wfAiGenerate() {
  if (wfState.aiGenBusy) return;
  const prompt = ($("#wf-ai-prompt")?.value || "").trim();
  if (!prompt) return;

  wfState.aiGenBusy = true;
  const btn = $("#wf-ai-generate");
  if (btn) { btn.disabled = true; btn.textContent = "分析中…"; }

  try {
    const response = await api("/api/workflows/preview", {
      method: "POST",
      body: JSON.stringify({ text: prompt }),
    });
    const plan = response.plan || {};
    const steps = [];
    (plan.steps || []).forEach((step) => {
      const device = wfDevice(step.device_id);
      const action = wfAction(device, step.action);
      if (!device || !action) return;
      if (step.action === "set_temperature" && step.mode) {
        const modeAction = wfAction(device, "set_hvac_mode");
        const supported = modeAction?.parameter?.options?.some((option) => option.value === step.mode);
        if (modeAction && supported) {
          steps.push({ deviceId: device.id, action: modeAction.id, value: step.mode, delay: 0 });
        }
      }
      const value = action.parameter?.field === "mode"
        ? step.mode ?? step.value ?? wfDefault(action)
        : step.value ?? wfDefault(action);
      steps.push({ deviceId: device.id, action: action.id, value, delay: 0 });
    });
    if (!steps.length) throw new Error(plan.reply || "Agent 未生成可执行的真实设备步骤");
    wfState.workflow = {
      name: plan.summary || `Agent 工作流 ${new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`,
      phrase: prompt,
      runMode: "sequence",
      steps,
      planning: {
        route: plan.route || "deterministic_fallback",
        reasoning: plan.reasoning || "",
        generatedAt: Date.now(),
      },
    };
    wfRenderBuilder();
    wfRenderStatePreview();
    toast(`已基于 ${steps.length} 个真实设备动作生成工作流`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = '<i data-lucide="wand-2"></i><span>生成</span>'; }
    wfState.aiGenBusy = false;
    refreshIcons();
  }
}

/* ---- 渲染 ---- */
function wfParameterEditor(action, step, index) {
  const parameter = action?.parameter;
  if (!parameter) return `<input value="无需参数" disabled>`;
  if (parameter.type === "select") {
    return `<select data-wf-field="value" data-wf-idx="${index}">
      ${(parameter.options || []).map((option) => `
        <option value="${escapeHtml(option.value)}" ${option.value === step.value ? "selected" : ""}>${escapeHtml(option.label)}</option>`).join("")}
    </select>`;
  }
  return `<input data-wf-field="value" data-wf-idx="${index}" type="number"
    min="${Number(parameter.min)}" max="${Number(parameter.max)}" step="${Number(parameter.step)}"
    value="${Number(step.value)}">`;
}

function wfRenderBuilder() {
  const wf = wfState.workflow;
  if ($("#wf-name")) $("#wf-name").value = wf.name;
  if ($("#wf-phrase")) $("#wf-phrase").value = wf.phrase;
  if ($("#wf-run-mode")) $("#wf-run-mode").value = wf.runMode || "sequence";

  const container = $("#wf-steps");
  if (!container) return;
  if (!wf.steps.length) {
    container.innerHTML = `<div class="empty-workflow">暂未添加步骤 — 使用上方 Agent 规划或手动添加设备</div>`;
    return;
  }
  container.innerHTML = wf.steps.map((step, index) => {
    const device = wfDevice(step.deviceId);
    const action = wfAction(device, step.action);
    if (!device || !action) {
      return `<article class="workflow-step-card"><div class="step-drag-handle">!</div><div>
        <strong>设备或动作已不可用</strong>
        <button class="step-remove-btn" type="button" data-wf-remove="${index}">删除</button>
      </div></article>`;
    }
    return `
      <article class="workflow-step-card" draggable="true" data-wf-step="${index}">
        <div class="step-drag-handle" title="拖拽排序">::</div>
        <div>
          <div class="step-head-row">
            <div class="step-device-title">
              <span class="step-index-badge">${index + 1}</span>
              <span>${escapeHtml(device.name)}</span>
            </div>
            <button class="step-remove-btn" type="button" data-wf-remove="${index}">删除</button>
          </div>
          <div class="step-fields-row">
            <label><span>设备</span>
              <select data-wf-field="deviceId" data-wf-idx="${index}">
                ${WF_DEVICES.map((item) => `<option value="${escapeHtml(item.id)}" ${item.id === step.deviceId ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("")}
              </select>
            </label>
            <label><span>动作</span>
              <select data-wf-field="action" data-wf-idx="${index}">
                ${device.actions.map((item) => `<option value="${escapeHtml(item.id)}" ${item.id === step.action ? "selected" : ""}>${escapeHtml(item.label)}</option>`).join("")}
              </select>
            </label>
            <label><span>参数</span>
              ${wfParameterEditor(action, step, index)}
            </label>
            <label><span>延迟(分)</span>
              <input data-wf-field="delay" data-wf-idx="${index}" type="number" min="0" max="60" value="${step.delay}">
            </label>
          </div>
        </div>
      </article>`;
  }).join("");
  wfBindStepEvents();
  lucide.createIcons();
}

function wfBindStepEvents() {
  $$("[data-wf-field]").forEach((input) => {
    input.addEventListener("change", () => {
      const idx = Number(input.dataset.wfIdx);
      const field = input.dataset.wfField;
      wfState.workflow.steps[idx][field] = field === "delay" ? Number(input.value) : input.value;
      if (field === "deviceId") {
        const dev = wfDevice(input.value);
        const firstAction = dev?.actions[0];
        if (!firstAction) return;
        wfState.workflow.steps[idx].action = firstAction.id;
        wfState.workflow.steps[idx].value = wfDefault(firstAction);
      }
      if (field === "action") {
        const dev = wfDevice(wfState.workflow.steps[idx].deviceId);
        const action = wfAction(dev, input.value);
        wfState.workflow.steps[idx].value = wfDefault(action);
      }
      wfRenderAll();
    });
  });
  $$("[data-wf-remove]").forEach((btn) => {
    btn.addEventListener("click", () => {
      wfState.workflow.steps.splice(Number(btn.dataset.wfRemove), 1);
      wfRenderAll();
    });
  });
  $$(".workflow-step-card").forEach((card) => {
    card.addEventListener("dragstart", () => {
      wfState.draggedIndex = Number(card.dataset.wfStep);
      card.classList.add("dragging");
    });
    card.addEventListener("dragend", () => card.classList.remove("dragging"));
    card.addEventListener("dragover", (event) => event.preventDefault());
    card.addEventListener("drop", (event) => {
      event.preventDefault();
      const target = Number(card.dataset.wfStep);
      if (wfState.draggedIndex === null || wfState.draggedIndex === target) return;
      const [step] = wfState.workflow.steps.splice(wfState.draggedIndex, 1);
      wfState.workflow.steps.splice(target, 0, step);
      wfRenderAll();
    });
  });
}

function wfRenderStatePreview(activeDeviceId = null, statusClass = null) {
  const container = $("#wf-state-preview");
  if (!container) return;
  container.innerHTML = WF_DEVICES.map((device) => `
    <div class="state-item-row ${device.id === activeDeviceId ? statusClass || "" : ""}">
      <div><strong>${escapeHtml(device.name)}</strong><span>${escapeHtml(device.room)} · ${escapeHtml(device.type)}</span></div>
      <div class="state-val">${escapeHtml(device.state)}</div>
    </div>
  `).join("");
}

function wfRenderRunner() {
  const wf = wfState.workflow;
  const saved = wfState.saved[wfState.activeIndex] || wf;
  if ($("#wf-runner-title")) $("#wf-runner-title").textContent = saved.name;
  if ($("#wf-active-phrase")) $("#wf-active-phrase").textContent = saved.phrase;
  if ($("#wf-runner-subtitle")) $("#wf-runner-subtitle").textContent = "请确认当前场景、启动口令和设备状态无误后执行。";
  if ($("#wf-voice-cmd") && !$("#wf-voice-cmd").value.trim()) $("#wf-voice-cmd").value = saved.phrase;
  wfUpdateCommandMatch();
  wfRenderTimeline(-1, -1);
  wfRenderStateList();
}

function wfUpdateCommandMatch() {
  const cmd = ($("#wf-voice-cmd")?.value || "").trim();
  const matchIdx = wfState.saved.findIndex((item) => item.phrase === cmd);
  if (matchIdx >= 0) {
    if ($("#wf-command-match")) {
      $("#wf-command-match").textContent = `已匹配：${wfState.saved[matchIdx].name}`;
      $("#wf-command-match").style.color = "var(--green)";
    }
  } else {
    if ($("#wf-command-match")) {
      $("#wf-command-match").textContent = "未匹配任何工作流";
      $("#wf-command-match").style.color = "var(--amber)";
    }
  }
  return matchIdx;
}

function wfRenderTimeline(runningIdx, doneIdx) {
  const wf = wfState.workflow;
  const container = $("#wf-timeline");
  if (!container) return;
  container.innerHTML = wf.steps.map((step, index) => {
    const device = wfDevice(step.deviceId);
    const action = wfAction(device, step.action);
    const status = step.runtimeStatus || (index <= doneIdx ? "done" : index === runningIdx ? "running" : "pending");
    const label = { done: "完成", running: "执行中", waiting: "延迟中", failed: "失败", pending: "等待" }[status] || "等待";
    return `
      <article class="timeline-entry ${status}">
        <div class="timeline-idx">${index + 1}</div>
        <div>
          <strong>${escapeHtml(device?.name || step.deviceId)}</strong>
          <span>${step.delay ? `延迟 ${escapeHtml(step.delay)} 分钟后 · ` : ""}${escapeHtml(action?.label || step.action)}${action?.parameter ? ` · ${escapeHtml(wfValueLabel(action, step.value))}` : ""}</span>
        </div>
        <div class="timeline-status-label">${label}</div>
      </article>`;
  }).join("");
}

function wfRenderStateList(activeDeviceId = null, statusClass = null) {
  const container = $("#wf-state-list");
  if (!container) return;
  container.innerHTML = WF_DEVICES.map((device) => `
    <div class="state-item-row ${device.id === activeDeviceId ? statusClass || "" : ""}">
      <div><strong>${escapeHtml(device.name)}</strong><span>${escapeHtml(device.room)} · ${escapeHtml(device.type)}</span></div>
      <div class="state-val">${escapeHtml(device.state)}</div>
    </div>
  `).join("");
}

/* ---- Agent 推理链路 ---- */
const WF_REASONING_CHAIN = [
  { key: "slot", label: "槽位提取", icon: "scan-search" },
  { key: "intent", label: "意图识别", icon: "brain-circuit" },
  { key: "env", label: "环境感知", icon: "thermometer" },
  { key: "plan", label: "计划生成", icon: "git-branch" },
  { key: "validate", label: "安全校验", icon: "shield-check" },
  { key: "execute", label: "工具执行", icon: "play" },
  { key: "verify", label: "结果验证", icon: "circle-check-big" },
  { key: "reflect", label: "反思评估", icon: "lightbulb" },
];

function wfRenderReasoning(activeIndex) {
  const container = $("#wf-reasoning-steps");
  if (!container) return;
  container.innerHTML = WF_REASONING_CHAIN.map((item, index) => `
    <span class="reasoning-step ${index < activeIndex ? "done" : index === activeIndex ? "active" : ""}">
      ${item.label}
    </span>
  `).join("");
}

async function wfRunWorkflow() {
  if (wfState.running) return;
  const matchIdx = wfUpdateCommandMatch();
  if (matchIdx < 0) {
    if ($("#wf-command-match")) {
      $("#wf-command-match").textContent = "未匹配 — 请检查口令或先保存工作流";
      $("#wf-command-match").style.color = "var(--red)";
    }
    return;
  }
  wfState.activeIndex = matchIdx;
  const active = wfClone(wfState.saved[matchIdx]);
  const validSteps = active.steps.filter((step) => wfAction(wfDevice(step.deviceId), step.action));
  if (!validSteps.length || validSteps.length !== active.steps.length) {
    toast("工作流中有设备或动作已不可用，请先编辑后再执行", true);
    return;
  }
  wfState.running = true;
  wfState.workflow = active;
  if ($("#wf-run-btn")) $("#wf-run-btn").disabled = true;
  if ($("#wf-exec-status")) $("#wf-exec-status").textContent = "执行中";
  wfRenderStateList();
  wfRenderReasoning(active.planning ? 4 : 3);
  const failures = [];
  let completed = 0;
  try {
    for (let i = 0; i < active.steps.length; i += 1) {
      const step = active.steps[i];
      if (Number(step.delay) > 0) {
        step.runtimeStatus = "waiting";
        wfRenderTimeline(i, completed - 1);
        await wfWait(Number(step.delay) * 60_000);
      }
      step.runtimeStatus = "running";
      wfRenderTimeline(i, completed - 1);
      wfRenderReasoning(5);
      wfRenderStateList(step.deviceId, "executing");
      try {
        const device = state.devices.find((item) => item.device_id === step.deviceId);
        if (!device?.online) throw new Error(`${wfDevice(step.deviceId)?.name || step.deviceId}当前离线`);
        const response = await controlDevice(device, wfPayload(step), `${device.name}工作流步骤已执行`);
        if (!response) throw new Error("设备控制未返回成功结果");
        step.runtimeStatus = "done";
        completed += 1;
        wfSyncDevices();
        wfRenderReasoning(7);
        wfRenderStateList(step.deviceId, "done");
      } catch (error) {
        step.runtimeStatus = "failed";
        failures.push({ step, message: error.message });
      }
      wfRenderTimeline(-1, -1);
    }
    wfRenderReasoning(8);
    if ($("#wf-exec-status")) $("#wf-exec-status").textContent = failures.length ? "部分失败" : "已完成";
    const reflection = $("#wf-reflection");
    const reflectionText = $("#wf-reflection-text");
    if (reflection && reflectionText) {
      reflection.hidden = false;
      reflectionText.textContent = failures.length
        ? `已向 Home Assistant 执行 ${active.steps.length} 个真实步骤，${completed} 个通过状态回读，${failures.length} 个失败：${failures.map((item) => item.message).join("；")}`
        : `已通过 Home Assistant 完成 ${completed} 个真实设备步骤，每个步骤均以设备状态回读为准。`;
    }
  } finally {
    wfState.running = false;
    if ($("#wf-run-btn")) $("#wf-run-btn").disabled = false;
  }
}

function wfWait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/* ---- 工作流保存与加载 ---- */
function wfSaveWorkflow() {
  wfSyncMeta();
  const wf = wfSanitizeWorkflow(wfState.workflow);
  if (!wf) {
    toast("请至少添加一个当前在线且可控制的真实设备步骤", true);
    return;
  }
  if (!wf.phrase) {
    toast("请填写唯一的启动口令", true);
    return;
  }
  wfState.workflow = wf;
  const existing = wfState.saved.findIndex((item) => item.name === wf.name);
  if (existing >= 0) {
    wfState.activeIndex = existing;
    wfState.saved[existing] = wfClone(wf);
  } else {
    wfState.saved.unshift(wfClone(wf));
    wfState.activeIndex = 0;
  }
  wfPersistSaved();
  wfRenderAll();
}

function wfSyncMeta() {
  wfState.workflow.name = ($("#wf-name")?.value || "").trim() || "未命名工作流";
  wfState.workflow.phrase = ($("#wf-phrase")?.value || "").trim();
  wfState.workflow.runMode = "sequence";
}

function wfNewWorkflow() {
  wfState.workflow = {
    name: "新的工作流", phrase: "", runMode: "sequence", steps: [],
  };
  wfState.activeIndex = -1;
  wfState.wfTab = "builder";
  wfSwitchPane("builder");
  wfRenderAll();
}

function wfSwitchPane(pane) {
  wfState.wfTab = pane;
  $$(".workflow-pane").forEach((el) => el.classList.toggle("active", el.dataset.workflowPane === pane));
  $$(".workflow-heading-tabs button").forEach((btn) => btn.classList.toggle("active", btn.dataset.workflowTab === pane));
}

function wfRenderAll() {
  wfRenderBuilder();
  wfRenderStatePreview();
  wfRenderRunner();
  wfRenderStateList();
  wfRenderReasoning(0);
  wfRenderSavedList();
}

function wfRenderSavedList() {
  const container = $("#wf-saved-list");
  if (!container) return;
  const countEl = $("#wf-saved-count");
  if (countEl) countEl.textContent = wfState.saved.length;

  if (!wfState.saved.length) {
    container.innerHTML = `<div class="empty-workflow">暂无保存的工作流</div>`;
    return;
  }
  container.innerHTML = wfState.saved.map((item, index) => `
    <article class="saved-item ${index === wfState.activeIndex ? "active" : ""}" data-wf-open="${index}">
      <strong>${escapeHtml(item.name)}</strong>
      <span class="saved-meta">口令：${escapeHtml(item.phrase)} · ${item.steps.length} 个步骤</span>
      <div class="saved-actions">
        <button class="edit-btn" type="button" data-wf-edit="${index}">编辑</button>
      </div>
    </article>
  `).join("");

  // 绑定点击事件
  $$("[data-wf-open]").forEach((card) => {
    card.addEventListener("click", () => {
      const idx = Number(card.dataset.wfOpen);
      wfState.activeIndex = idx;
      wfState.workflow = wfClone(wfState.saved[idx]);
      if ($("#wf-name")) $("#wf-name").value = wfState.workflow.name;
      if ($("#wf-phrase")) $("#wf-phrase").value = wfState.workflow.phrase;
      if ($("#wf-run-mode")) $("#wf-run-mode").value = wfState.workflow.runMode || "sequence";
      wfSwitchPane("runner");
      wfRenderAll();
    });
  });
  $$("[data-wf-edit]").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.stopPropagation();
      const idx = Number(btn.dataset.wfEdit);
      wfState.activeIndex = idx;
      wfState.workflow = wfClone(wfState.saved[idx]);
      if ($("#wf-name")) $("#wf-name").value = wfState.workflow.name;
      if ($("#wf-phrase")) $("#wf-phrase").value = wfState.workflow.phrase;
      if ($("#wf-run-mode")) $("#wf-run-mode").value = wfState.workflow.runMode || "sequence";
      wfSwitchPane("builder");
      wfRenderAll();
    });
  });
}

function wfLoadSaved() {
  try {
    const cached = JSON.parse(localStorage.getItem("spacebutler.workflows") || "null");
    wfState.saved = Array.isArray(cached) ? cached : [];
  } catch {
    wfState.saved = [];
  }
  wfState.activeIndex = wfState.saved.length ? 0 : -1;
  wfState.workflow = wfState.saved.length
    ? wfClone(wfState.saved[0])
    : { name: "新的工作流", phrase: "", runMode: "sequence", steps: [] };
}

/* ---- 初始化入口 ---- */
function initWorkflowView() {
  wfLoadSaved();
  wfRefreshDevices();

  // 导航按钮：点击切换到工作流视图
  $$("[data-view-target=\"workflow\"]").forEach((btn) => {
    btn.addEventListener("click", () => {
      switchView("workflow");
      wfRenderAll();
    });
  });

  // 工作流编排/执行 子视图切换
  $$(".workflow-heading-tabs button[data-workflow-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      wfSwitchPane(btn.dataset.workflowTab);
      wfRenderAll();
    });
  });

  // 按钮事件绑定
  $("#wf-save")?.addEventListener("click", wfSaveWorkflow);
  $("#wf-new")?.addEventListener("click", wfNewWorkflow);
  $("#wf-ai-generate")?.addEventListener("click", wfAiGenerate);
  $("#wf-ai-prompt")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); wfAiGenerate(); }
  });
  $("#wf-run-btn")?.addEventListener("click", wfRunWorkflow);
  $("#wf-voice-cmd")?.addEventListener("input", wfUpdateCommandMatch);
  $("#wf-name")?.addEventListener("input", wfSyncMeta);
  $("#wf-phrase")?.addEventListener("input", wfSyncMeta);
  $("#wf-run-mode")?.addEventListener("change", wfSyncMeta);

  // 添加设备按钮
  $("#wf-add-device")?.addEventListener("click", () => {
    const select = $("#wf-device-picker");
    if (!select) return;
    const device = wfDevice(select.value);
    const action = device?.actions[0];
    if (!device || !action) return;
    wfState.workflow.steps.push({
      deviceId: device.id, action: action.id,
      value: wfDefault(action), delay: 0,
    });
    wfRenderAll();
  });

  // 折叠侧边栏
  $("#wf-toggle-saved")?.addEventListener("click", () => {
    const sidebar = $("#wf-saved-sidebar");
    if (sidebar) {
      sidebar.classList.toggle("collapsed");
      const iconEl = document.querySelector("#wf-saved-sidebar .icon-button i");
      if (sidebar.classList.contains("collapsed")) {
        if (iconEl) iconEl.setAttribute("data-lucide", "panel-left-open");
      } else {
        if (iconEl) iconEl.setAttribute("data-lucide", "panel-left-close");
      }
      lucide.createIcons();
    }
  });

  wfRenderAll();
}
