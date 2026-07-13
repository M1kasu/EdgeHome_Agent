/* EdgeHome 可信执行工作台：不依赖第三方卡片，所有字段均来自 Home Assistant 状态机。 */

const EDGEHOME_WORKBENCH_VERSION = "20260714";

const ENTITY_LABELS = {
  "light.edgehome_living_room_main": "客厅主灯",
  "cover.edgehome_living_room_curtain": "客厅窗帘",
  "climate.edgehome_living_room_ac": "客厅空调",
  "switch.edgehome_living_room_tv": "客厅电视",
  "switch.edgehome_router": "家庭路由器",
  "switch.edgehome_refrigerator": "冰箱",
};

const TOOL_LABELS = {
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

const STATUS_META = {
  success: { label: "已完成并验证", tone: "success", icon: "mdi:check-decagram" },
  partial_success: { label: "部分完成", tone: "warning", icon: "mdi:alert-decagram" },
  failed: { label: "执行未完成", tone: "danger", icon: "mdi:close-octagon" },
  needs_replan: { label: "等待受限重规划", tone: "warning", icon: "mdi:source-branch-sync" },
  handled: { label: "已处理", tone: "info", icon: "mdi:shield-check" },
  unknown: { label: "等待首次指令", tone: "muted", icon: "mdi:clock-outline" },
  unavailable: { label: "执行记录不可用", tone: "danger", icon: "mdi:cloud-alert" },
};

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
    heat_cool: "自动冷热",
    dry: "除湿",
    fan_only: "送风",
    unavailable: "离线",
    unknown: "未知",
  };
  return labels[state] || String(state || "未知");
}

