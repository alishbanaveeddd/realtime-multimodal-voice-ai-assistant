import { describe, expect, it } from "vitest";
import {
  INITIAL_STATE,
  reduceEvent,
  type MysaState,
} from "./lib/protocol";

function env(
  type: string,
  payload: Record<string, unknown> = {},
  session_id = "ses_1",
) {
  return { type, payload, session_id };
}

describe("mysa protocol reducer", () => {
  it("session.started connects and resets state", () => {
    const s = reduceEvent(
      { ...INITIAL_STATE, error: "old" },
      env("session.started"),
    );
    expect(s.connected).toBe(true);
    expect(s.sessionId).toBe("ses_1");
    expect(s.error).toBeNull();
  });

  it("request.started activates deepgram and thinks", () => {
    const s = reduceEvent(INITIAL_STATE, env("request.started"));
    expect(s.phase).toBe("thinking");
    expect(s.stages.deepgram).toBe("active");
    expect(s.stages.microphone).toBe("idle");
  });

  it("transcript.partial shows the live user transcript", () => {
    const s = reduceEvent(
      INITIAL_STATE,
      env("transcript.partial", { text: "what is the capital of pakistan" }),
    );
    expect(s.phase).toBe("listening");
    expect(s.partial).toBe("what is the capital of pakistan");
  });

  it("transcript.final adds the user's message verbatim", () => {
    const s = reduceEvent(
      INITIAL_STATE,
      env("transcript.final", { text: "What is the capital of Pakistan?" }),
    );
    expect(s.messages).toEqual([
      { role: "user", text: "What is the capital of Pakistan?", final: true },
    ]);
    expect(s.stages.deepgram).toBe("done");
  });

  it("llm.token streams Mysa's answer incrementally", () => {
    let s: MysaState = reduceEvent(INITIAL_STATE, env("llm.token", { text: "Islamabad" }));
    s = reduceEvent(s, env("llm.token", { text: " is the capital." }));
    expect(s.messages).toHaveLength(1);
    expect(s.messages[0].role).toBe("mysa");
    expect(s.messages[0].text).toBe("Islamabad is the capital.");
    expect(s.messages[0].final).toBe(false);
    expect(s.phase).toBe("speaking");
    expect(s.stages.groq).toBe("active");
    expect(s.stages.elevenlabs).toBe("active");
  });

  it("tts.done finalizes the answer and detects the follow-up offer", () => {
    let s: MysaState = reduceEvent(
      INITIAL_STATE,
      env("llm.token", {
        text: "The capital is Islamabad. Do you need more information?",
      }),
    );
    s = reduceEvent(s, env("tts.done"));
    expect(s.messages[0].final).toBe(true);
    expect(s.offerPending).toBe(true);
    expect(s.stages.elevenlabs).toBe("done");
  });

  it("tts.done without the offer does not set offerPending", () => {
    let s: MysaState = reduceEvent(
      INITIAL_STATE,
      env("llm.token", { text: "The capital is Islamabad." }),
    );
    s = reduceEvent(s, env("tts.done"));
    expect(s.offerPending).toBe(false);
  });

  it("metrics carries only real values the backend sent", () => {
    const s = reduceEvent(
      INITIAL_STATE,
      env("metrics", { llm_ttft_ms: 312, request_total_ms: 1600 }),
    );
    expect(s.metrics).toEqual({ llm_ttft_ms: 312, request_total_ms: 1600 });
  });

  it("request.completed returns to idle with all stages done", () => {
    let s: MysaState = reduceEvent(INITIAL_STATE, env("llm.token", { text: "x" }));
    s = reduceEvent(s, env("request.completed"));
    expect(s.phase).toBe("idle");
    expect(s.stages.groq).toBe("done");
    expect(s.stages.segmenter).toBe("done");
    expect(s.stages.speaker).toBe("done");
  });

  it("request.error surfaces a soft error", () => {
    const s = reduceEvent(
      INITIAL_STATE,
      env("request.error", { kind: "tts", message: "synthesis failed" }),
    );
    expect(s.phase).toBe("error");
    expect(s.error).toBe("tts: synthesis failed");
  });

  it("request.canceled does not fabricate an error", () => {
    const s = reduceEvent(INITIAL_STATE, env("request.canceled"));
    expect(s.phase).toBe("idle");
    expect(s.error).toContain("canceled");
  });

  it("unknown events are ignored (state unchanged)", () => {
    const s = reduceEvent(INITIAL_STATE, env("future.event"));
    expect(s).toBe(INITIAL_STATE);
  });
});
