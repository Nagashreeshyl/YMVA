// Audio worklets for browser test calls. Audio is exchanged with the server as 16 kHz mono
// 16-bit PCM; these convert to and from the audio device's own sample rate.
const WIRE_RATE = 16000;

class MicCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = sampleRate / WIRE_RATE; // input samples per output sample
    this.pos = 0;
    this.out = new Int16Array(320); // 20 ms per message
    this.n = 0;
    this.level = 0;
    this.frames = 0;
  }

  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input) return true;
    let peak = 0;
    for (let i = 0; i < input.length; i++) peak = Math.max(peak, Math.abs(input[i]));
    this.level = Math.max(peak, this.level * 0.9);
    // Linear-interpolation downsampling to 16 kHz.
    while (this.pos < input.length - 1) {
      const i = Math.floor(this.pos);
      const frac = this.pos - i;
      const s = input[i] * (1 - frac) + input[i + 1] * frac;
      this.out[this.n++] = Math.max(-1, Math.min(1, s)) * 0x7fff;
      if (this.n === this.out.length) {
        this.port.postMessage({ pcm: this.out.buffer }, [this.out.buffer]);
        this.out = new Int16Array(320);
        this.n = 0;
      }
      this.pos += this.step;
    }
    this.pos -= input.length;
    if (++this.frames % 8 === 0) this.port.postMessage({ level: this.level });
    return true;
  }
}

class PcmPlayer extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = [];
    this.current = null;
    this.index = 0; // fractional read position in the current chunk (16 kHz samples)
    this.step = WIRE_RATE / sampleRate;
    this.playing = false;
    this.port.onmessage = (e) => {
      if (e.data === "clear") {
        this.queue = [];
        this.current = null;
        this.index = 0;
        return;
      }
      const int16 = new Int16Array(e.data);
      const f = new Float32Array(int16.length);
      for (let i = 0; i < int16.length; i++) f[i] = int16[i] / 0x8000;
      this.queue.push(f);
    };
  }

  next() {
    this.current = this.queue.shift() || null;
    this.index = 0;
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    let produced = false;
    for (let o = 0; o < out.length; o++) {
      if (this.current && this.index >= this.current.length - 1) {
        const carry = this.index - (this.current.length - 1);
        this.next();
        this.index = carry;
      } else if (!this.current) {
        this.next();
      }
      if (!this.current) {
        out[o] = 0;
        continue;
      }
      const i = Math.floor(this.index);
      const frac = this.index - i;
      out[o] = this.current[i] * (1 - frac) + (this.current[i + 1] ?? this.current[i]) * frac;
      this.index += this.step;
      produced = true;
    }
    if (produced !== this.playing) {
      this.playing = produced;
      this.port.postMessage({ playing: produced });
    }
    return true;
  }
}

registerProcessor("mic-capture", MicCapture);
registerProcessor("pcm-player", PcmPlayer);
