const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const state = {
  status: null,
  devices: [],
  rooms: [],
  dashboard: null,
  favoriteDeviceIds: null,
  roomFilter: "all",
  view: "home",
  floorSelectedId: null,
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
    $("#temperature-source").textContent = "晴 · 空气优";
  }
  $("#household-device-status").textContent = "全屋已撤防";
  if (payload.night_safety) {
    const config = payload.night_safety.config || {};
    const room = (payload.night_safety.available_rooms || []).find((item) => item.room === config.origin_room);
    $("#household-member-status").textContent = `${config.member_name || "家庭成员"} · ${room?.name || config.origin_room || "家中"}`;
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

function renderPrimaryAction(device) {
  const action = device.ui?.primary_action;
  if (!action) {
    return `<button class="icon-button" data-favorite-open title="查看状态"><i data-lucide="search"></i></button>`;
  }
  return `
    <button class="power-button ${deviceIsActive(device) ? "on" : ""}"
      data-control-payload="${controlPayloadAttribute(action.payload)}" title="${escapeHtml(action.label)}">
      <i data-lucide="power"></i>
    </button>`;
}

function favoriteDeviceIcon(device) {
  const name = `${device.name || ""} ${device.ui?.name || ""}`.toLowerCase();
  if (device.type === "switch" && /(电视|tv|screen|display)/i.test(name)) return "monitor";
  if (device.type === "light" && /(阅读|台灯|desk)/i.test(name)) return "lamp-desk";
  return device.ui?.icon || "cpu";
}

function renderFavoriteControl(device) {
  const controls = device.ui?.controls || [];
  const range = controls.find((control) => control.kind === "range");
  const stepper = controls.find((control) => control.kind === "stepper");
  let detail = "";
  if (range) {
    detail = `
      <label class="favorite-range" title="${escapeHtml(range.label)}">
        <input type="range" min="${Number(range.min)}" max="${Number(range.max)}" step="${Number(range.step)}"
          value="${Number(range.value)}" data-control-range data-control-field="${escapeHtml(range.field)}"
          data-control-payload="${controlPayloadAttribute(range.payload)}">
      </label>`;
  } else if (stepper) {
    const value = Number(stepper.value);
    const step = Number(stepper.step);
    const minimum = Number(stepper.min);
    const maximum = Number(stepper.max);
    const lowerPayload = { ...(stepper.payload || {}), [stepper.field]: Math.max(minimum, value - step) };
    const upperPayload = { ...(stepper.payload || {}), [stepper.field]: Math.min(maximum, value + step) };
    detail = `
      <div class="favorite-temperature" aria-label="${escapeHtml(stepper.label)}">
        <button data-control-payload="${controlPayloadAttribute(lowerPayload)}" title="降低温度"><i data-lucide="minus"></i></button>
        <strong>${escapeHtml(value)}°C</strong>
        <button data-control-payload="${controlPayloadAttribute(upperPayload)}" title="升高温度"><i data-lucide="plus"></i></button>
      </div>`;
  }
  return `<div class="favorite-control">${renderPrimaryAction(device)}${detail}</div>`;
}

function renderFavoriteDevices() {
  const grid = $("#favorite-devices");
  if (!grid) return;
  const configuredIds = state.favoriteDeviceIds || state.dashboard?.favorite_device_ids || [];
  const devices = configuredIds
    .map((deviceId) => state.devices.find((item) => item.device_id === deviceId))
    .filter(Boolean);
  grid.innerHTML = devices.map((device) => {
    const icon = favoriteDeviceIcon(device);
    return `
      <article class="favorite-device" data-device-id="${escapeHtml(device.device_id)}">
        <span class="favorite-device-icon ${dashboardDeviceTone(device)}"><i data-lucide="${escapeHtml(icon)}"></i></span>
        <div class="favorite-device-copy">
          <strong>${escapeHtml(dashboardDeviceName(device))}</strong>
          <span>${escapeHtml(dashboardDeviceState(device))}</span>
        </div>
        ${renderFavoriteControl(device)}
      </article>`;
  }).join("");
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

function renderFloorplanRooms() {
  const roomLayer = $("#floorplan-rooms");
  if (!roomLayer) return;
  const rooms = state.dashboard?.rooms || [];
  roomLayer.innerHTML = rooms.map((room) => `
    <section class="floorplan-space" data-theme="${Number(room.theme || 0)}"
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
    return `
      <button class="floor-device ${dashboardDeviceTone(device)} ${selected ? "selected" : ""}"
        style="left:${position.left}%;top:${position.top}%" data-floor-device-id="${escapeHtml(device.device_id)}"
        data-floor-shape="${escapeHtml(floorShape)}"
        aria-label="${escapeHtml(dashboardDeviceName(device))}，${escapeHtml(dashboardDeviceState(device))}" aria-pressed="${selected}">
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
  renderFloorplan();
  refreshIcons();
}

function renderInventory(payload) {
  state.devices = payload.devices || [];
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
  $("#estimated-today-energy").textContent = text(state.dashboard.energy?.estimated_today_kwh, "--");
  $("#inventory-summary").textContent = `${state.devices.length} 台设备 · ${state.rooms.length} 个空间`;
  $("#new-device-type").innerHTML = (state.dashboard.device_types || []).map((item) => `
    <option value="${escapeHtml(item.value)}">${escapeHtml(item.label)}</option>`).join("");
  $("#room-options").innerHTML = state.rooms.map((room) => `<option value="${escapeHtml(room.room)}">${escapeHtml(room.name)}</option>`).join("");
  if (!$("#new-device-room").value && state.rooms[0]) $("#new-device-room").value = state.rooms[0].room;
  renderRoomTabs();
  const grid = $("#device-grid");
  if (!grid.contains(document.activeElement)) renderDeviceGrid();
  const floorplan = $("#floorplan-stage");
  if (!floorplan?.contains(document.activeElement)) renderSpatialDashboard();
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
  const meta = {
    label: device.ui?.type_label || "设备",
    icon: device.ui?.icon || "cpu",
    tone: device.ui?.tone || "blue",
  };
  const status = {
    primary: device.ui?.state_primary || (device.online ? "状态未知" : "设备离线"),
    secondary: device.ui?.state_secondary || "等待设备上报",
  };
  const discovered = device.discovered ? "HA 已发现" : "等待 HA";
  const runtime = device.runtime && device.runtime.isolation === "dedicated" ? device.runtime : null;
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
          <button class="icon-button favorite-toggle ${state.favoriteDeviceIds?.includes(device.device_id) ? "selected" : ""}"
            data-favorite-toggle title="${state.favoriteDeviceIds?.includes(device.device_id) ? "取消收藏" : "加入收藏"}">
            <i data-lucide="star"></i>
          </button>
          ${runtime ? `<span class="runtime-badge ${runtime.status === "running" ? "running" : "stopped"}" title="独立容器：${escapeHtml(runtime.container)}"><i data-lucide="container"></i></span>` : ""}
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
          ${faultOptions(device.fault_mode, device.ui?.fault_options || [])}
        </select>
      </label>
    </article>`;
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

function faultOptions(selected, options) {
  return options.map((option) => `
    <option value="${escapeHtml(option.value)}" ${selected === option.value ? "selected" : ""}>${escapeHtml(option.label)}</option>`).join("");
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
  await Promise.all([loadStatus(), loadDevices(), loadConversation(), loadAgentActivity()]);
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
  return payload;
}

function switchView(view) {
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
  selectFloorDevice(device.device_id);
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

async function addDevice(event) {
  event.preventDefault();
  const name = $("#new-device-name").value.trim();
  const type = $("#new-device-type").value;
  const room = $("#new-device-room").value;
  if (!name) return;
  $("#submit-add-device").disabled = true;
  try {
    const payload = await api("/api/devices", {
      method: "POST",
      body: JSON.stringify({ name, type, room }),
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
  $("#favorite-devices").addEventListener("click", handleFavoriteClick);
  $("#favorite-devices").addEventListener("change", handleDeviceChange);
  $("#room-status-grid").addEventListener("click", handleRoomStatusClick);
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

const WF_PRESETS = {
  home: {
    name: "回家模式", phrase: "我要到家了", runMode: "sequence",
    steps: [
      { deviceId: "curtain_living", action: "拉上", value: "关闭", delay: 0 },
      { deviceId: "ac_living", action: "调温", value: "26°C", delay: 0 },
      { deviceId: "light_living", action: "调亮", value: "60%", delay: 1 },
      { deviceId: "heater", action: "打开", value: "开启", delay: 5 },
    ],
  },
  movie: {
    name: "观影模式", phrase: "我要看电影", runMode: "sequence",
    steps: [
      { deviceId: "curtain_living", action: "拉上", value: "关闭", delay: 0 },
      { deviceId: "light_living", action: "调亮", value: "20%", delay: 0 },
      { deviceId: "ac_living", action: "调温", value: "25°C", delay: 0 },
      { deviceId: "tv_living", action: "打开", value: "开启", delay: 1 },
    ],
  },
  sleep: {
    name: "睡眠模式", phrase: "我要睡觉了", runMode: "sequence",
    steps: [
      { deviceId: "tv_living", action: "关闭", value: "关闭", delay: 0 },
      { deviceId: "light_living", action: "关闭", value: "关闭", delay: 0 },
      { deviceId: "light_bedroom", action: "调亮", value: "10%", delay: 1 },
      { deviceId: "ac_bedroom", action: "调温", value: "24°C", delay: 2 },
    ],
  },
  away: {
    name: "离家模式", phrase: "我要出门了", runMode: "sequence",
    steps: [
      { deviceId: "light_living", action: "关闭", value: "关闭", delay: 0 },
      { deviceId: "ac_living", action: "关闭", value: "关闭", delay: 0 },
      { deviceId: "tv_living", action: "关闭", value: "关闭", delay: 0 },
      { deviceId: "curtain_living", action: "拉上", value: "关闭", delay: 2 },
      { deviceId: "heater", action: "关闭", value: "关闭", delay: 0 },
    ],
  },
  energy: {
    name: "节能模式", phrase: "帮我省电", runMode: "sequence",
    steps: [
      { deviceId: "ac_living", action: "调温", value: "27°C", delay: 0 },
      { deviceId: "light_living", action: "调亮", value: "40%", delay: 0 },
      { deviceId: "purifier", action: "关闭", value: "关闭", delay: 0 },
    ],
  },
};

const WF_DEVICES = [
  { id: "curtain_living", name: "客厅窗帘", room: "客厅", type: "窗帘", state: "打开", actions: ["拉上", "打开", "调到"] },
  { id: "ac_living", name: "客厅空调", room: "客厅", type: "空调", state: "关闭", actions: ["打开", "关闭", "调温"] },
  { id: "light_living", name: "客厅主灯", room: "客厅", type: "灯光", state: "关闭", actions: ["打开", "关闭", "调亮"] },
  { id: "heater", name: "热水器", room: "浴室", type: "热水", state: "关闭", actions: ["打开", "关闭"] },
  { id: "purifier", name: "空气净化器", room: "客厅", type: "空气", state: "关闭", actions: ["打开", "关闭", "强档"] },
  { id: "tv_living", name: "客厅电视", room: "客厅", type: "影音", state: "关闭", actions: ["打开", "关闭"] },
  { id: "vacuum", name: "扫地机器人", room: "全屋", type: "清洁", state: "待机", actions: ["开始清扫", "回充"] },
  { id: "light_bedroom", name: "卧室灯", room: "卧室", type: "灯光", state: "关闭", actions: ["打开", "关闭", "调亮"] },
  { id: "ac_bedroom", name: "卧室空调", room: "卧室", type: "空调", state: "关闭", actions: ["打开", "关闭", "调温"] },
];

const wfState = {
  workflow: structuredClone(WF_PRESETS.home),
  saved: [],
  activeIndex: 0,
  draggedIndex: null,
  running: false,
  wfTab: "builder",
  aiGenBusy: false,
};

function wfClone(value) {
  return JSON.parse(JSON.stringify(value));
}

function wfDevice(id) {
  return WF_DEVICES.find((device) => device.id === id) || WF_DEVICES[0];
}

function wfDefault(action) {
  const map = { 调温: "26°C", 调亮: "60%", 调到: "50%", 拉上: "关闭", 强档: "高风量" };
  return map[action] || "开启";
}

/* ---- AI 生成工作流 ---- */
const WF_AI_TEMPLATES = {
  sleep: { name: "睡眠模式", phrase: "我要睡觉了", steps: [
    { deviceId: "tv_living", action: "关闭", value: "关闭", delay: 0 },
    { deviceId: "light_living", action: "关闭", value: "关闭", delay: 0 },
    { deviceId: "light_bedroom", action: "调亮", value: "10%", delay: 1 },
    { deviceId: "ac_bedroom", action: "调温", value: "24°C", delay: 2 },
  ]},
  away: { name: "离家模式", phrase: "我要出门了", steps: [
    { deviceId: "light_living", action: "关闭", value: "关闭", delay: 0 },
    { deviceId: "ac_living", action: "关闭", value: "关闭", delay: 0 },
    { deviceId: "tv_living", action: "关闭", value: "关闭", delay: 0 },
    { deviceId: "curtain_living", action: "拉上", value: "关闭", delay: 2 },
  ]},
  movie: { name: "观影模式", phrase: "我要看电影", steps: [
    { deviceId: "curtain_living", action: "拉上", value: "关闭", delay: 0 },
    { deviceId: "light_living", action: "调亮", value: "20%", delay: 0 },
    { deviceId: "ac_living", action: "调温", value: "25°C", delay: 0 },
    { deviceId: "tv_living", action: "打开", value: "开启", delay: 1 },
  ]},
  energy: { name: "节能模式", phrase: "帮我省电", steps: [
    { deviceId: "ac_living", action: "调温", value: "27°C", delay: 0 },
    { deviceId: "light_living", action: "调亮", value: "40%", delay: 0 },
  ]},
};

function wfAiGenerate() {
  if (wfState.aiGenBusy) return;
  const prompt = ($("#wf-ai-prompt")?.value || "").trim();
  if (!prompt) return;

  wfState.aiGenBusy = true;
  const btn = $("#wf-ai-generate");
  if (btn) { btn.disabled = true; btn.textContent = "分析中…"; }

  let matched = null;
  const lowered = prompt.toLowerCase();
  if (/睡|床|休息|晚安|晚上/.test(lowered)) matched = "sleep";
  else if (/出门|离家|不在家|外出|上班/.test(lowered)) matched = "away";
  else if (/电影|观影|看片|影院/.test(lowered)) matched = "movie";
  else if (/省电|节能|环保|省能源|绿色/.test(lowered)) matched = "energy";
  else {
    // 默认尝试匹配包含的语义
    if (/回家|到家|回来|进门/.test(lowered)) matched = "home";
    else if (/电影|观影/.test(lowered)) matched = "movie";
    else if (/睡/.test(lowered)) matched = "sleep";
    else matched = "home";
  }

  const template = WF_AI_TEMPLATES[matched] || WF_AI_TEMPLATES.home;
  const newWf = wfClone(template);
  newWf.phrase = prompt.length <= 30 ? prompt : prompt.slice(0, 28) + "…";

  // 模拟打字机效果填充步骤
  setTimeout(() => {
    wfState.workflow = newWf;
    $("#wf-name").value = newWf.name;
    $("#wf-phrase").value = newWf.phrase;
    wfRenderBuilder();
    wfRenderStatePreview();
    if (btn) { btn.disabled = false; btn.innerHTML = '<i data-lucide="wand-2"></i><span>生成</span>'; }
    wfState.aiGenBusy = false;
    lucide.createIcons();
  }, 500);
}

/* ---- 渲染 ---- */
function wfRenderBuilder() {
  const wf = wfState.workflow;
  if ($("#wf-name")) $("#wf-name").value = wf.name;
  if ($("#wf-phrase")) $("#wf-phrase").value = wf.phrase;
  if ($("#wf-run-mode")) $("#wf-run-mode").value = wf.runMode || "sequence";

  const container = $("#wf-steps");
  if (!container) return;
  if (!wf.steps.length) {
    container.innerHTML = `<div class="empty-workflow">暂未添加步骤 — 使用上方 AI 生成或手动添加设备</div>`;
    return;
  }
  container.innerHTML = wf.steps.map((step, index) => {
    const device = wfDevice(step.deviceId);
    return `
      <article class="workflow-step-card" draggable="true" data-wf-step="${index}">
        <div class="step-drag-handle" title="拖拽排序">::</div>
        <div>
          <div class="step-head-row">
            <div class="step-device-title">
              <span class="step-index-badge">${index + 1}</span>
              <span>${device.name}</span>
            </div>
            <button class="step-remove-btn" type="button" data-wf-remove="${index}">删除</button>
          </div>
          <div class="step-fields-row">
            <label><span>设备</span>
              <select data-wf-field="deviceId" data-wf-idx="${index}">
                ${WF_DEVICES.map((item) => `<option value="${item.id}" ${item.id === step.deviceId ? "selected" : ""}>${item.name}</option>`).join("")}
              </select>
            </label>
            <label><span>动作</span>
              <select data-wf-field="action" data-wf-idx="${index}">
                ${device.actions.map((action) => `<option value="${action}" ${action === step.action ? "selected" : ""}>${action}</option>`).join("")}
              </select>
            </label>
            <label><span>参数</span>
              <input data-wf-field="value" data-wf-idx="${index}" value="${step.value}">
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
        wfState.workflow.steps[idx].action = dev.actions[0];
        wfState.workflow.steps[idx].value = wfDefault(dev.actions[0]);
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
      <div><strong>${device.name}</strong><span>${device.room} · ${device.type}</span></div>
      <div class="state-val">${device.state}</div>
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
    const status = index <= doneIdx ? "done" : index === runningIdx ? "running" : "pending";
    const label = status === "done" ? "完成" : status === "running" ? "执行中" : "等待";
    return `
      <article class="timeline-entry ${status}">
        <div class="timeline-idx">${index + 1}</div>
        <div>
          <strong>${device.name}</strong>
          <span>${step.delay ? `延迟 ${step.delay} 分钟后 · ` : ""}${step.action} · ${step.value}</span>
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
      <div><strong>${device.name}</strong><span>${device.room} · ${device.type}</span></div>
      <div class="state-val">${device.state}</div>
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

function wfApplyStep(step) {
  const device = WF_DEVICES.find((item) => item.id === step.deviceId);
  if (!device) return;
  const action = step.action;
  if (action === "打开") device.state = step.value === "开启" ? "开启" : step.value;
  else if (action === "关闭") device.state = "关闭";
  else if (action === "拉上") device.state = "已拉上";
  else if (action === "调温") device.state = `运行 · ${step.value}`;
  else if (action === "调亮") device.state = `开启 · ${step.value}`;
  else if (action === "调到") device.state = step.value;
  else if (action === "强档") device.state = "运行 · 高风量";
  else if (action === "开始清扫") device.state = "清扫中";
  else if (action === "回充") device.state = "回充中";
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
  wfState.running = true;
  if ($("#wf-run-btn")) $("#wf-run-btn").disabled = true;
  if ($("#wf-exec-status")) $("#wf-exec-status").textContent = "执行中";

  // 重置所有设备状态
  WF_DEVICES.forEach((device) => { device.state = "关闭"; });
  wfRenderStateList();
  wfRenderReasoning(0);

  for (let i = 0; i < active.steps.length; i += 1) {
    wfRenderTimeline(i, i - 1);
    wfRenderReasoning(4); // 槽位→意图→环境→计划 完成
    await wfWait(500);
    wfRenderReasoning(5); // 安全校验
    await wfWait(400);
    wfRenderReasoning(6); // 工具执行
    wfApplyStep(active.steps[i]);
    wfRenderStateList(active.steps[i].deviceId, "executing");
    wfRenderTimeline(-1, i - 1);
    await wfWait(500);
    wfRenderReasoning(7); // 结果验证
    wfRenderStateList(active.steps[i].deviceId, "done");
    wfRenderTimeline(-1, i);
    await wfWait(300);
    // 反思
    wfRenderReasoning(8);
    await wfWait(200);
    // 下一步
    wfRenderReasoning(0);
  }

  wfRenderStateList();
  if ($("#wf-exec-status")) $("#wf-exec-status").textContent = "已完成";
  if ($("#wf-run-btn")) $("#wf-run-btn").disabled = false;

  // AI 反思评估
  const reflection = $("#wf-reflection");
  const reflectionText = $("#wf-reflection-text");
  if (reflection && reflectionText) {
    reflection.hidden = false;
    const closedDevices = active.steps.filter((step) => step.action === "关闭").length;
    const total = active.steps.length;
    reflectionText.textContent =
      `本次执行共 ${total} 个步骤，全部成功完成。` +
      `共关闭 ${closedDevices} 台非必要设备，预估节省待机功耗约 12W。` +
      `建议：可将"${active.phrase}"场景的空调温度从 26°C 调整至 25°C 以兼顾舒适度与能耗。`;
  }

  wfState.running = false;
}

function wfWait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/* ---- 工作流保存与加载 ---- */
function wfSaveWorkflow() {
  wfSyncMeta();
  const wf = wfState.workflow;
  const existing = wfState.saved.findIndex((item) => item.name === wf.name);
  if (existing >= 0) {
    wfState.activeIndex = existing;
    wfState.saved[existing] = wfClone(wf);
  } else {
    wfState.saved.unshift(wfClone(wf));
    wfState.activeIndex = 0;
  }
  try { localStorage.setItem("spacebutler.workflows", JSON.stringify(wfState.saved)); } catch {}
  wfRenderAll();
}

function wfSyncMeta() {
  wfState.workflow.name = ($("#wf-name")?.value || "").trim() || "未命名工作流";
  wfState.workflow.phrase = ($("#wf-phrase")?.value || "").trim() || "启动工作流";
  wfState.workflow.runMode = $("#wf-run-mode")?.value || "sequence";
}

function wfNewWorkflow() {
  wfState.workflow = {
    name: "新的工作流", phrase: "启动新的工作流",
    runMode: "sequence",
    steps: [{ deviceId: "light_living", action: "打开", value: "开启", delay: 0 }],
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
      <strong>${item.name}</strong>
      <span class="saved-meta">口令：${item.phrase} · ${item.steps.length} 个步骤</span>
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
    if (Array.isArray(cached) && cached.length) wfState.saved = cached;
    else wfState.saved = [
      wfClone(WF_PRESETS.home), wfClone(WF_PRESETS.movie), wfClone(WF_PRESETS.sleep),
      wfClone(WF_PRESETS.away), wfClone(WF_PRESETS.energy),
    ];
  } catch {
    wfState.saved = [
      wfClone(WF_PRESETS.home), wfClone(WF_PRESETS.movie), wfClone(WF_PRESETS.sleep),
    ];
  }
  wfState.activeIndex = 0;
  wfState.workflow = wfClone(wfState.saved[0]);
}

/* ---- 初始化入口 ---- */
function initWorkflowView() {
  wfLoadSaved();

  // 初始化设备选择器
  const devicePicker = $("#wf-device-picker");
  if (devicePicker) {
    devicePicker.innerHTML = WF_DEVICES.map((device) =>
      `<option value="${device.id}">${device.room} · ${device.name} · ${device.type}</option>`
    ).join("");
  }

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
    wfState.workflow.steps.push({
      deviceId: device.id, action: device.actions[0],
      value: wfDefault(device.actions[0]), delay: 0,
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
