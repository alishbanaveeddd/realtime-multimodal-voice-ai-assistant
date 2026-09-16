/**
 * Mapping of the existing backend WebSocket protocol to Mysa UI state.
 *
 * Backend contract (unchanged):
 *  - binary frames: PCM16 / 16 kHz / mono audio (mic in, TTS out)
 *  - text frames: JSON envelopes
 *    { type, version, session_id, request_id, seq, timestamp, payload }
 *
 * Consumed events:
 *  session.started, request.started, transcript.partial, transcript.final,
 *  llm.token, tts.done, metrics, request.completed, request.error, llm.error,
 *  request.canceled, error, session.ended
 */

export type Phase = "idle" | "listening" | "thinking" | "speaking" | "error";

export type StageId =
  | "microphone"
  | "deepgram"
  | "groq"
  | "segmenter"
  | "elevenlabs"
  | "speaker";

export interface Metrics {
  asr_first_transcript_ms?: number;
  asr_final_transcript_ms?: number;
  llm_ttft_ms?: number;
  tts_ttfb_ms?: number;
  request_total_ms?: number;
}

export interface Message {
  role: "user" | "mysa";
  text: string;
  final: boolean;
}

export interface MysaState {
  connected: boolean;
  sessionId: string | null;
  phase: Phase;
  /** live partial transcript of the user's current utterance */
  partial: string;
  /** the conversation log */
  messages: Message[];
  /** "Do you need more information?" offer is pending */
  offerPending: boolean;
  stages: Record<StageId, "idle" | "active" | "done">;
  metrics: Metrics | null;
  error: string | null;
}

export const INITIAL_STATE: MysaState = {
  connected: false,
  sessionId: null,
  phase: "idle",
  partial: "",
  messages: [],
  offerPending: false,
  stages: {
    microphone: "idle",
    deepgram: "idle",
    groq: "idle",
    segmenter: "idle",
    elevenlabs: "idle",
    speaker: "idle",
  },
  metrics: null,
  error: null,
};

/** A backend JSON envelope as it arrives on the wire. */
export interface Envelope {
  type: string;
  payload?: Record<string, unknown>;
  session_id?: string | null;
}

function setStage(
  state: MysaState,
  id: StageId,
  status: "idle" | "active" | "done",
): MysaState {
  return { ...state, stages: { ...state.stages, [id]: status } };
}

const FOLLOWUP_OFFER = "do you need more information";

/** Pure reducer: one backend event → next UI state. */
export function reduceEvent(state: MysaState, env: Envelope): MysaState {
  const payload = (env.payload ?? {}) as Record<string, unknown>;
  switch (env.type) {
    case "session.started":
      return {
        ...INITIAL_STATE,
        connected: true,
        sessionId: env.session_id ?? null,
      };

    case "session.ended":
      return { ...INITIAL_STATE };

    case "request.started":
      return setStage(
        { ...state, partial: "", metrics: null, error: null, phase: "thinking" },
        "deepgram",
        "active",
      );

    case "transcript.partial":
      return {
        ...state,
        partial: String(payload.text ?? ""),
        phase: "listening",
      };

    case "transcript.final": {
      const text = String(payload.text ?? "");
      let next = setStage({ ...state }, "deepgram", "done");
      if (text) {
        next = {
          ...next,
          messages: [
            ...next.messages,
            { role: "user", text, final: true },
          ],
          partial: "",
        };
      }
      return next;
    }

    case "llm.token": {
      const token = String(payload.text ?? "");
      const messages = [...state.messages];
      const last = messages[messages.length - 1];
      if (last && last.role === "mysa" && !last.final) {
        messages[messages.length - 1] = { ...last, text: last.text + token };
      } else {
        messages.push({ role: "mysa", text: token, final: false });
      }
      let next = setStage(
        { ...state, messages, phase: "speaking" },
        "groq",
        "active",
      );
      next = setStage(setStage(next, "segmenter", "active"), "elevenlabs", "active");
      return next;
    }

    case "tts.done": {
      const messages = [...state.messages];
      const last = messages[messages.length - 1];
      if (last && last.role === "mysa" && !last.final) {
        messages[messages.length - 1] = { ...last, final: true };
      }
      const offerPending = (last?.text ?? "")
        .toLowerCase()
        .includes(FOLLOWUP_OFFER);
      return setStage(
        { ...state, messages, offerPending },
        "elevenlabs",
        "done",
      );
    }

    case "metrics":
      return { ...state, metrics: payload as Metrics };

    case "request.completed": {
      const phases: MysaState = {
        ...state,
        phase: "idle",
      };
      return setStage(
        setStage(setStage(phases, "groq", "done"), "segmenter", "done"),
        "speaker",
        "done",
      );
    }

    case "request.canceled":
      return { ...state, phase: "idle", error: "The request was canceled." };

    case "llm.error":
    case "request.error": {
      const kind = String(payload.kind ?? "server");
      const message = String(payload.message ?? "Something went wrong.");
      return { ...state, phase: "error", error: `${kind}: ${message}` };
    }

    case "error":
      return {
        ...state,
        phase: "error",
        error: String(payload.message ?? "Protocol error."),
      };

    default:
      return state;
  }
}
