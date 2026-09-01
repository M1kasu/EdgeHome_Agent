/* AudioWorklet: convert the actual device rate to 16 kHz mono PCM16.
 * A fractional box filter carries state across render quanta (including 44.1 kHz).
 * Raw audio is transferred to the main thread in 200 ms chunks, never stored.
 */
class VoiceCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.weight = 0;
    this.sum = 0;
    this.buffer = new ArrayBuffer(6400);
    this.view = new DataView(this.buffer);
    this.offset = 0;
    this.stopped = false;
    this.port.onmessage = ({ data }) => {
      if (data === "stop" && !this.stopped) {
        this.stopped = true;
        if (this.offset) {
          const tail = this.buffer.slice(0, this.offset);
          this.port.postMessage({ audio: tail }, [tail]);
        }
        this.port.postMessage({ stopped: true });
      }
    };
  }

  process(inputs) {
    if (this.stopped) return false;
    const input = inputs[0]?.[0];
    if (!input) return true;
    for (const value of input) {
      let remaining = 1;
      while (remaining > 1e-8) {
        const amount = Math.min(remaining, this.ratio - this.weight);
        this.sum += value * amount;
        this.weight += amount;
        remaining -= amount;
        if (this.weight >= this.ratio - 1e-8) {
          const sample = Math.max(-1, Math.min(1, this.sum / this.ratio));
          this.view.setInt16(this.offset, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
          this.offset += 2;
          this.sum = 0;
          this.weight = 0;
          if (this.offset === this.buffer.byteLength) {
            this.port.postMessage({ audio: this.buffer }, [this.buffer]);
            this.buffer = new ArrayBuffer(6400);
            this.view = new DataView(this.buffer);
            this.offset = 0;
          }
        }
      }
    }
    return true;
  }
}
registerProcessor("voice-capture", VoiceCaptureProcessor);
