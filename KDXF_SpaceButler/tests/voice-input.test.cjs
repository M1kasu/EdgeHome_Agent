const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'workbench/voice-input.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

function emitter() {
  return { handlers: {}, addEventListener(name, fn) { (this.handlers[name] ||= []).push(fn); },
    dispatch(name, event = {}) { for (const fn of this.handlers[name] || []) fn(event); } };
}
function element() {
  return { ...emitter(), value: '', maxLength: 500, dataset: {}, textContent: '', hidden: false,
    setAttribute() {}, setPointerCapture() {}, closest() { return null; }, dispatchEvent() {},
    setRangeText(text) { this.value += text; } };
}

function setup(options = {}) {
  const calls = [], submitted = [];
  const track = { ...emitter(), stopped: false, stop() { this.stopped = true; } };
  const stream = { getTracks: () => [track], getAudioTracks: () => [track] };
  const document = emitter(), window = { ...emitter(), isSecureContext: true, AudioWorkletNode: true };
  class AudioContext {
    constructor() { this.state = 'running'; this.audioWorklet = { addModule: async () => {} }; }
    async resume() {}
    async close() { this.state = 'closed'; }
    createMediaStreamSource() { return { connect: node => node, disconnect() {} }; }
  }
  class AudioWorkletNode {
    constructor() {
      this.port = { postMessage: () => queueMicrotask(() => this.port.onmessage({ data: { stopped: true } })) };
    }
    connect() { return this; }
    disconnect() {}
  }
  let partial = '打开客厅灯。';
  const fetch = async (url, init = {}) => {
    calls.push({ url, ...init });
    let body;
    if (url.endsWith('/status')) body = { configured: true, max_seconds: 90 };
    else if (url.endsWith('/sessions')) body = { state: 'recording', session_id: 'local-test' };
    else if (url.endsWith('/finish')) body = options.finish ? await options.finish() : { state: 'final', text: '打开客厅灯，空调设为26度。' };
    else if (init.method === 'DELETE') body = { state: 'cancelled' };
    else if (url.endsWith('/audio')) {
      if (options.uploadError) throw new Error('upload interrupted');
      body = { state: 'recording' };
    } else body = { state: 'recording', text: partial };
    return { ok: true, json: async () => body };
  };
  const context = vm.createContext({ window, document, fetch, navigator: { mediaDevices: {
    getUserMedia: options.media || (async () => stream),
  } }, AudioContext, AudioWorkletNode, AbortSignal, performance, Event: class {}, setTimeout, clearTimeout,
    setInterval, clearInterval, console });
  vm.runInContext(source, context);
  const ui = { input: element(), button: element(), panel: element(), status: element(),
    transcript: element(), timer: element(), cancelButton: element() };
  ui.input.value = '原来的草稿';
  const voice = new window.PushToTalk({ ...ui, isBusy: () => false, onSubmit: async text => submitted.push(text) });
  return { voice, ui, track, stream, calls, submitted, window, document, setPartial(text) { partial = text; } };
}

test('partial subtitles never submit; release sends the final transcript exactly once', async () => {
  const t = setup();
  try {
    await t.voice.start();
    await tick();
    assert.equal(t.ui.transcript.textContent, '打开客厅灯。');
    assert.deepEqual(t.submitted, []);
    const run = t.voice.current;
    t.voice.enqueue(run, new ArrayBuffer(6400));
    t.voice.enqueue(run, new ArrayBuffer(200));
    await t.voice.release();
    await t.voice.release();
    assert.deepEqual(t.submitted, ['打开客厅灯，空调设为26度。']);
    const upload = t.calls.filter(c => c.url.endsWith('/audio'));
    assert.deepEqual(upload.map(c => c.headers['X-Audio-Sequence']), ['0', '1']);
    assert.equal(t.calls.filter(c => c.url.endsWith('/finish')).length, 1);
    assert.equal(t.track.stopped, true);
    assert.equal(t.voice.active, false);
  } finally { t.voice.cancel(); }
});

test('cancel while awaiting final ignores the late result and restores the draft', async () => {
  let finish;
  const t = setup({ finish: () => new Promise(resolve => { finish = resolve; }) });
  await t.voice.start();
  t.voice.enqueue(t.voice.current, new ArrayBuffer(6400));
  const release = t.voice.release();
  await tick();
  t.voice.cancel();
  finish({ state: 'final', text: '打开门' });
  await release;
  assert.deepEqual(t.submitted, []);
  assert.equal(t.ui.input.value, '原来的草稿');
  assert.equal(t.track.stopped, true);
});

