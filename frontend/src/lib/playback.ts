/**
 * Browser playback of Mysa's TTS audio.
 *
 * The backend streams binary PCM16 / 16 kHz / mono frames. We schedule each
 * chunk as an AudioBuffer on a single 16 kHz AudioContext, in order, with no
 * gaps — the user hears Mysa exactly as synthesized. An AnalyserNode after the
 * gain stage provides the real playback amplitude that drives the orb's
 * "speaking" animation.
 */

export class TtsPlayer {
  private ctx: AudioContext | null = null;
  private gain: GainNode | null = null;
  private analyser: AnalyserNode | null = null;
  private nextTime = 0;
  private analyserBuf: Float32Array<ArrayBuffer> | null = null;

  /** Must be called from a user gesture at least once. */
  ensureStarted(): void {
    if (this.ctx) {
      void this.ctx.resume();
      return;
    }
    const ctx = new AudioContext({ sampleRate: 16000 });
    const gain = ctx.createGain();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    gain.connect(analyser);
    analyser.connect(ctx.destination);
    this.ctx = ctx;
    this.gain = gain;
    this.analyser = analyser;
    this.analyserBuf = new Float32Array(analyser.fftSize);
  }

  /** Queue one raw PCM16 LE frame chunk for gapless playback. */
  playChunk(pcm: Uint8Array): void {
    if (!this.ctx || !this.gain || pcm.length === 0) return;
    const samples = Math.floor(pcm.length / 2);
    const buffer = this.ctx.createBuffer(1, samples, this.ctx.sampleRate);
    const data = buffer.getChannelData(0);
    const view = new DataView(pcm.buffer, pcm.byteOffset, samples * 2);
    for (let i = 0; i < samples; i++) {
      data[i] = view.getInt16(i * 2, true) / 32768;
    }
    const src = this.ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(this.gain);
    const now = this.ctx.currentTime;
    if (this.nextTime < now + 0.02) this.nextTime = now + 0.02;
    src.start(this.nextTime);
    this.nextTime += buffer.duration;
  }

  /** Real output amplitude (0..1) while Mysa is speaking. */
  currentLevel(): number {
    if (!this.analyser || !this.analyserBuf) return 0;
    this.analyser.getFloatTimeDomainData(this.analyserBuf);
    let sum = 0;
    for (let i = 0; i < this.analyserBuf.length; i++) {
      sum += this.analyserBuf[i] * this.analyserBuf[i];
    }
    return Math.min(1, Math.sqrt(sum / this.analyserBuf.length) * 4);
  }

  /** Whether scheduled audio is still playing (for the "speaking" state). */
  get isPlaying(): boolean {
    return !!this.ctx && this.nextTime > this.ctx.currentTime + 0.03;
  }

  /** Drop any buffered schedule (e.g. after cancel). */
  reset(): void {
    this.nextTime = this.ctx ? this.ctx.currentTime : 0;
  }

  dispose(): void {
    void this.ctx?.close();
    this.ctx = null;
    this.gain = null;
    this.analyser = null;
    this.analyserBuf = null;
  }
}
