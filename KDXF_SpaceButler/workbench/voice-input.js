/* Push-to-talk lifecycle. A final ASR result is the only path to onSubmit. */
class PushToTalk {
  constructor(options) {
    Object.assign(this, options);
    // 多个入口共享 document 级 V 键监听，靠 enabled() 决定这一次归谁。
    this.enabled = options.enabled || (() => true);
    this.current = null;
    this.config = null;
    this.keyHeld = false;
    this.keyTimer = null;
    this.tapTarget = null;
    this.button.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || this.active) return;
      event.preventDefault();
      this.button.setPointerCapture(event.pointerId);
      this.start();
    });
    this.button.addEventListener("pointerup", () => this.release());
    this.button.addEventListener("pointercancel", () => this.cancel());
    this.button.addEventListener("lostpointercapture", () => {
      if (this.current?.phase === "recording") this.cancel();
    });
    this.cancelButton.addEventListener("click", () => this.cancel());
    document.addEventListener("keydown", (event) => this.keydown(event), true);
    document.addEventListener("keyup", (event) => this.keyup(event), true);
    document.addEventListener("compositionstart", () => this.cancel(), true);
    window.addEventListener("blur", () => this.cancel());
    window.addEventListener("pagehide", () => this.cancel());
    document.addEventListener("visibilitychange", () => { if (document.hidden) this.cancel(); });
    this.refresh();
  }

  get active() { return this.current !== null; }

  async request(path, options = {}) {
    const response = await fetch(path, { ...options, signal: AbortSignal.timeout(20000) });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `语音请求失败（${response.status}）`);
    return payload;
  }

  async refresh() {
    try {
      this.config = await this.request("/api/voice/status");
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) {
        throw new Error("麦克风需要现代浏览器和 localhost / HTTPS 地址。");
      }
      if (!this.config.configured) throw new Error("服务端尚未配置语音 API Key。");
      this.show("idle", "按住 V 或话筒说话，松开后提交给 Agent");
    } catch (error) {
      this.show("error", error.message);
    }
  }

  show(phase, message) {
    this.panel.dataset.phase = phase;
    this.status.textContent = message;
    this.button.setAttribute("aria-pressed", String(phase === "recording"));
    this.cancelButton.hidden = !this.active;
    this.onActive?.();
  }

  keydown(event) {
    if (!this.enabled() && !this.active && !this.keyHeld) return;
    if (event.key === "Escape" && (this.active || this.keyHeld)) {
      event.preventDefault();
      this.cancel();
      return;
    }
    if (event.code !== "KeyV" || event.isComposing || event.keyCode === 229
        || event.ctrlKey || event.altKey || event.metaKey || event.shiftKey) return;
    const editable = event.target.closest?.("input, textarea, select, [contenteditable]");
    if ((editable && editable !== this.input) || event.target.closest?.("dialog") || this.isBusy()) return;
    if (event.repeat || this.keyHeld) { if (this.keyHeld) event.preventDefault(); return; }
    if (this.active) return;
    event.preventDefault();
    this.keyHeld = true;
    this.tapTarget = editable === this.input ? this.input : null;
    this.keyTimer = setTimeout(() => {
      this.keyTimer = null;
      this.tapTarget = null;
      if (this.keyHeld) this.start();
    }, 180);
  }

  keyup(event) {
    if (event.code !== "KeyV" || !this.keyHeld) return;
    event.preventDefault();
    clearTimeout(this.keyTimer);
    this.keyTimer = null;
    this.keyHeld = false;
    // A quick tap in the text composer is still ordinary typing.
    if (this.tapTarget && !this.active) {
      this.tapTarget.setRangeText("v", this.tapTarget.selectionStart, this.tapTarget.selectionEnd, "end");
      this.tapTarget.dispatchEvent(new Event("input", { bubbles: true }));
    }
    this.tapTarget = null;
    this.release();
  }

  async start() {
    if (this.active || this.isBusy() || !this.enabled()) return;
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) {
      this.show("error", "请使用 localhost / HTTPS，并在浏览器中允许麦克风。");
      return;
    }
    this.onStart?.();
    const run = { phase: "starting", draft: this.input.value, sequence: 0, queued: 0,
      audioBytes: 0, upload: Promise.resolve(), session: null, stream: null, ctx: null, node: null };
    this.current = run;
    this.transcript.textContent = "识别文字将在这里实时出现";
    this.timer.textContent = "00:00";
    this.show("starting", "正在连接语音服务，请继续按住；首次使用需允许麦克风");
    try {
      run.stream = await navigator.mediaDevices.getUserMedia({ audio: {
        channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true,
      } });
      if (this.current !== run) { this.stopMedia(run); return; }
      run.ctx = new AudioContext();
      await run.ctx.resume();
      await run.ctx.audioWorklet.addModule("/voice-capture.js");
      if (this.current !== run) { this.stopMedia(run); return; }
      run.session = await this.request("/api/voice/sessions", { method: "POST" });
      if (this.current !== run) { this.dispose(run); return; }
      if (run.session.state !== "recording") throw new Error(run.session.error || "语音会话未就绪，请重试。");
      run.node = new AudioWorkletNode(run.ctx, "voice-capture");
      run.node.port.onmessage = ({ data }) => {
        if (this.current !== run) return;
        if (data.audio) this.enqueue(run, data.audio);
        if (data.stopped) run.flushed?.();
      };
      run.source = run.ctx.createMediaStreamSource(run.stream);
      // The worklet emits silence, so the microphone is never played through speakers.
      run.source.connect(run.node).connect(run.ctx.destination);
      run.stream.getAudioTracks().forEach((track) => track.addEventListener("ended", () => {
        if (this.current === run && run.phase === "recording") this.fail(run, "麦克风已断开，本次任务未提交。");
      }));
      run.phase = "recording";
      run.started = performance.now();
      run.clock = setInterval(() => {
        const elapsed = Math.floor((performance.now() - run.started) / 1000);
        this.timer.textContent = `${String(Math.floor(elapsed / 60)).padStart(2, "0")}:${String(elapsed % 60).padStart(2, "0")}`;
        if (elapsed >= (this.config?.max_seconds || 90)) this.fail(run, "已达到 90 秒上限，本次未提交，请分段说话。");
      }, 250);
      this.show("recording", "正在聆听 · 松开后提交 · Esc 取消");
      this.poll(run);
    } catch (error) {
      const message = ({ NotAllowedError: "麦克风权限被拒绝，请在地址栏允许麦克风后重试。",
        NotFoundError: "未找到麦克风，请连接麦克风后重试。",
        NotReadableError: "麦克风不可用，可能被其他应用占用。" })[error.name] || error.message;
      this.fail(run, message);
    }
  }

  enqueue(run, audio) {
    run.audioBytes += audio.byteLength;
    run.queued += 1;
    if (run.queued > 25) { this.fail(run, "音频上传过慢，本次任务未提交，请检查网络。"); return; }
    const sequence = run.sequence++;
    run.upload = run.upload.then(async () => {
      if (this.current !== run) return;
      const response = await this.request(`/api/voice/sessions/${run.session.session_id}/audio`, {
        method: "POST", body: audio,
        headers: { "Content-Type": "application/octet-stream", "X-Audio-Sequence": String(sequence) },
      });
      if (response.state === "error") throw new Error(response.error);
      run.queued -= 1;
    }).catch((error) => this.fail(run, error.message));
  }

  async poll(run) {
    try {
      const result = await this.request(`/api/voice/sessions/${run.session.session_id}`);
      if (this.current !== run) return;
      if (result.state === "error" || result.state === "cancelled") throw new Error(result.error || "录音会话已结束。");
      if (result.text) {
        this.transcript.textContent = result.text;
        this.input.value = result.text;
      }
      if (result.state !== "final") run.pollTimer = setTimeout(() => this.poll(run), 150);
    } catch (error) { this.fail(run, error.message); }
  }

  async release() {
    const run = this.current;
    if (!run || run.phase === "finishing") return;
    if (run.phase === "starting") { this.cancel("连接尚未完成，本次未提交。允许麦克风后请重新按住 V。"); return; }
    run.phase = "finishing";
    clearInterval(run.clock);
    this.show("finishing", "录音已停止，正在确认最终文字…");
    try {
      await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error("音频收尾超时，本次任务未提交。")), 1500);
        run.flushed = () => { clearTimeout(timeout); resolve(); };
        run.node.port.postMessage("stop");
      });
      this.stopMedia(run);
      await run.upload;
      if (this.current !== run) return;
      if (!run.audioBytes) { this.cancel("没有录到声音，请重新按住 V。"); return; }
      const result = await this.request(`/api/voice/sessions/${run.session.session_id}/finish`, { method: "POST" });
      if (this.current !== run) return;
      if (result.state !== "final") throw new Error(result.error || "未收到最终识别结果，本次任务未提交。");
      const text = String(result.text || "").trim();
      this.current = null;
      this.dispose(run);
      if (!text || !/[\p{L}\p{N}]/u.test(text)) {
        this.input.value = run.draft;
        this.show("idle", "未识别到有效语音，没有提交任务。请重新按住 V。");
        return;
      }
      this.input.value = text;
      this.transcript.textContent = text;
      if (text.length > this.input.maxLength) {
        this.show("error", "识别文字超过 500 字，未自动提交。请在输入框中精简后发送。");
        return;
      }
      this.show("idle", "识别完成，已交给 Agent 理解与拆解任务");
      await this.onSubmit(text);
    } catch (error) { this.fail(run, error.message); }
  }

  stopMedia(run) {
    run.stream?.getTracks().forEach((track) => track.stop());
    run.source?.disconnect();
    run.node?.disconnect();
    if (run.ctx && run.ctx.state !== "closed") run.ctx.close().catch(() => {});
  }

  dispose(run) {
    clearInterval(run.clock);
    clearTimeout(run.pollTimer);
    this.stopMedia(run);
    if (run.session) {
      fetch(`/api/voice/sessions/${run.session.session_id}`, { method: "DELETE", keepalive: true }).catch(() => {});
    }
  }

  fail(run, message) {
    if (this.current !== run) return;
    this.cancel(message || "语音识别失败，本次任务未提交。", true);
  }

  cancel(message = "已取消录音，没有提交任务。", error = false) {
    clearTimeout(this.keyTimer);
    this.keyTimer = null;
    this.keyHeld = false;
    this.tapTarget = null;
    const run = this.current;
    if (!run) return;
    this.current = null;
    this.dispose(run);
    this.input.value = run.draft;
    this.show(error ? "error" : "idle", message);
  }
}
window.PushToTalk = PushToTalk;
