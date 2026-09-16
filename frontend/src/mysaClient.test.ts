/**
 * Regression tests for the Mysa client's outbound envelope contract.
 *
 * The backend (unchanged) enforces a per-CONNECTION monotonic `seq` and
 * requires the real session_id on every envelope. The client originally reset
 * `seq` to 0 at the start of each turn, which made the second turn (the "yes"
 * follow-up) fail with: non-monotonic seq 1 after 1.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MysaClient } from "./lib/mysaClient";

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static readonly OPEN = 1;
  static readonly CLOSED = 3;

  readyState = FakeWebSocket.OPEN;
  binaryType = "";
  sent: unknown[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this);
  }

  send(data: unknown): void {
    this.sent.push(data);
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
  }

  emit(data: unknown): void {
    this.onmessage?.({ data });
  }
}

interface SentEnvelope {
  type: string;
  version: string;
  session_id: string | null;
  seq: number;
}

function lastSocket(): FakeWebSocket {
  return FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
}

function sessionStarted(ws: FakeWebSocket, sessionId = "ses_test"): void {
  ws.emit(
    JSON.stringify({
      type: "session.started",
      version: "1.0",
      session_id: sessionId,
      request_id: null,
      seq: 1,
      timestamp: 1,
      payload: {},
    }),
  );
}

function sentEnvelopes(ws: FakeWebSocket): SentEnvelope[] {
  return ws.sent
    .filter((m): m is string => typeof m === "string")
    .map((m) => JSON.parse(m) as SentEnvelope);
}

describe("MysaClient outbound envelope contract", () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.stubGlobal("location", { protocol: "http:", host: "localhost:5173" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends audio.eos with the real session id from session.started", () => {
    const client = new MysaClient();
    client.connect();
    const ws = lastSocket();
    sessionStarted(ws, "ses_abc123");

    client.stopTurn();

    const envs = sentEnvelopes(ws);
    expect(envs).toHaveLength(1);
    expect(envs[0].type).toBe("audio.eos");
    expect(envs[0].session_id).toBe("ses_abc123");
    expect(envs[0].version).toBe("1.0");
  });

  it("keeps seq monotonic across turns (regression: 2nd turn was rejected)", () => {
    const client = new MysaClient();
    client.connect();
    const ws = lastSocket();
    sessionStarted(ws);

    client.stopTurn(); // turn 1
    client.stopTurn(); // turn 2 — a "yes" follow-up on the SAME connection

    const envs = sentEnvelopes(ws);
    expect(envs).toHaveLength(2);
    expect(envs[0].seq).toBe(1);
    // The backend rejects seq <= last accepted seq, so this MUST increase.
    expect(envs[1].seq).toBeGreaterThan(envs[0].seq);
  });

  it("never sends an envelope with a null session id", async () => {
    const client = new MysaClient();
    client.connect();
    const ws = lastSocket();

    // No session.started yet: starting a turn must not emit session_id: null.
    await client.startTurn();

    expect(ws.sent).toHaveLength(0);
    expect(client.getState().error).toMatch(/still connecting/i);
  });

  it("treats binary TTS frames as audio (never as terminal text)", () => {
    const client = new MysaClient();
    client.connect();
    const ws = lastSocket();
    sessionStarted(ws);

    // A binary frame with no audio context yet is safely ignored, not printed.
    expect(() => ws.emit(new Uint8Array([1, 2, 3, 4]).buffer)).not.toThrow();
    expect(sentEnvelopes(ws)).toHaveLength(0);
  });

  it("reports a lost connection to the user", () => {
    const client = new MysaClient();
    client.connect();
    const ws = lastSocket();

    ws.onclose?.();

    expect(client.getState().connected).toBe(false);
    expect(client.getState().error).toBeTruthy();
  });
});