test('release before microphone permission resolves never creates an upstream session', async () => {
  let grant;
  const t = setup({ media: () => new Promise(resolve => { grant = resolve; }) });
  const start = t.voice.start();
  await t.voice.release();
  grant(t.stream);
  await start;
  assert.equal(t.calls.filter(c => c.url.endsWith('/sessions')).length, 0);
  assert.equal(t.track.stopped, true);
  assert.deepEqual(t.submitted, []);
});

test('permission denial, silence, and network failures never submit a task', async () => {
  for (const options of [
    { media: async () => { const e = new Error(); e.name = 'NotAllowedError'; throw e; } },
    { finish: async () => ({ state: 'final', text: '' }) },
    { uploadError: true },
    { finish: async () => ({ state: 'error', error: 'provider disconnected' }) },
  ]) {
    const t = setup(options);
    await t.voice.start();
    if (t.voice.active) {
      t.voice.enqueue(t.voice.current, new ArrayBuffer(6400));
      await t.voice.release();
    }
    assert.deepEqual(t.submitted, []);
    assert.equal(t.voice.active, false);
    assert.equal(t.ui.input.value, '原来的草稿');
  }
});

test('long V hold starts once, repeats do not restart it, and keyup submits', async () => {
  const t = setup();
  const event = { code: 'KeyV', key: 'v', target: element(), preventDefault() {} };
  t.document.dispatch('keydown', event);
  t.document.dispatch('keydown', { ...event, repeat: true });
  await delay(220);
  assert.equal(t.calls.filter(c => c.url.endsWith('/sessions')).length, 1);
  t.voice.enqueue(t.voice.current, new ArrayBuffer(6400));
  t.document.dispatch('keyup', event);
  await tick();
  await tick();
  assert.equal(t.submitted.length, 1);
  t.voice.cancel();
});

test('quick V tap remains text and IME/modifier keys do not start microphone capture', async () => {
  const t = setup();
  t.ui.input.closest = selector => selector.startsWith('input') ? t.ui.input : null;
  const event = { code: 'KeyV', key: 'v', target: t.ui.input, preventDefault() {} };
  t.document.dispatch('keydown', event);
  t.document.dispatch('keyup', event);
  assert.equal(t.ui.input.value, '原来的草稿v');
  for (const flags of [{ isComposing: true }, { keyCode: 229 }, { ctrlKey: true }, { metaKey: true }]) {
    t.document.dispatch('keydown', { ...event, ...flags });
  }
  await delay(220);
  assert.equal(t.voice.active, false);
  assert.equal(t.calls.filter(c => c.url.endsWith('/sessions')).length, 0);
});

test('losing window focus stops microphone and does not submit', async () => {
  const t = setup();
  await t.voice.start();
  t.window.dispatch('blur');
  assert.equal(t.voice.active, false);
  assert.equal(t.track.stopped, true);
  assert.deepEqual(t.submitted, []);
});

for (const rate of [16000, 44100, 48000]) {
  test(`AudioWorklet resamples ${rate}Hz to five 200ms PCM16 chunks per second`, () => {
    let Processor;
    const packets = [];
    const context = vm.createContext({ sampleRate: rate,
      AudioWorkletProcessor: class { constructor() { this.port = { postMessage: p => packets.push(p) }; } },
      registerProcessor: (_, type) => { Processor = type; },
    });
    vm.runInContext(fs.readFileSync(path.join(root, 'workbench/voice-capture.js'), 'utf8'), context);
    const processor = new Processor();
    for (let offset = 0; offset < rate; offset += 128) {
      processor.process([[new Float32Array(Math.min(128, rate - offset)).fill(0.5)]]);
    }
    processor.port.onmessage({ data: 'stop' });
    const audio = packets.filter(p => p.audio);
    assert.equal(audio.length, 5);
    assert.equal(audio.reduce((sum, p) => sum + p.audio.byteLength, 0), 32000);
    for (const packet of audio) assert.equal(new DataView(packet.audio).getInt16(0, true), 16384);
    assert.equal(packets.at(-1).stopped, true);
  });
}
