import type { Metrics } from "../lib/protocol";

/**
 * Only what the browser can actually know:
 * - WebSocket: real connection state
 * - ASR/LLM/TTS: inferred from real events (transcripts/tokens/audio arriving)
 */
export function StatusPanel({
  connected,
  metrics,
}: {
  connected: boolean;
  metrics: Metrics | null;
}) {
  const asrOk = metrics?.asr_final_transcript_ms !== undefined;
  const llmOk = metrics?.llm_ttft_ms !== undefined;
  const ttsOk = metrics?.tts_ttfb_ms !== undefined;
  return (
    <div className="glass status">
      <h3>System</h3>
      <ul>
        <li>
          <span>WebSocket</span>
          <b className={connected ? "ok" : "bad"}>● {connected ? "Connected" : "Offline"}</b>
        </li>
        <li>
          <span>Deepgram (ASR)</span>
          <b className={asrOk ? "ok" : ""}>
            ● {asrOk ? "Responded" : connected ? "Waiting" : "—"}
          </b>
        </li>
        <li>
          <span>Groq (LLM)</span>
          <b className={llmOk ? "ok" : ""}>
            ● {llmOk ? "Responded" : connected ? "Waiting" : "—"}
          </b>
        </li>
        <li>
          <span>ElevenLabs (TTS)</span>
          <b className={ttsOk ? "ok" : ""}>
            ● {ttsOk ? "Responded" : connected ? "Waiting" : "—"}
          </b>
        </li>
      </ul>
    </div>
  );
}
