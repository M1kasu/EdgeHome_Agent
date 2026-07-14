/*
 * EdgeHome 智能体演示工作台。
 *
 * 设计上沿用“对话输入 + 快捷场景 + 过程可见”的竞赛演示结构；所有结果均来自
 * Home Assistant Conversation WebSocket、sensor.edgehome_last_execution 和 MQTT 实体状态，
 * 不在前端预置任何执行结果或设备数据。
 */

const EDGEHOME_WORKBENCH_VERSION = "20260714-19";

const SHORTCUTS = [
  { label: "观影场景", prompt: "帮我准备客厅观影场景" },
  { label: "离家保护", prompt: "我要出门了，进入离家保护模式" },
  { label: "调暗主灯", prompt: "把客厅主灯调到四成" },
  { label: "窗帘控制", prompt: "把客厅窗帘拉到三分之一" },
  { label: "能耗查询", prompt: "查询客厅当前能耗" },
  { label: "设备巡检", prompt: "检查客厅设备现在的状态" },
];

const DEVICE_META = [
  { entityId: "light.edgehome_living_room_main", label: "客厅主灯", icon: "mdi:ceiling-light" },
  { entityId: "cover.edgehome_living_room_curtain", label: "客厅窗帘", icon: "mdi:curtains" },
  { entityId: "climate.edgehome_living_room_ac", label: "客厅空调", icon: "mdi:air-conditioner" },
  { entityId: "switch.edgehome_living_room_tv", label: "客厅电视", icon: "mdi:television-classic" },
];

const ENTITY_LABELS = {
  "light.edgehome_living_room_main": "客厅主灯",
  "cover.edgehome_living_room_curtain": "客厅窗帘",
  "climate.edgehome_living_room_ac": "客厅空调",
  "switch.edgehome_living_room_tv": "客厅电视",
  "switch.edgehome_router": "家庭路由器",
  "switch.edgehome_refrigerator": "冰箱",
};

const TOOL_LABELS = {
  get_room_state: "读取家庭状态",
  get_device_state: "读取设备状态",
  set_light_brightness: "设置灯光亮度",
  set_temperature: "设置空调温度",
  "light.turn_on": "设置灯光",
  "light.turn_off": "关闭灯光",
  "cover.open_cover": "打开窗帘",
  "cover.close_cover": "关闭窗帘",
  "cover.set_cover_position": "设置窗帘位置",
  "climate.set_temperature": "设置空调温度",
  "climate.set_hvac_mode": "设置空调模式",
  "switch.turn_on": "开启设备",
  "switch.turn_off": "关闭设备",
};

const LOCAL_READ_TOOLS = new Set(["get_room_state", "get_device_state"]);

const TRACE_STATUS = {
  success: { label: "已完成并验证", className: "ok", icon: "mdi:check-circle" },
  partial_success: { label: "部分完成", className: "warn", icon: "mdi:alert-circle" },
  failed: { label: "执行未完成", className: "error", icon: "mdi:close-circle" },
  needs_replan: { label: "需要重新规划", className: "warn", icon: "mdi:source-branch-sync" },
  handled: { label: "已处理", className: "info", icon: "mdi:check-circle-outline" },
  unknown: { label: "等待指令", className: "muted", icon: "mdi:clock-outline" },
  unavailable: { label: "记录不可用", className: "error", icon: "mdi:cloud-alert" },
};

// Home Assistant 在实体状态变化后可能重新创建 panel_custom 实例。将演示会话提升到
// 模块级别，使一次真实请求在面板刷新前后仍可持续展示，而不写入浏览器存储。
const WORKBENCH_SESSION = {
  conversationId: "",
  agentId: "",
  messages: [],
  processing: false,
};
let activeWorkbench = null;

