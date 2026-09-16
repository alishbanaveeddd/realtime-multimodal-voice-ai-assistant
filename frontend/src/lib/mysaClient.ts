/**
 * MysaClient — the single WebSocket connection to the existing backend.
 *
 * - sends mic frames as binary + `audio.eos` envelopes (monotonic seq)
 * - consumes every backend event through the pure reducer
 * - feeds binary TTS frames to the Web Audio player
 * - reports real state only (no fabricated values)
 */

import {
  INITIAL_STATE,
  reduceEvent,
  type Envelope,
  type MysaState,
} from "./protocol";
import { MicCapture, type MicSender } from "./mic";
import { TtsPlayer } from "./playback";

export type Listener = (state: MysaState) => void;

const EVENT_VERSION = "1.0";
const NO_SENDER: MicSender = {
  sendFrame: () => undefined,
  onLevel: () => undefined,
  onError: () => undefined,
};

export class MysaClient {
  private ws: WebSocket | null = null;
  private seq = 0;
  private mic = new MicCapture();
  private player = new TtsPlayer();
  private state: MysaState = INITIAL_STATE;
  private listeners = new Set<Listener>();
  private url: string;
  private micLevel = 0;

  constructor(url = "/ws") {
    this.url = url;
  }

  getState(): MysaState {
    return this.state;
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    fn(this.state);
    return () => {
      this.listeners.delete(fn);
    };
  }

  private emit(state: MysaState): void {
    this.state = state;
    for (const fn of this.listeners) fn(state);
  }

  private patch(partial: Partial<MysaState>): void {
    this.emit({ ...this.state, ...partial });
  }

  connect(): void {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}${this.url}`);
    this.ws = ws;
    ws.binaryType = "arraybuffer";
    ws.onopen = () => {
      this.patch({ connected: true });
    };
    ws.onclose = () => {
      this.emit({
        ...INITIAL_STATE,
        error: this.state.error ?? "Connection to Mysa was lost.",
      });
    };
    ws.onerror = () => {
      this.patch({
        error:
          "Could not reach the Mysa backend. Start it with scripts/run_server.py.",
      });
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") {
        this.onEvent(JSON.parse(ev.data) as Envelope);
        return;
      }
      // Binary = TTS audio (PCM16/16 kHz/mono) → real browser playback.
      this.player.playChunk(new Uint8Array(ev.data as ArrayBuffer));
    };
  }

  private onEvent(env: Envelope): void {
    const next = reduceEvent(this.state, env);
    if (next !== this.state) this.emit(next);
    if (String(env.type) === "request.completed") {
      // Let scheduled TTS audio finish playing before returning to idle.
      this.patch({ phase: this.player.isPlaying ? "speaking" : "idle" });
      const watch = (): void => {
        if (this.player.isPlaying) {
          window.setTimeout(watch, 120);
        } else if (this.state.phase === "speaking") {
          this.patch({ phase: "idle" });
        }
      };
      window.setTimeout(watch, 120);
    }
  }

  /** Start one conversational turn: open the mic and begin streaming. */
  async startTurn(): Promise<void> {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      this.patch({ error: "Not connected to the Mysa backend." });
      return;
    }
    if (!this.state.sessionId) {
      this.patch({ error: "Mysa is still connecting — try again in a moment." });
      return;
    }
    this.player.ensureStarted();
    // `seq` stays monotonic for the LIFE of the connection: the backend
    // rejects any envelope whose seq is <= the last accepted one (§8.3),
    // and it is not reset per request. Resetting it here would make the
    // second turn (e.g. the "yes" follow-up) fail validation.
    // The conversation log persists across turns (same as the backend session),
    // so a "yes" follow-up still shows the question it refers to.
    this.patch({
      phase: "listening",
      partial: "",
      offerPending: false,
      metrics: null,
      error: null,
      stages: {
        microphone: "active",
        deepgram: "idle",
        groq: "idle",
        segmenter: "idle",
        elevenlabs: "idle",
        speaker: "idle",
      },
    });
    const sender: MicSender = {
      sendFrame: (frame) => {
        if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(frame);
      },
      onLevel: (level) => {
        this.micLevel = level;
      },
      onError: (message) => {
        this.patch({ phase: "error", error: message });
      },
    };
    await this.mic.start(sender);
  }

  /** End the turn: flush mic, send audio.eos. */
  stopTurn(): void {
    this.mic.stop({
      sendFrame: (frame) => {
        if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(frame);
      },
      onLevel: () => undefined,
      onError: () => undefined,
    });
    this.patch({
      phase: "thinking",
      stages: { ...this.state.stages, microphone: "done", deepgram: "active" },
    });
    this.sendEnvelope("audio.eos", {});
  }

  cancelTurn(): void {
    this.mic.stop(NO_SENDER);
    this.sendEnvelope("request.canceled", {});
  }

  private sendEnvelope(type: string, payload: Record<string, unknown>): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.seq += 1;
    this.ws.send(
      JSON.stringify({
        type,
        version: EVENT_VERSION,
        session_id: this.state.sessionId,
        request_id: null,
        seq: this.seq,
        timestamp: Date.now() / 1000,
        payload,
      }),
    );
  }

  /** Live amplitude sources for the orb/waveform (real data only). */
  levels(): { mic: () => number; speak: () => number; speaking: () => boolean } {
    return {
      mic: () => this.micLevel,
      speak: () => this.player.currentLevel(),
      speaking: () => this.player.isPlaying,
    };
  }

  dispose(): void {
    this.mic.stop(NO_SENDER);
    this.player.dispose();
    this.ws?.close();
    this.ws = null;
  }
}
