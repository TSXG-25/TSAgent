/** Minimal opt-in synth for UI feedback. Silent until the user enables it. */
export class SoundFX {
  enabled = false;
  private ctx?: AudioContext;

  constructor() {
    this.enabled = localStorage.getItem("tsa.sound") === "1";
  }

  toggle() {
    this.enabled = !this.enabled;
    localStorage.setItem("tsa.sound", this.enabled ? "1" : "0");
    if (this.enabled && !this.ctx) this.ctx = new AudioContext();
    if (this.ctx?.state === "suspended") void this.ctx.resume();
    if (this.enabled) this.hover();
    return this.enabled;
  }

  private blip(freq: number, dur: number, type: OscillatorType, gain = 0.04, slide = 0, delay = 0) {
    if (!this.enabled || !this.ctx) return;
    const t = this.ctx.currentTime + delay;
    const o = this.ctx.createOscillator();
    const g = this.ctx.createGain();
    o.type = type;
    o.frequency.setValueAtTime(freq, t);
    if (slide) o.frequency.exponentialRampToValueAtTime(Math.max(24, freq + slide), t + dur);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(gain, t + 0.015);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g).connect(this.ctx.destination);
    o.start(t);
    o.stop(t + dur + 0.05);
  }

  hover() { this.blip(1500, 0.06, "sine", 0.02); }
  select() { this.blip(340, 0.28, "sine", 0.05, -160); }
  lock() { this.blip(110, 0.85, "sine", 0.07, -60); this.blip(2100, 0.16, "sine", 0.04, 0, 0.8); }
  unlock() { this.blip(180, 0.5, "sine", 0.05, 400); }
  insert() { this.blip(220, 0.4, "sine", 0.06, -180); this.blip(1900, 0.14, "sine", 0.03, 0, 0.34); }

}