function escapeHtml(value) {
  return String(value ?? "—")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function numberOrNull(value) {
  const result = Number(value);
  return Number.isFinite(result) ? result : null;
}

function stateText(state) {
  const labels = {
    on: "开启",
    off: "关闭",
    open: "打开",
    closed: "关闭",
    opening: "开启中",
    closing: "关闭中",
    heat: "制热",
    cool: "制冷",
    heat_cool: "自动冷暖",
    dry: "除湿",
    fan_only: "送风",
    unavailable: "离线",
    unknown: "未知",
  };
  return labels[state] || String(state || "未知");
}

function dateText(value) {
  if (!value || value === "—") return "等待新的请求";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString("zh-CN", { hour12: false });
}

function briefId(value) {
  if (!value) return "新会话";
  return value.length > 18 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
}

function isRawToolPayload(value) {
  const text = String(value || "").trim();
  if (!text) return false;
  return (
    /(?:HassCallService|function_call|tool_calls|target_device)/i.test(text) &&
    (/^```(?:json)?\s*[\[{]/i.test(text) || /^[\[{]/.test(text))
  );
}

function conversationErrorDetail(error) {
  const candidates = [
    error?.message,
    error?.error?.message,
    error?.data?.message,
    error?.error?.data?.message,
    error?.code,
    error?.error?.code,
    typeof error === "string" ? error : "",
  ];
  return candidates.find((candidate) => typeof candidate === "string" && candidate.trim()) || "";
}

class EdgeHomeExecutionWorkbench extends HTMLElement {
  connectedCallback() {
    activeWorkbench = this;
    if (!WORKBENCH_SESSION.messages.length) {
      WORKBENCH_SESSION.messages = [this._introMessage()];
    }
    this._render();
  }

  disconnectedCallback() {
    if (activeWorkbench === this) activeWorkbench = null;
  }

  _introMessage() {
    return {
      role: "assistant",
      intro: true,
      content: "我是 EdgeHome 家居智能体。输入自然语言指令，右侧会同步展示本轮的决策、计划、MQTT 回读和设备状态。",
    };
  }

  _refreshActiveWorkbench() {
    (activeWorkbench || this)._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _entity(entityId) {
    return this._hass?.states?.[entityId] || null;
  }

  _attribute(entity, name, fallback = null) {
    return entity?.attributes?.[name] ?? fallback;
  }

  _trace() {
    return this._entity("sensor.edgehome_last_execution");
  }

  _agentEntityId() {
    const entities = Object.keys(this._hass?.states || {});
    // 仅用于界面展示；不能直接作为 conversation/process 的 agent_id。
    return entities.find((entityId) => entityId.startsWith("conversation.") && entityId.includes("edgehome")) || undefined;
  }

  async _resolveAgentId() {
    if (WORKBENCH_SESSION.agentId) return WORKBENCH_SESSION.agentId;
    const response = await this._hass.callWS({
      type: "conversation/agent/list",
      language: this._hass.language || "zh-Hans",
    });
    const agent = asArray(response?.agents).find(
      (candidate) => typeof candidate?.id === "string" && candidate.id.startsWith("conversation.") && candidate.id.includes("edgehome"),
    );
    if (!agent?.id) {
      throw new Error("未发现可用的 EdgeHome 对话代理，未发送本轮请求。");
    }
    WORKBENCH_SESSION.agentId = agent.id;
    return agent.id;
  }

  _appendMessage(message) {
    WORKBENCH_SESSION.messages = [...WORKBENCH_SESSION.messages, message];
  }

  _replyText(response) {
    const candidate =
      response?.response?.speech?.plain?.speech ||
      response?.response?.card?.simple?.content ||
      "";
    if (!isRawToolPayload(candidate)) {
      return candidate || "智能体已处理请求，但没有返回可展示的文本。";
    }
    return this._traceSummaryForToolPayload();
  }

  _traceSummaryForToolPayload() {
    const trace = this._trace();
    const speech = this._attribute(trace, "speech", "");
    if (speech && !isRawToolPayload(speech)) return speech;

    const results = asArray(this._attribute(trace, "step_results", []));
    if (!results.length) return "已生成受限设备执行计划，详细状态请查看右侧工作台。";
    const failed = results.filter((result) => result?.verified === false);
    if (failed.length) return `已生成执行计划，但有 ${failed.length} 个步骤未通过设备状态回读。`;
    const waiting = results.filter((result) => result?.verified === null || result?.verified === undefined);
    if (waiting.length) return `已生成执行计划，正在等待 ${waiting.length} 个设备步骤的 MQTT 状态回读。`;
    return "设备执行计划已完成，并已通过 MQTT 状态回读验证。";
  }

  async _sendPrompt(rawPrompt) {
    const prompt = String(rawPrompt || "").trim();
    if (!prompt || WORKBENCH_SESSION.processing) return;
    if (!this._hass?.callWS) {
      this._appendMessage({ role: "assistant", error: true, content: "当前页面未连接到 Home Assistant 对话服务。" });
      this._refreshActiveWorkbench();
      return;
    }

    WORKBENCH_SESSION.processing = true;
    this._appendMessage({ role: "user", content: prompt });
    this._appendMessage({ role: "assistant", pending: true, content: "EdgeHome 正在理解指令并等待设备回读…" });
    this._refreshActiveWorkbench();

    try {
      const request = {
        type: "conversation/process",
        text: prompt,
        language: this._hass.language || "zh-Hans",
      };
      if (WORKBENCH_SESSION.conversationId) request.conversation_id = WORKBENCH_SESSION.conversationId;
      request.agent_id = await this._resolveAgentId();

      const response = await this._hass.callWS(request);
      WORKBENCH_SESSION.conversationId = response?.conversation_id || WORKBENCH_SESSION.conversationId;
      WORKBENCH_SESSION.messages = WORKBENCH_SESSION.messages.slice(0, -1);
      this._appendMessage({ role: "assistant", content: this._replyText(response) });
    } catch (error) {
      WORKBENCH_SESSION.messages = WORKBENCH_SESSION.messages.slice(0, -1);
      const detail = conversationErrorDetail(error);
      const explanation = detail
        ? `失败原因：${detail}`
        : "对话服务未返回详细原因；本轮未确认任何设备操作成功。";
      this._appendMessage({ role: "assistant", error: true, content: `本轮请求未能完成。${explanation}` });
    } finally {
      WORKBENCH_SESSION.processing = false;
      this._refreshActiveWorkbench();
    }
  }

  _resetConversation() {
    if (WORKBENCH_SESSION.processing) return;
    WORKBENCH_SESSION.conversationId = "";
    WORKBENCH_SESSION.messages = [
      {
        role: "assistant",
        intro: true,
        content: "已开始新的面板会话。新的请求不会继承上一轮设备控制上下文。",
      },
    ];
    this._refreshActiveWorkbench();
  }

  _initialMqttDevicesRestored() {
    const light = this._entity("light.edgehome_living_room_main");
    const curtain = this._entity("cover.edgehome_living_room_curtain");
    const ac = this._entity("climate.edgehome_living_room_ac");
    const tv = this._entity("switch.edgehome_living_room_tv");
    const brightness = numberOrNull(this._attribute(light, "brightness"));
    const curtainPosition = numberOrNull(this._attribute(curtain, "current_position"));
    const temperature = numberOrNull(this._attribute(ac, "temperature"));

    // 与 device_simulator/config/devices.yaml 的 M1--M4 初始状态一致。
    // 前端只依据 HA 已回读的 MQTT 实体判定成功，不把“已发出脚本”误报为恢复完成。
    return (
      light?.state === "on" &&
      brightness === 204 &&
      curtain?.state === "open" &&
      curtainPosition === 100 &&
      ac?.state === "cool" &&
      temperature === 24 &&
      tv?.state === "off"
    );
  }

  async _waitForInitialMqttDeviceStates(timeoutMs = 6000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (this._initialMqttDevicesRestored()) return true;
      await new Promise((resolve) => window.setTimeout(resolve, 250));
    }
    return this._initialMqttDevicesRestored();
  }

  async _restoreInitialState() {
    if (WORKBENCH_SESSION.processing) return;
    if (!this._hass?.callService) {
      this._appendMessage({
        role: "assistant",
        error: true,
        content: "无法恢复初始状态：当前页面未连接到 Home Assistant 服务调用接口。",
      });
      this._refreshActiveWorkbench();
      return;
    }

    WORKBENCH_SESSION.processing = true;
    this._appendMessage({ role: "user", content: "恢复初始状态" });
    this._appendMessage({
      role: "assistant",
      pending: true,
      content: "正在恢复演示基线：重置环境条件，并等待 M1--M4 MQTT 设备重新上报状态…",
    });
    this._refreshActiveWorkbench();

    try {
      await this._hass.callService("script", "turn_on", {
        entity_id: "script.edgehome_reset_demo_environment",
      });
      const restored = await this._waitForInitialMqttDeviceStates();
      if (!restored) {
        throw new Error(
          "MQTT 设备未在 6 秒内全部回到初始状态；未将本次恢复标记为成功。请检查 device-simulator 与 Mosquitto 是否在线。",
        );
      }

      WORKBENCH_SESSION.conversationId = "";
      WORKBENCH_SESSION.messages = [
        {
          role: "assistant",
          intro: true,
          content:
            "初始状态已恢复：主灯 80%、窗帘打开、空调制冷 24°C、电视关闭；环境条件与安全设备也已复位。已同时新建会话，不继承之前的控制上下文。",
        },
      ];
    } catch (error) {
      WORKBENCH_SESSION.messages = WORKBENCH_SESSION.messages.slice(0, -1);
      const detail = conversationErrorDetail(error) || "Home Assistant 未返回详细原因。";
      this._appendMessage({ role: "assistant", error: true, content: `恢复初始状态未完成。失败原因：${detail}` });
    } finally {
      WORKBENCH_SESSION.processing = false;
      this._refreshActiveWorkbench();
    }
  }

  _messageList() {
    return WORKBENCH_SESSION.messages
      .map((message) => {
        const kind = message.role === "user" ? "user" : message.error ? "error" : "assistant";
        const label = message.role === "user" ? "你" : message.intro ? "EdgeHome 引导" : "EdgeHome";
        const pending = message.pending ? '<span class="pending-dot" aria-label="处理中"></span>' : "";
        // 会话状态可能来自页面热更新前的旧组件；渲染时再次过滤，确保历史气泡也不泄露工具 JSON。
        const content = isRawToolPayload(message.content)
          ? this._traceSummaryForToolPayload()
          : message.error && message.content === "本轮请求未能完成。"
            ? "本轮请求未能完成。该历史记录没有保存后端原因；未确认任何设备操作成功。"
            : message.content;
        return `<div class="message-row ${kind}">
          <span class="message-label">${escapeHtml(label)}</span>
          <div class="message-bubble">${pending}${escapeHtml(content)}</div>
        </div>`;
      })
      .join("");
  }

  _deviceDetail(entityId) {
    const entity = this._entity(entityId);
    const state = entity?.state || "unavailable";
    if (entityId.includes("living_room_main")) {
      const brightness = numberOrNull(this._attribute(entity, "brightness"));
      return state === "on" && brightness !== null ? `亮度 ${Math.round((brightness / 255) * 100)}%` : stateText(state);
    }
    if (entityId.includes("curtain")) {
      const position = numberOrNull(this._attribute(entity, "current_position"));
      return position === null ? stateText(state) : `${position}% · ${stateText(state)}`;
    }
    if (entityId.includes("_ac")) {
      const target = numberOrNull(this._attribute(entity, "temperature"));
      const current = numberOrNull(this._attribute(entity, "current_temperature"));
      if (state === "off") return "已关闭";
      return target === null ? stateText(state) : `${target}°C${current === null ? "" : ` / 当前 ${current}°C`}`;
    }
    return stateText(state);
  }

  _deviceRows() {
    return DEVICE_META.map(({ entityId, label, icon }) => {
      const entity = this._entity(entityId);
      const state = entity?.state || "unavailable";
      const online = state !== "unavailable" && state !== "unknown";
      return `<div class="device-row">
        <ha-icon icon="${icon}"></ha-icon>
        <span>${escapeHtml(label)}</span>
        <small>${escapeHtml(this._deviceDetail(entityId))}</small>
        <i class="state-dot ${online ? "online" : "offline"}" title="${online ? "设备在线" : "设备不可用"}"></i>
      </div>`;
    }).join("");
  }

  _planRows(steps) {
    if (!steps.length) return '<div class="empty-copy">本轮没有生成可执行的设备计划。</div>';
    return steps
      .map((step, index) => {
        const target = ENTITY_LABELS[step?.entity_id] || step?.entity_id || "本地处理";
        const compilerPreflight = String(step?.step_id || "").startsWith("preflight_");
        const tool = compilerPreflight ? "安全预读（编译器）" : (TOOL_LABELS[step?.tool] || step?.tool || "受限处理");
        const dependencies = asArray(step?.depends_on);
        const argumentText = Object.entries(step?.arguments || {})
          .filter(([key]) => key !== "entity_id")
          .map(([key, value]) => `${key}=${typeof value === "object" ? JSON.stringify(value) : value}`)
          .join("，");
        const dependencyText = dependencies.length ? `依赖：${dependencies.join(" → ")}` : "起始步骤";
        const detail = [target, compilerPreflight ? "控制前强制读取同一设备状态" : dependencyText, argumentText].filter(Boolean).join(" · ");
        return `<div class="list-row">
          <b>${index + 1}</b><span><strong>${escapeHtml(tool)}</strong><small>${escapeHtml(detail)}</small></span>
        </div>`;
      })
      .join("");
  }

  _constraintRows(constraints, rationale, repairReason) {
    const rationaleRow = rationale
      ? `<p class="decision-copy">${escapeHtml(rationale)}</p>`
      : '<div class="empty-copy">本轮未提供额外规划说明。</div>';
    const constraintRows = constraints.length
      ? constraints
        .map((constraint) => `<div class="constraint-row"><ha-icon icon="${constraint?.hard === false ? "mdi:information-outline" : "mdi:shield-check-outline"}"></ha-icon><span>${escapeHtml(constraint?.statement || "未命名约束")}</span><em>${escapeHtml(constraint?.source === "safety" ? "安全" : constraint?.source === "environment" ? "环境" : "用户")}</em></div>`)
        .join("")
      : '<div class="empty-copy">未提取到需要额外执行的显式约束。</div>';
    const repair = repairReason ? `<p class="repair-copy">模型计划曾被拦截并修复：${escapeHtml(repairReason)}</p>` : "";
    return `${rationaleRow}<div class="constraint-list">${constraintRows}</div>${repair}`;
  }

  _resultRows(results) {
    if (!results.length) return '<div class="empty-copy">本轮未发送 MQTT 命令；本地查询无需设备回读。</div>';
    return results
      .map((result) => {
        const verified = result?.verified === true;
        const pending = result?.verified === null || result?.verified === undefined;
        const target = ENTITY_LABELS[result?.entity_id] || result?.entity_id || result?.tool || "执行步骤";
        const outcome = pending ? "等待回读" : verified ? "已验证" : "未通过";
        const className = pending ? "pending" : verified ? "verified" : "failed";
        const before = stateText(result?.state_before);
        const after = stateText(result?.state_after);
        const duration = numberOrNull(result?.duration_ms);
        const error = result?.error ? `<small class="error-copy">${escapeHtml(result.error)}</small>` : "";
        return `<div class="result-row ${className}">
          <ha-icon icon="${verified ? "mdi:check-circle" : pending ? "mdi:clock-outline" : "mdi:alert-circle"}"></ha-icon>
          <span><strong>${escapeHtml(target)}</strong><small>${escapeHtml(before)} → ${escapeHtml(after)}${duration === null ? "" : ` · ${duration} ms`}</small>${error}</span>
          <em>${outcome}</em>
        </div>`;
      })
      .join("");
  }

  _environmentRows() {
    const metrics = [
      { entityId: "sensor.edgehome_living_room_temperature", label: "客厅温度", icon: "mdi:thermometer" },
      { entityId: "sensor.edgehome_living_room_illuminance", label: "环境光照", icon: "mdi:weather-sunny" },
      { entityId: "sensor.edgehome_living_room_tv_power", label: "电视功耗", icon: "mdi:flash-outline" },
    ];
    return metrics
      .map(({ entityId, label, icon }) => {
        const entity = this._entity(entityId);
        const unit = this._attribute(entity, "unit_of_measurement", "");
        const value = entity?.state && entity.state !== "unavailable" ? `${entity.state}${unit}` : "不可用";
        return `<div class="metric-cell"><ha-icon icon="${icon}"></ha-icon><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
      })
      .join("");
  }

  _protectedDevice(entityId, icon) {
    const entity = this._entity(entityId);
    const safe = entity?.state === "on";
    return `<div class="safety-device ${safe ? "safe" : "attention"}">
      <ha-icon icon="${icon}"></ha-icon><span>${escapeHtml(ENTITY_LABELS[entityId])}</span><strong>${safe ? "受保护 · 开启" : stateText(entity?.state)}</strong>
    </div>`;
  }

  _render() {
    if (!this.isConnected) return;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });

    const trace = this._trace();
    const traceState = trace?.state || "unknown";
    const status = TRACE_STATUS[traceState] || TRACE_STATUS.handled;
    const steps = asArray(this._attribute(trace, "plan_steps", []));
    const results = asArray(this._attribute(trace, "step_results", []));
    // 仅设备实体动作属于 MQTT 回读；读取家庭状态属于本地规划前置步骤，不能被误标为“等待设备回读”。
    const mqttResults = results.filter((result) => Boolean(result?.entity_id) && !LOCAL_READ_TOOLS.has(result?.tool));
    const verifiedCount = mqttResults.filter((result) => result?.verified === true).length;
    const route = this._attribute(trace, "route_label", "等待第一次请求");
    const decision = this._attribute(trace, "decision", "—");
    const planningSource = this._attribute(trace, "planning_source_label", "未触发模型规划");
    const planningAttempts = numberOrNull(this._attribute(trace, "planning_attempts", 0)) || 0;
    const planningRationale = this._attribute(trace, "planning_rationale", "");
    const planningConstraints = asArray(this._attribute(trace, "planning_constraints", []));
    const planningRepairReason = this._attribute(trace, "planning_repair_reason", "");
    const goal = this._attribute(trace, "goal", "—");
    const speech = this._attribute(trace, "speech", "发起请求后，这里会保留智能体的真实回复。");
    const updatedAt = this._attribute(trace, "updated_at", null);
    const agentId = WORKBENCH_SESSION.agentId || this._agentEntityId();
    const processing = WORKBENCH_SESSION.processing ? "disabled" : "";

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; min-height: 100%; color: var(--primary-text-color, #1f2937); font-family: Roboto, "Noto Sans SC", "Microsoft YaHei", sans-serif; }
        * { box-sizing: border-box; }
        button, textarea { font: inherit; }
        .workbench { max-width: 1480px; margin: 0 auto; padding: 24px; }
        .header { display: flex; justify-content: space-between; gap: 20px; align-items: flex-start; margin-bottom: 18px; }
        .brand { display: flex; align-items: flex-start; gap: 13px; }
        .brand-mark { display: grid; width: 46px; height: 46px; place-items: center; border-radius: 14px; color: var(--primary-color, #03a9f4); background: color-mix(in srgb, var(--primary-color, #03a9f4) 10%, var(--card-background-color, #fff)); }
        .brand-mark ha-icon { --mdc-icon-size: 27px; }
        h1 { margin: 0; color: var(--primary-text-color, #182230); font-size: clamp(24px, 3vw, 31px); line-height: 1.2; letter-spacing: -.025em; }
        .subtitle { margin: 6px 0 0; color: var(--secondary-text-color, #64748b); font-size: 13px; line-height: 1.55; }
        .status-badge { display: flex; align-items: center; gap: 7px; padding: 9px 12px; border-radius: 999px; color: var(--secondary-text-color, #64748b); background: var(--card-background-color, #fff); box-shadow: var(--ha-card-box-shadow, 0 1px 3px rgba(0,0,0,.12)); font-size: 12px; white-space: nowrap; }
        .status-badge ha-icon { --mdc-icon-size: 18px; }
        .status-badge.ok { color: var(--success-color, #16805c); } .status-badge.warn { color: var(--warning-color, #b26a00); } .status-badge.error { color: var(--error-color, #c62828); } .status-badge.info { color: var(--primary-color, #0369a1); }
        .layout { display: grid; grid-template-columns: minmax(0, 1.22fr) minmax(355px, .78fr); gap: 16px; align-items: start; }
        .left-column, .right-column { display: grid; gap: 16px; }
        .card { overflow: hidden; border-radius: var(--ha-card-border-radius, 18px); background: var(--card-background-color, #fff); box-shadow: var(--ha-card-box-shadow, 0 1px 3px rgba(0,0,0,.12)); }
        .card-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 16px 18px 12px; }
        .card-title { display: flex; align-items: center; gap: 8px; color: var(--primary-color, #03a9f4); font-size: 15px; font-weight: 700; }
        .card-title ha-icon { --mdc-icon-size: 20px; }
        .card-note { color: var(--secondary-text-color, #64748b); font-size: 11px; }
        .conversation-card { min-height: 510px; display: grid; grid-template-rows: auto 1fr auto; }
        .messages { display: flex; min-height: 315px; max-height: 465px; flex-direction: column; gap: 13px; overflow: auto; padding: 8px 18px 18px; background: color-mix(in srgb, var(--secondary-background-color, #f6f8fb) 78%, var(--card-background-color, #fff)); }
        .message-row { display: flex; flex-direction: column; gap: 5px; max-width: 82%; }
        .message-row.user { align-self: flex-end; align-items: flex-end; }
        .message-label { color: var(--secondary-text-color, #64748b); font-size: 11px; font-weight: 700; }
        .message-bubble { position: relative; padding: 11px 13px; border-radius: 12px; color: var(--primary-text-color, #1f2937); background: var(--card-background-color, #fff); box-shadow: 0 1px 2px rgba(25, 40, 65, .07); font-size: 14px; line-height: 1.6; white-space: pre-wrap; }
        .message-row.user .message-bubble { color: var(--text-primary-color, #fff); background: var(--primary-color, #03a9f4); }
        .message-row.error .message-bubble { color: var(--error-color, #c62828); background: color-mix(in srgb, var(--error-color, #c62828) 8%, var(--card-background-color, #fff)); }
        .pending-dot { display: inline-block; width: 8px; height: 8px; margin-right: 8px; border-radius: 50%; background: var(--primary-color, #03a9f4); animation: pulse 1.1s infinite ease-in-out; }
        @keyframes pulse { 0%,100% { opacity: .35; transform: scale(.8); } 50% { opacity: 1; transform: scale(1); } }
        .composer { padding: 14px 18px 18px; border-top: 1px solid var(--divider-color, #e2e8f0); }
        .composer-row { display: flex; align-items: flex-end; gap: 9px; }
        textarea { width: 100%; min-height: 43px; max-height: 110px; resize: vertical; padding: 10px 12px; border: 1px solid var(--divider-color, #d7dde7); border-radius: 10px; color: var(--primary-text-color, #1f2937); outline: none; background: var(--card-background-color, #fff); font-size: 13px; line-height: 1.45; }
        textarea:focus { border-color: var(--primary-color, #03a9f4); box-shadow: 0 0 0 2px color-mix(in srgb, var(--primary-color, #03a9f4) 18%, transparent); }
        .icon-button, .send-button, .shortcut, .restore-demo { border: 0; cursor: pointer; }
        .icon-button { display: grid; width: 43px; height: 43px; flex: 0 0 auto; place-items: center; border-radius: 10px; color: var(--secondary-text-color, #64748b); background: var(--secondary-background-color, #f1f5f9); }
        .icon-button ha-icon { --mdc-icon-size: 20px; }
        .send-button { display: inline-flex; height: 43px; align-items: center; gap: 6px; padding: 0 13px; border-radius: 10px; color: var(--text-primary-color, #fff); background: var(--primary-color, #03a9f4); font-size: 13px; font-weight: 700; }
        .send-button ha-icon { --mdc-icon-size: 18px; }
        button:hover:not(:disabled) { filter: brightness(.96); } button:disabled { cursor: not-allowed; opacity: .55; }
        .shortcut-area { padding: 0 18px 18px; }
        .shortcut-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin: 0 0 9px; }
        .section-label { color: var(--secondary-text-color, #64748b); font-size: 12px; font-weight: 700; }
        .restore-demo { display: inline-flex; align-items: center; gap: 5px; min-height: 29px; padding: 5px 9px; border-radius: 8px; color: var(--secondary-text-color, #58677b); background: var(--secondary-background-color, #f1f5f9); font-size: 11px; font-weight: 700; }
        .restore-demo ha-icon { --mdc-icon-size: 15px; }
        .shortcut-grid { display: flex; flex-wrap: wrap; gap: 8px; }
        .shortcut { padding: 8px 10px; border-radius: 9px; color: var(--primary-color, #0277bd); background: color-mix(in srgb, var(--primary-color, #03a9f4) 9%, var(--card-background-color, #fff)); font-size: 12px; }
        .process-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
        .process-card { padding: 15px; }
        .process-card h3 { display: flex; align-items: center; gap: 7px; margin: 0 0 11px; color: var(--primary-color, #03a9f4); font-size: 14px; }
        .process-card h3 ha-icon { --mdc-icon-size: 18px; }
        .kv { display: grid; gap: 8px; }
        .kv div { display: grid; grid-template-columns: 72px 1fr; gap: 8px; align-items: start; }
        .kv span { color: var(--secondary-text-color, #64748b); font-size: 11px; } .kv strong { color: var(--primary-text-color, #1f2937); font-size: 12px; font-weight: 600; line-height: 1.45; overflow-wrap: anywhere; }
        .panel-body { padding: 0 18px 18px; }
        .decision-copy { margin: 0; padding: 11px 12px; border-left: 3px solid var(--primary-color, #03a9f4); border-radius: 0 8px 8px 0; color: var(--secondary-text-color, #58677b); background: var(--secondary-background-color, #f6f8fb); font-size: 12px; line-height: 1.55; white-space: pre-wrap; }
        .list-row, .result-row, .device-row { display: flex; gap: 9px; align-items: center; padding: 10px 0; border-top: 1px solid var(--divider-color, #e5eaf1); }
        .list-row:first-child, .result-row:first-child, .device-row:first-child { border-top: 0; }
        .list-row > b { display: grid; width: 22px; height: 22px; flex: 0 0 auto; place-items: center; border-radius: 50%; color: var(--primary-color, #0277bd); background: color-mix(in srgb, var(--primary-color, #03a9f4) 11%, transparent); font-size: 11px; }
        .list-row span, .result-row span { min-width: 0; flex: 1; } .list-row strong, .result-row strong { display: block; color: var(--primary-text-color, #1f2937); font-size: 12px; } .list-row small, .result-row small { display: block; margin-top: 3px; color: var(--secondary-text-color, #64748b); font-size: 11px; }
        .result-row ha-icon { --mdc-icon-size: 19px; } .result-row.verified ha-icon { color: var(--success-color, #16805c); } .result-row.pending ha-icon { color: var(--warning-color, #b26a00); } .result-row.failed ha-icon { color: var(--error-color, #c62828); }
        .result-row em { color: var(--secondary-text-color, #64748b); font-size: 10px; font-style: normal; white-space: nowrap; } .error-copy { color: var(--error-color, #c62828) !important; }
        .constraint-list { margin-top: 10px; }
        .constraint-row { display: flex; align-items: center; gap: 8px; padding: 9px 0; border-top: 1px solid var(--divider-color, #e5eaf1); }
        .constraint-row ha-icon { color: var(--primary-color, #03a9f4); --mdc-icon-size: 17px; } .constraint-row span { flex: 1; color: var(--primary-text-color, #1f2937); font-size: 12px; line-height: 1.45; } .constraint-row em { color: var(--secondary-text-color, #64748b); font-size: 10px; font-style: normal; }
        .repair-copy { margin: 10px 0 0; color: var(--warning-color, #b26a00); font-size: 11px; line-height: 1.5; }
        .empty-copy { padding: 8px 0 3px; color: var(--secondary-text-color, #64748b); font-size: 12px; line-height: 1.55; }
        .device-row > ha-icon { color: var(--primary-color, #03a9f4); --mdc-icon-size: 19px; } .device-row > span { min-width: 0; flex: 1; color: var(--primary-text-color, #1f2937); font-size: 12px; font-weight: 600; } .device-row > small { color: var(--secondary-text-color, #64748b); font-size: 11px; text-align: right; }
        .state-dot { width: 8px; height: 8px; flex: 0 0 auto; border-radius: 50%; } .state-dot.online { background: var(--success-color, #16805c); } .state-dot.offline { background: var(--error-color, #c62828); }
        .metric-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
        .metric-cell { padding: 10px; border-radius: 10px; background: var(--secondary-background-color, #f6f8fb); } .metric-cell ha-icon { color: var(--primary-color, #03a9f4); --mdc-icon-size: 18px; } .metric-cell span, .metric-cell strong { display: block; } .metric-cell span { margin-top: 7px; color: var(--secondary-text-color, #64748b); font-size: 10px; } .metric-cell strong { margin-top: 3px; color: var(--primary-text-color, #1f2937); font-size: 13px; overflow-wrap: anywhere; }
        .safety-device { display: flex; align-items: center; gap: 8px; padding: 9px 0; border-top: 1px solid var(--divider-color, #e5eaf1); } .safety-device:first-child { border-top: 0; } .safety-device ha-icon { color: var(--primary-color, #03a9f4); --mdc-icon-size: 19px; } .safety-device span { flex: 1; color: var(--primary-text-color, #1f2937); font-size: 12px; font-weight: 600; } .safety-device strong { color: var(--success-color, #16805c); font-size: 11px; } .safety-device.attention strong { color: var(--warning-color, #b26a00); }
        .boundary-note { margin: 10px 0 0; color: var(--secondary-text-color, #64748b); font-size: 11px; line-height: 1.55; }
        .footer { margin-top: 14px; color: var(--secondary-text-color, #64748b); font-size: 10px; text-align: right; }
        @media (max-width: 980px) { .layout { grid-template-columns: 1fr; } .right-column { grid-template-columns: repeat(2, minmax(0, 1fr)); align-items: start; } .right-column > .card:nth-child(1), .right-column > .card:nth-child(2) { grid-column: span 1; } }
        @media (max-width: 680px) { .workbench { padding: 16px; } .header { flex-direction: column; gap: 11px; } .status-badge { align-self: flex-start; } .right-column { grid-template-columns: 1fr; } .process-grid, .metric-grid { grid-template-columns: 1fr; } .conversation-card { min-height: 480px; } .message-row { max-width: 92%; } .send-button span { display: none; } .send-button { width: 43px; justify-content: center; padding: 0; } }
      </style>
      <main class="workbench">
        <header class="header">
          <div class="brand">
            <div class="brand-mark"><ha-icon icon="mdi:home-automation"></ha-icon></div>
            <div><h1>EdgeHome · 家居智能体工作台</h1><p class="subtitle">对话驱动 · 规划可见 · MQTT 设备回读 · 本地可信执行</p></div>
          </div>
          <div class="status-badge ${status.className}"><ha-icon icon="${status.icon}"></ha-icon>${escapeHtml(status.label)}</div>
        </header>

        <section class="layout">
          <div class="left-column">
            <section class="card conversation-card">
              <div class="card-head"><div class="card-title"><ha-icon icon="mdi:message-text-outline"></ha-icon>对话窗口</div><span class="card-note">自然语言输入与最终解释回复</span></div>
              <div class="messages" id="edgehome-messages">${this._messageList()}</div>
              <div class="composer">
                <div class="composer-row"><textarea id="edgehome-input" ${processing} placeholder="例如：把客厅主灯调到四成，或者帮我准备观影场景"></textarea><button class="icon-button" type="button" data-action="reset" ${processing} aria-label="新建会话"><ha-icon icon="mdi:restart"></ha-icon></button><button class="send-button" type="button" data-action="send" ${processing}><ha-icon icon="mdi:send"></ha-icon><span>${WORKBENCH_SESSION.processing ? "处理中" : "发送"}</span></button></div>
              </div>
              <div class="shortcut-area"><div class="shortcut-heading"><span class="section-label">快捷场景</span><button class="restore-demo" type="button" data-action="restore-demo" ${processing} title="恢复 MQTT 设备、环境条件和安全设备的演示初始状态"><ha-icon icon="mdi:restore"></ha-icon><span>${WORKBENCH_SESSION.processing ? "正在恢复" : "恢复初始状态"}</span></button></div><div class="shortcut-grid">${SHORTCUTS.map((item) => `<button class="shortcut" type="button" data-prompt="${escapeHtml(item.prompt)}" ${processing}>${escapeHtml(item.label)}</button>`).join("")}</div></div>
            </section>

            <section class="process-grid">
              <article class="card process-card"><h3><ha-icon icon="mdi:brain"></ha-icon>模型与意图</h3><div class="kv"><div><span>执行智能体</span><strong>${escapeHtml(agentId ? "EdgeHome 本地对话智能体" : "Home Assistant 默认对话智能体")}</strong></div><div><span>决策路径</span><strong>${escapeHtml(route)}</strong></div><div><span>规划来源</span><strong>${escapeHtml(planningSource)}${planningAttempts ? ` · ${planningAttempts} 次` : ""}</strong></div><div><span>判断说明</span><strong>${escapeHtml(decision)}</strong></div></div></article>
              <article class="card process-card"><h3><ha-icon icon="mdi:message-processing-outline"></ha-icon>会话上下文</h3><div class="kv"><div><span>会话标识</span><strong>${escapeHtml(briefId(WORKBENCH_SESSION.conversationId))}</strong></div><div><span>对话轮次</span><strong>${Math.max(0, Math.floor((WORKBENCH_SESSION.messages.length - 1) / 2))}</strong></div><div><span>更新时间</span><strong>${escapeHtml(dateText(updatedAt))}</strong></div></div></article>
            </section>
          </div>

          <div class="right-column">
            <section class="card"><div class="card-head"><div class="card-title"><ha-icon icon="mdi:source-branch"></ha-icon>任务规划 DAG</div><span class="card-note">${steps.length} 步</span></div><div class="panel-body">${this._planRows(steps)}</div></section>
            <section class="card"><div class="card-head"><div class="card-title"><ha-icon icon="mdi:shield-search"></ha-icon>约束与规划证据</div><span class="card-note">模型输出先校验后执行</span></div><div class="panel-body">${this._constraintRows(planningConstraints, planningRationale, planningRepairReason)}</div></section>
            <section class="card"><div class="card-head"><div class="card-title"><ha-icon icon="mdi:transmission-tower"></ha-icon>执行与 MQTT 回读</div><span class="card-note">${mqttResults.length ? `${verifiedCount}/${mqttResults.length} 已验证` : "本地读取"}</span></div><div class="panel-body">${this._resultRows(mqttResults)}</div></section>
            <section class="card"><div class="card-head"><div class="card-title"><ha-icon icon="mdi:devices"></ha-icon>设备状态面板</div><span class="card-note">独立 MQTT 模拟设备</span></div><div class="panel-body">${this._deviceRows()}</div></section>
            <section class="card"><div class="card-head"><div class="card-title"><ha-icon icon="mdi:chart-line"></ha-icon>环境与能耗</div><span class="card-note">实时实体状态</span></div><div class="panel-body"><div class="metric-grid">${this._environmentRows()}</div></div></section>
            <section class="card"><div class="card-head"><div class="card-title"><ha-icon icon="mdi:shield-lock-outline"></ha-icon>安全边界</div><span class="card-note">保护设备不可批量断电</span></div><div class="panel-body">${this._protectedDevice("switch.edgehome_router", "mdi:router-network")}${this._protectedDevice("switch.edgehome_refrigerator", "mdi:fridge-outline")}<p class="boundary-note">执行候选动作先经过实体白名单、参数范围与安全策略检查；最终仅以设备 MQTT 状态和 Home Assistant 回读一致为完成依据。</p></div></section>
          </div>
        </section>
        <div class="footer">EdgeHome Agent Workspace · UI ${EDGEHOME_WORKBENCH_VERSION}</div>
      </main>`;

    this._bindInteractionEvents();
    requestAnimationFrame(() => {
      const messages = this.shadowRoot?.querySelector("#edgehome-messages");
      if (messages) messages.scrollTop = messages.scrollHeight;
    });
  }

  _bindInteractionEvents() {
    if (this._eventsBound || !this.shadowRoot) return;
    this._eventsBound = true;

    // 事件委托避免 Home Assistant 状态更新触发重新渲染后，旧按钮监听器失效。
    this.addEventListener("click", (event) => {
      const trigger = event.composedPath().find((node) => node?.dataset?.action || node?.dataset?.prompt);
      if (!trigger) return;
      if (trigger.dataset.prompt) {
        this._sendPrompt(trigger.dataset.prompt);
        return;
      }
      if (trigger.dataset.action === "reset") {
        this._resetConversation();
        return;
      }
      if (trigger.dataset.action === "restore-demo") {
        this._restoreInitialState();
        return;
      }
      if (trigger.dataset.action === "send") {
        this._sendPrompt(this.shadowRoot?.querySelector("#edgehome-input")?.value);
      }
    });
    this.addEventListener("keydown", (event) => {
      if (event.target?.id === "edgehome-input" && event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        this._sendPrompt(event.target.value);
      }
    });
  }
}

if (!customElements.get("edgehome-execution-workbench")) {
  customElements.define("edgehome-execution-workbench", EdgeHomeExecutionWorkbench);
}
