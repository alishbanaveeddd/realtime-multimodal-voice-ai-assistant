/**
 * Microphone capture for Mysa.
 *
 * The browser captures whatever the hardware gives (typically 44.1/48 kHz
 * Float32). The backend contract is PCM16 / 16 kHz / mono, so every chunk is
 * explicitly resampled + quantized here before framing into 320-byte
 * (10 ms) messages. The backend audio contract is NOT modified.
 */

export const TARGET_RATE = 16000;
export const FRAME_BYTES = 320; // 160 samples = 10 ms

/** Linear-interpolation resample of mono Float32 audio to 16 kHz. */
export function resampleTo16k(input: Float32Array, srcRate: number): Float32Array {
  if (srcRate === TARGET_RATE) return input;
  if (input.length === 0) return new Float32Array(0);
  const ratio = srcRate / TARGET_RATE;
  const outLen = Math.max(1, Math.floor(input.length / ratio));
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const pos = i * ratio;
    const i0 = Math.floor(pos);
    const frac = pos - i0;
    const a = input[i0];
    const b = input[Math.min(i0 + 1, input.length - 1)];
    out[i] = a + (b - a) * frac;
  }
  return out;
}

/** Quantize mono Float32 [-1, 1] to PCM16 little-endian bytes. */
export function floatToPcm16(input: Float32Array): Uint8Array {
  const out = new Uint8Array(input.length * 2);
  const view = new DataView(out.buffer);
  for (let i = 0; i < input.length; i++) {
    let s = input[i];
    if (s > 1) s = 1;
    else if (s < -1) s = -1;
    view.setInt16(i * 2, Math.round(s * 32767), true);
  }
  return out;
}

/** Root-mean-square amplitude of a mono Float32 chunk (0..1). */
export function rmsLevel(input: Float32Array): number {
  if (input.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < input.length; i++) sum += input[i] * input[i];
  return Math.min(1, Math.sqrt(sum / input.length) * 4);
}

/** Split PCM16 bytes into 320-byte wire frames (zero-padding the last). */
export function framePcm16(pcm: Uint8Array): Uint8Array[] {
  const frames: Uint8Array[] = [];
  for (let off = 0; off < pcm.length; off += FRAME_BYTES) {
    const end = Math.min(off + FRAME_BYTES, pcm.length);
    if (end - off === FRAME_BYTES) {
      frames.push(pcm.slice(off, end));
    } else {
      const frame = new Uint8Array(FRAME_BYTES);
      frame.set(pcm.subarray(off, end));
      frames.push(frame);
    }
  }
  return frames;
}

export interface MicSender {
  /** Send one 320-byte PCM16 frame to the backend. */
  sendFrame(frame: Uint8Array): void;
  /** Report live microphone level (0..1) for orb/waveform animation. */
  onLevel(level: number): void;
  /** Report a capture/conversion error. */
  onError(message: string): void;
}

export class MicCapture {
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: ScriptProcessorNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private tail = new Uint8Array(0);

  async start(sender: MicSender): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) {
      sender.onError(
        "This browser does not support microphone capture (or the page is not on a secure origin).",
      );
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch (err) {
      const e = err as DOMException;
      sender.onError(
        e.name === "NotAllowedError"
          ? "Microphone permission was denied. Allow microphone access and try again."
          : e.name === "NotFoundError"
            ? "No microphone was found on this device."
            : `Could not start the microphone: ${e.message}`,
      );
      return;
    }
    this.stream = stream;
    const ctx = new AudioContext();
    this.ctx = ctx;
    this.source = ctx.createMediaStreamSource(stream);

    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    this.source.connect(analyser);
    const levelBuf = new Float32Array(analyser.fftSize);

    const node = ctx.createScriptProcessor(4096, 1, 1);
    this.node = node;
    node.onaudioprocess = (ev) => {
      const input = ev.inputBuffer.getChannelData(0);
      const levelBufCopy = new Float32Array(input);
      // Live level for orb/waveform (pre-resample is fine for amplitude).
      analyser.getFloatTimeDomainData(levelBuf);
      sender.onLevel(rmsLevel(levelBuf));

      // Convert: resample to 16 kHz → PCM16 → 320-byte frames.
      const resampled = resampleTo16k(levelBufCopy, ctx.sampleRate);
      const pcm = floatToPcm16(resampled);
      const buf = new Uint8Array(this.tail.length + pcm.length);
      buf.set(this.tail);
      buf.set(pcm, this.tail.length);
      const whole = Math.floor(buf.length / FRAME_BYTES) * FRAME_BYTES;
      for (let off = 0; off < whole; off += FRAME_BYTES) {
        sender.sendFrame(buf.slice(off, off + FRAME_BYTES));
      }
      this.tail = buf.slice(whole);
    };
    // Muted output path: we only consume the input stream.
    const mute = ctx.createGain();
    mute.gain.value = 0;
    this.source.connect(node);
    node.connect(mute);
    mute.connect(ctx.destination);
  }

  /** Flush any buffered partial frame and stop capture. */
  stop(sender: MicSender): void {
    if (this.tail.length > 0) {
      const frame = new Uint8Array(FRAME_BYTES);
      frame.set(this.tail);
      sender.sendFrame(frame);
      this.tail = new Uint8Array(0);
    }
    this.node?.disconnect();
    this.source?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    void this.ctx?.close();
    this.node = null;
    this.source = null;
    this.stream = null;
    this.ctx = null;
  }
}
