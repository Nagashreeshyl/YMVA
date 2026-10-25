// Audio worklets for browser test calls.
//   microphone -> server: 16 kHz mono 16-bit PCM
//   server -> speaker:    24 kHz mono 16-bit PCM
const MIC_RATE = 16000;
const PLAYBACK_RATE = 24000;

class MicCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / MIC_RATE; // device samples per output sample (e.g. 3 at 48 kHz)
    this.acc = 0; // running sum for the current output sample (box low-pass filter)
    this.count = 0;
    this.pos = 0; // device samples consumed toward the next output sample
    this.out = new Int16Array(320); // 20 ms per message
    this.n = 0;
    this.level = 0;
    this.frames = 0;
  }

  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input) return true;
    let peak = 0;
    for (let i = 0; i < input.length; i++) {
      const s = input[i];
      peak = Math.max(peak, Math.abs(s));
      // Average the device samples that make up each 16 kHz sample: removes the high
      // frequencies that would otherwise alias into the band speech recognition listens to.
      this.acc += s;
      this.count++;
      this.pos++;
      if (this.pos >= this.ratio) {
        this.pos -= this.ratio;
        const v = this.acc / this.count;
        this.acc = 0;
        this.count = 0;
        this.out[this.n++] = Math.max(-1, Math.min(1, v)) * 0x7fff;
        if (this.n === this.out.length) {
          this.port.postMessage({ pcm: this.out.buffer }, [this.out.buffer]);
          this.out = new Int16Array(320);
          this.n = 0;
        }
      }
    }
    this.level = Math.max(peak, this.level * 0.9);
    if (++this.frames % 8 === 0) this.port.postMessage({ level: this.level });
    return true;
  }
}

class PcmPlayer extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = [];
    this.queued = 0; // samples waiting, at 24 kHz
    this.current = null;
    this.index = 0;
    this.step = PLAYBACK_RATE / sampleRate;
    this.playing = false;
    this.buffering = true;
    this.waited = 0;
    // Wait for ~80 ms of audio before starting a reply, so it plays smoothly instead of stuttering.
    this.prebuffer = PLAYBACK_RATE * 0.08;
    this.port.onmessage = (e) => {
      if (e.data === "clear") {
        this.queue = [];
        this.queued = 0;
        this.current = null;
        this.index = 0;
        this.buffering = true;
        return;
      }
      const int16 = new Int16Array(e.data);
      const f = new Float32Array(int16.length);
      for (let i = 0; i < int16.length; i++) f[i] = int16[i] / 0x8000;
      this.queue.push(f);
      this.queued += f.length;
    };
  }

  next() {
    this.current = this.queue.shift() || null;
    if (this.current) this.queued -= this.current.length;
    this.index = 0;
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    if (this.buffering && !this.current) {
      // Start once enough audio is queued, or after ~100 ms if a reply is very short.
      const waitedEnough = this.queued > 0 && ++this.waited * out.length >= sampleRate * 0.1;
      if (this.queued < this.prebuffer && !waitedEnough) {
        out.fill(0);
        return true;
      }
    }
    this.buffering = false;
    this.waited = 0;
    let produced = false;
    for (let o = 0; o < out.length; o++) {
      if (this.current && this.index >= this.current.length - 1) {
        const carry = this.index - (this.current.length - 1);
        const last = this.current[this.current.length - 1];
        this.next();
        this.index = carry;
        if (!this.current) {
          out[o] = last;
          this.buffering = true;
          continue;
        }
      } else if (!this.current) {
        this.next();
      }
      if (!this.current) {
        out[o] = 0;
        this.buffering = true;
        continue;
      }
      const i = Math.floor(this.index);
      const frac = this.index - i;
      const a = this.current[i];
      const b = i + 1 < this.current.length ? this.current[i + 1] : a;
      out[o] = a + (b - a) * frac;
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