class EdgeHomeExecutionWorkbench extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 16;
  }

  connectedCallback() {
    this._render();
  }

  _entity(entityId) {
    return this._hass?.states?.[entityId] || null;
  }

  _attribute(entity, name, fallback = null) {
    return entity?.attributes?.[name] ?? fallback;
  }

  _deviceCard(entityId, title, icon, metric, detail) {
    const entity = this._entity(entityId);
    const state = entity?.state || "unavailable";
    const online = state !== "unavailable" && state !== "unknown";
    return `
      <article class="device-card ${online ? "" : "is-offline"}">
        <div class="device-icon"><ha-icon icon="${icon}"></ha-icon></div>
        <div class="device-main">
          <span>${escapeHtml(title)}</span>
          <strong>${escapeHtml(metric)}</strong>
          <small>${escapeHtml(detail)}</small>
        </div>
        <span class="device-state ${online ? "" : "offline"}">${online ? "MQTT 在线" : "不可用"}</span>
      </article>`;
  }

  _lightCard() {
    const entity = this._entity("light.edgehome_living_room_main");
    const state = entity?.state || "unavailable";
    const brightness = numberOrNull(this._attribute(entity, "brightness"));
    const percent = brightness === null ? null : Math.round((brightness / 255) * 100);
    const metric = state === "on" ? `${percent ?? "—"}%` : stateText(state);
    return this._deviceCard(
      "light.edgehome_living_room_main",
      "客厅主灯",
      "mdi:ceiling-light",
      metric,
      state === "on" ? "亮度由设备状态回读" : "等待下一次控制"
    );
  }

  _curtainCard() {
    const entity = this._entity("cover.edgehome_living_room_curtain");
    const state = entity?.state || "unavailable";
    const position = numberOrNull(this._attribute(entity, "current_position"));
    const metric = position === null ? stateText(state) : `${position}%`;
    return this._deviceCard(
      "cover.edgehome_living_room_curtain",
      "客厅窗帘",
      "mdi:curtains",
      metric,
      state === "opening" || state === "closing" ? "设备正在移动" : stateText(state)
    );
  }

  _acCard() {
    const entity = this._entity("climate.edgehome_living_room_ac");
    const state = entity?.state || "unavailable";
    const target = numberOrNull(this._attribute(entity, "temperature"));
    const current = numberOrNull(this._attribute(entity, "current_temperature"));
    const metric = state === "off" ? "已关闭" : target === null ? stateText(state) : `${target}°C`;
    const detail = current === null ? stateText(state) : `当前 ${current}°C · ${stateText(state)}`;
    return this._deviceCard("climate.edgehome_living_room_ac", "客厅空调", "mdi:air-conditioner", metric, detail);
  }

  _tvCard() {
    const entity = this._entity("switch.edgehome_living_room_tv");
    const state = entity?.state || "unavailable";
    return this._deviceCard(
      "switch.edgehome_living_room_tv",
      "客厅电视",
      "mdi:television-classic",
      stateText(state),
      state === "on" ? "设备状态已同步" : "未纳入当前操作"
    );
  }

  _plan(steps) {
    if (!steps.length) {
      return `<div class="empty-state"><ha-icon icon="mdi:route-variant"></ha-icon><span>等待一次请求后展示结构化计划</span></div>`;
    }
    return steps
      .map((step, index) => {
        const entityId = step?.entity_id || "";
        const target = ENTITY_LABELS[entityId] || entityId || "本地处理";
        const tool = TOOL_LABELS[step?.tool] || step?.tool || "受限处理";
        return `
          <div class="timeline-row">
            <span class="timeline-index">${index + 1}</span>
            <div>
              <strong>${escapeHtml(tool)}</strong>
              <small>${escapeHtml(target)}</small>
            </div>
            <ha-icon icon="mdi:shield-check-outline"></ha-icon>
          </div>`;
      })
      .join("");
  }

  _verification(results) {
    if (!results.length) {
      return `<div class="empty-state"><ha-icon icon="mdi:database-search-outline"></ha-icon><span>无设备动作，或等待状态回读</span></div>`;
    }
    return results
      .map((result) => {
        const entityId = result?.entity_id || "";
        const target = ENTITY_LABELS[entityId] || entityId || result?.tool || "执行步骤";
        const verified = result?.verified === true;
        const pending = result?.verified === null || result?.verified === undefined;
        const before = stateText(result?.state_before);
        const after = stateText(result?.state_after);
        const outcome = pending ? "待校验" : verified ? "已验证" : "未通过";
        const error = result?.error ? `<small class="verify-error">${escapeHtml(result.error)}</small>` : "";
        return `
          <div class="verify-row ${verified ? "verified" : pending ? "pending" : "failed"}">
            <span class="verify-icon"><ha-icon icon="${verified ? "mdi:check" : pending ? "mdi:clock-outline" : "mdi:alert"}"></ha-icon></span>
            <div class="verify-copy">
              <strong>${escapeHtml(target)}</strong>
              <small>${escapeHtml(before)} <b>→</b> ${escapeHtml(after)}</small>
              ${error}
            </div>
            <span>${outcome}</span>
          </div>`;
      })
      .join("");
  }

  _protectedDevice(entityId, icon) {
    const entity = this._entity(entityId);
    const state = entity?.state || "unavailable";
    const safe = state === "on";
    return `
      <div class="protected-device ${safe ? "safe" : "attention"}">
        <ha-icon icon="${icon}"></ha-icon>
        <span>${escapeHtml(ENTITY_LABELS[entityId])}</span>
        <strong>${safe ? "受保护 · 开启" : stateText(state)}</strong>
      </div>`;
  }

  _render() {
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
    }
    const traceEntityId = this._config?.trace_entity || "sensor.edgehome_last_execution";
    const trace = this._entity(traceEntityId);
    const traceState = trace?.state || "unknown";
    const status = STATUS_META[traceState] || STATUS_META.handled;
    const steps = asArray(this._attribute(trace, "plan_steps", []));
    const results = asArray(this._attribute(trace, "step_results", []));
    const verifiedCount = results.filter((result) => result?.verified === true).length;
    const route = this._attribute(trace, "route_label", "等待首次请求");
    const goal = this._attribute(trace, "goal", "从右上角 Assist 输入生活化指令");
    const decision = this._attribute(trace, "decision", "未生成新的执行决策");
    const speech = this._attribute(trace, "speech", "等待一次 EdgeHome 处理结果");
    const updatedAt = this._attribute(trace, "updated_at", "—");

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; min-height: 100%; color: #e9f1ff; font-family: Inter, "Noto Sans SC", "Microsoft YaHei", system-ui, sans-serif; }
        * { box-sizing: border-box; }
        .workbench { position: relative; overflow: hidden; padding: 26px; border-radius: 24px; background: radial-gradient(circle at 8% -5%, rgba(48, 106, 255, .42), transparent 29%), radial-gradient(circle at 92% 0%, rgba(0, 214, 179, .18), transparent 25%), linear-gradient(135deg, #071321 0%, #0c1d31 48%, #091629 100%); box-shadow: 0 20px 60px rgba(2, 8, 23, .32); }
        .workbench::before { content: ""; position: absolute; inset: 0; pointer-events: none; opacity: .32; background-image: linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.025) 1px, transparent 1px); background-size: 30px 30px; mask-image: linear-gradient(to bottom, #000, transparent 86%); }
        .header, .content-grid, .footer-note { position: relative; z-index: 1; }
        .header { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; padding-bottom: 24px; border-bottom: 1px solid rgba(157, 187, 230, .16); }
        .brand { display: flex; gap: 16px; align-items: flex-start; }
        .brand-shield { display: grid; place-items: center; flex: 0 0 auto; width: 52px; height: 52px; border: 1px solid rgba(93, 151, 255, .7); border-radius: 16px; color: #9bc0ff; background: linear-gradient(145deg, rgba(61, 118, 255, .3), rgba(15, 34, 68, .5)); box-shadow: inset 0 0 20px rgba(120, 166, 255, .12); }
        .brand-shield ha-icon { --mdc-icon-size: 30px; }
        .eyebrow { display: flex; align-items: center; gap: 7px; color: #87a6d8; font-size: 11px; font-weight: 700; letter-spacing: .14em; }
        .pulse { width: 7px; height: 7px; border-radius: 50%; background: #45e3b3; box-shadow: 0 0 0 5px rgba(69, 227, 179, .12), 0 0 14px #45e3b3; }
        h1 { margin: 4px 0 5px; font-size: clamp(24px, 3vw, 34px); letter-spacing: -.045em; color: #f6f9ff; }
        .subtitle { margin: 0; color: #a9bbd6; font-size: 13px; line-height: 1.6; }
        .status-card { min-width: 190px; padding: 13px 14px; border: 1px solid rgba(159, 185, 228, .19); border-radius: 14px; background: rgba(5, 16, 33, .42); }
        .status-card .label { display: block; margin-bottom: 8px; color: #91a6c7; font-size: 11px; }
        .status-line { display: flex; align-items: center; gap: 8px; font-weight: 700; font-size: 14px; }
        .status-line ha-icon { --mdc-icon-size: 19px; }
        .status-line.success { color: #55e8bb; } .status-line.warning { color: #ffd06b; } .status-line.danger { color: #ff8f9f; } .status-line.info { color: #98bbff; } .status-line.muted { color: #b2c0d7; }
        .status-card small { display: block; margin-top: 6px; color: #788eaf; font-size: 11px; }
        .summary-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin: 20px 0; }
        .summary-cell { padding: 14px; border: 1px solid rgba(157, 187, 230, .15); border-radius: 14px; background: rgba(16, 37, 66, .58); }
        .summary-cell span { display: block; margin-bottom: 7px; color: #8ca2c4; font-size: 11px; }
        .summary-cell strong { display: block; overflow: hidden; color: #f0f5ff; font-size: 14px; line-height: 1.35; text-overflow: ellipsis; white-space: nowrap; }
        .content-grid { display: grid; grid-template-columns: 1.16fr .84fr; gap: 16px; }
        .left-stack, .right-stack { display: grid; gap: 16px; }
        .panel { border: 1px solid rgba(157, 187, 230, .15); border-radius: 18px; background: linear-gradient(145deg, rgba(18, 42, 75, .78), rgba(8, 24, 46, .76)); overflow: hidden; }
        .panel-header { display: flex; align-items: center; justify-content: space-between; padding: 16px 18px 12px; }
        .panel-title { display: flex; align-items: center; gap: 9px; color: #f1f6ff; font-size: 15px; font-weight: 700; }
        .panel-title ha-icon { color: #84afff; --mdc-icon-size: 19px; }
        .panel-kicker { color: #8499ba; font-size: 11px; }
        .flow { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0; padding: 5px 18px 18px; }
        .flow-node { position: relative; display: grid; gap: 7px; justify-items: center; text-align: center; color: #9eb2d1; font-size: 11px; }
        .flow-node:not(:last-child)::after { content: ""; position: absolute; top: 17px; left: 63%; width: 74%; height: 1px; background: linear-gradient(90deg, #3776dd, rgba(70, 153, 255, .12)); }
        .flow-node ha-icon { z-index: 1; padding: 8px; border: 1px solid rgba(96, 151, 255, .45); border-radius: 11px; color: #9ec2ff; background: #102a50; --mdc-icon-size: 20px; }
        .flow-node.active ha-icon { color: #5ce7bd; border-color: rgba(79, 229, 181, .6); background: rgba(38, 148, 122, .2); box-shadow: 0 0 20px rgba(53, 235, 185, .12); }
        .flow-node.alert ha-icon { color: #ffd16f; border-color: rgba(255, 209, 111, .55); background: rgba(151, 104, 12, .18); }
        .device-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; padding: 0 18px 18px; }
        .device-card { display: flex; align-items: center; gap: 11px; min-height: 96px; padding: 13px; border: 1px solid rgba(161, 195, 241, .14); border-radius: 14px; background: rgba(5, 18, 37, .44); }
        .device-card.is-offline { opacity: .65; }
        .device-icon { display: grid; flex: 0 0 auto; width: 37px; height: 37px; place-items: center; border-radius: 11px; color: #9ac0ff; background: linear-gradient(145deg, #193963, #102540); }
        .device-icon ha-icon { --mdc-icon-size: 21px; }
        .device-main { min-width: 0; flex: 1; }
        .device-main span, .device-main small { display: block; overflow: hidden; color: #8ea4c4; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
        .device-main strong { display: block; margin: 4px 0; color: #f0f6ff; font-size: 18px; letter-spacing: -.02em; }
        .device-state { align-self: flex-start; padding: 3px 6px; border-radius: 99px; color: #73e5be; background: rgba(61, 209, 164, .12); font-size: 9px; white-space: nowrap; }
        .device-state.offline { color: #ff9aaa; background: rgba(255, 116, 139, .12); }
        .timeline, .verification { padding: 0 18px 18px; }
        .timeline-row, .verify-row { display: flex; align-items: center; gap: 11px; padding: 11px 0; border-top: 1px solid rgba(159, 190, 234, .1); }
        .timeline-row:first-child, .verify-row:first-child { border-top: 0; }
        .timeline-index { display: grid; width: 24px; height: 24px; place-items: center; border-radius: 50%; color: #c4d8ff; background: rgba(75, 132, 236, .2); font-size: 11px; font-weight: 700; }
        .timeline-row div, .verify-copy { min-width: 0; flex: 1; }
        .timeline-row strong, .verify-copy strong { display: block; overflow: hidden; color: #eaf2ff; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
        .timeline-row small, .verify-copy small { display: block; overflow: hidden; margin-top: 3px; color: #8fa5c4; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
        .timeline-row > ha-icon { color: #5d91df; --mdc-icon-size: 17px; }
        .verify-icon { display: grid; width: 25px; height: 25px; place-items: center; border-radius: 50%; }
        .verify-icon ha-icon { --mdc-icon-size: 16px; }
        .verify-row.verified .verify-icon { color: #50e7b9; background: rgba(65, 215, 166, .13); }
        .verify-row.pending .verify-icon { color: #f6ce72; background: rgba(241, 183, 65, .14); }
        .verify-row.failed .verify-icon { color: #ff95a3; background: rgba(250, 102, 124, .14); }
        .verify-row > span:last-child { color: #9eb2cf; font-size: 10px; white-space: nowrap; }
        .verify-error { color: #ff9eaa !important; }
        .empty-state { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 23px 14px 20px; color: #8296b5; text-align: center; font-size: 12px; }
        .empty-state ha-icon { color: #5579b5; --mdc-icon-size: 25px; }
        .decision { margin: 0 18px 18px; padding: 13px; border-left: 2px solid #4d85e8; border-radius: 0 10px 10px 0; color: #b6c8e4; background: rgba(51, 99, 177, .12); font-size: 12px; line-height: 1.55; }
        .decision b { color: #eef5ff; }
        .safety-wrap { display: grid; gap: 9px; padding: 0 18px 14px; }
        .protected-device { display: flex; align-items: center; gap: 9px; padding: 10px; border: 1px solid rgba(158, 187, 230, .14); border-radius: 10px; background: rgba(6, 20, 39, .35); }
        .protected-device ha-icon { color: #9ebcff; --mdc-icon-size: 19px; }
        .protected-device span { flex: 1; color: #b7c8e3; font-size: 12px; }
        .protected-device strong { color: #71e7bd; font-size: 11px; }
        .protected-device.attention strong { color: #ffbf78; }
        .safety-note { margin: 0 18px 17px; color: #8399ba; font-size: 11px; line-height: 1.5; }
        .boundary-list { display: grid; gap: 8px; padding: 0 18px 18px; }
        .boundary-item { display: flex; gap: 9px; align-items: flex-start; padding: 10px 11px; border: 1px solid rgba(156, 191, 239, .13); border-radius: 10px; color: #9db1cf; background: rgba(17, 42, 76, .45); font-size: 11px; line-height: 1.5; }
        .boundary-item ha-icon { flex: 0 0 auto; margin-top: 1px; color: #7eaeff; --mdc-icon-size: 17px; }
        .boundary-item strong { color: #dce9ff; font-weight: 600; }
        .footer-note { display: flex; justify-content: space-between; gap: 12px; margin-top: 17px; color: #7f95b5; font-size: 10px; }
        .footer-note strong { color: #9cb6dd; font-weight: 600; }
        @media (max-width: 840px) { .workbench { padding: 18px; border-radius: 18px; } .header { align-items: stretch; flex-direction: column; } .status-card { min-width: 0; } .content-grid { grid-template-columns: 1fr; } .summary-grid { grid-template-columns: 1fr; } }
        @media (max-width: 470px) { .brand { gap: 11px; } .brand-shield { width: 43px; height: 43px; border-radius: 13px; } h1 { font-size: 23px; } .device-grid { grid-template-columns: 1fr; padding-left: 13px; padding-right: 13px; } .flow { padding-left: 13px; padding-right: 13px; } .flow-node:not(:last-child)::after { width: 66%; } .panel-header { padding-left: 13px; padding-right: 13px; } .timeline, .verification, .safety-wrap, .boundary-list { padding-left: 13px; padding-right: 13px; } .decision, .safety-note { margin-left: 13px; margin-right: 13px; } }
      </style>
      <section class="workbench">
        <header class="header">
          <div class="brand">
            <div class="brand-shield"><ha-icon icon="mdi:shield-home-outline"></ha-icon></div>
            <div>
              <div class="eyebrow"><span class="pulse"></span> EDGEHOME / TRUSTED EXECUTION</div>
              <h1>家居可信执行工作台</h1>
              <p class="subtitle">从右上角 Assist 发起对话；本页同步展示计划、设备状态与回读结果。</p>
            </div>
          </div>
          <div class="status-card">
            <span class="label">最近一次执行结论</span>
            <div class="status-line ${status.tone}"><ha-icon icon="${status.icon}"></ha-icon>${escapeHtml(status.label)}</div>
            <small>${escapeHtml(route)}</small>
          </div>
        </header>

        <section class="summary-grid">
          <div class="summary-cell"><span>当前处理路径</span><strong>${escapeHtml(route)}</strong></div>
          <div class="summary-cell"><span>最近目标</span><strong>${escapeHtml(goal)}</strong></div>
          <div class="summary-cell"><span>状态回读</span><strong>${results.length ? `${verifiedCount} / ${results.length} 步已验证` : "等待设备动作"}</strong></div>
        </section>

        <main class="content-grid">
          <div class="left-stack">
            <section class="panel">
              <div class="panel-header"><div class="panel-title"><ha-icon icon="mdi:source-branch"></ha-icon>可信执行链路</div><span class="panel-kicker">候选计划不可直连设备</span></div>
              <div class="flow">
                <div class="flow-node active"><ha-icon icon="mdi:message-text-outline"></ha-icon><span>生活化指令</span></div>
                <div class="flow-node active"><ha-icon icon="mdi:format-list-checks"></ha-icon><span>受限计划</span></div>
                <div class="flow-node ${traceState === "failed" ? "alert" : "active"}"><ha-icon icon="mdi:transmission-tower"></ha-icon><span>MQTT 设备</span></div>
                <div class="flow-node ${status.tone === "danger" || status.tone === "warning" ? "alert" : "active"}"><ha-icon icon="mdi:database-check-outline"></ha-icon><span>状态回读</span></div>
              </div>
            </section>

            <section class="panel">
              <div class="panel-header"><div class="panel-title"><ha-icon icon="mdi:sofa-outline"></ha-icon>家庭设备</div><span class="panel-kicker">独立 MQTT 数字设备</span></div>
              <div class="device-grid">${this._lightCard()}${this._curtainCard()}${this._acCard()}${this._tvCard()}</div>
            </section>

            <section class="panel">
              <div class="panel-header"><div class="panel-title"><ha-icon icon="mdi:clipboard-text-outline"></ha-icon>本轮结论</div><span class="panel-kicker">${escapeHtml(updatedAt)}</span></div>
              <div class="decision"><b>EdgeHome：</b>${escapeHtml(speech)}<br><b>决策：</b>${escapeHtml(decision)}</div>
            </section>
          </div>

          <div class="right-stack">
            <section class="panel">
              <div class="panel-header"><div class="panel-title"><ha-icon icon="mdi:format-list-numbered"></ha-icon>任务计划</div><span class="panel-kicker">${steps.length} 步</span></div>
              <div class="timeline">${this._plan(steps)}</div>
            </section>

            <section class="panel">
              <div class="panel-header"><div class="panel-title"><ha-icon icon="mdi:check-circle-outline"></ha-icon>回读验证</div><span class="panel-kicker">以最终状态为准</span></div>
              <div class="verification">${this._verification(results)}</div>
            </section>

            <section class="panel">
              <div class="panel-header"><div class="panel-title"><ha-icon icon="mdi:shield-lock-outline"></ha-icon>安全边界</div><span class="panel-kicker">受保护设备不参与批量断电</span></div>
              <div class="safety-wrap">${this._protectedDevice("switch.edgehome_router", "mdi:router-network")} ${this._protectedDevice("switch.edgehome_refrigerator", "mdi:fridge-outline")}</div>
              <p class="safety-note">界面只读取已落地的执行和状态数据；真实控制统一由 Assist 触发，避免演示页面绕开规划与回读链路。</p>
              <div class="boundary-list">
                <div class="boundary-item"><ha-icon icon="mdi:shield-check-outline"></ha-icon><span><strong>计划先行：</strong>候选动作须经实体白名单、参数范围和安全策略检查。</span></div>
                <div class="boundary-item"><ha-icon icon="mdi:database-check-outline"></ha-icon><span><strong>回读为准：</strong>仅当 MQTT 设备状态与 Home Assistant 回读一致，才可报告完成。</span></div>
              </div>
            </section>
          </div>
        </main>

        <footer class="footer-note"><span><strong>可信完成标准：</strong>设备 MQTT 状态与 Home Assistant 回读一致</span><span>UI ${EDGEHOME_WORKBENCH_VERSION}</span></footer>
      </section>`;

  }
}

if (!customElements.get("edgehome-execution-workbench")) {
  customElements.define("edgehome-execution-workbench", EdgeHomeExecutionWorkbench);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: "edgehome-execution-workbench",
  name: "EdgeHome 可信执行工作台",
  description: "展示实际计划、MQTT 设备状态与回读验证的本地工作台。",
});
