/**
 * Pure, DOM-free tests for the VoiceOrb amplitude/size/label contracts.
 * The vitest suite runs in a node environment, so the React component itself
 * is exercised through these exported helpers (its render output is CSS, not
 * logic) — same strategy as the rest of the Mysa suite.
 */

import { describe, expect, it } from "vitest";
import {
  DEFAULT_ORB_LABELS,
  ORB_STATES,
  clampLevel,
  envelopeStep,
  gateLevel,
  resolveSizePx,
} from "./VoiceOrb";

describe("amplitude clamping", () => {
  it("treats undefined, null and NaN as silence", () => {
    expect(clampLevel(undefined)).toBe(0);
    expect(clampLevel(null)).toBe(0);
    expect(clampLevel(Number.NaN)).toBe(0);
  });

  it("clamps out-of-range values into 0..1", () => {
    expect(clampLevel(-0.5)).toBe(0);
    expect(clampLevel(1.5)).toBe(1);
    expect(clampLevel(0.42)).toBe(0.42);
  });
});

describe("noise gate", () => {
  it("gates everything at or below 0.02 to silence", () => {
    expect(gateLevel(0)).toBe(0);
    expect(gateLevel(0.02)).toBe(0);
    expect(gateLevel(0.019)).toBe(0);
  });

  it("rescales the 0.02..1 span onto the full 0..1 range", () => {
    expect(gateLevel(1)).toBeCloseTo(1);
    expect(gateLevel(0.52)).toBeCloseTo((0.52 - 0.02) / 0.98);
    expect(gateLevel(0.52)).toBeGreaterThan(0);
    expect(gateLevel(0.52)).toBeLessThan(1);
  });
});

describe("envelope follower", () => {
  it("attacks fast (70 ms) when rising toward the goal", () => {
    const up = envelopeStep(0, 1, 0.07);
    const down = envelopeStep(1, 0, 0.07);
    // Same 70 ms step: rising covers more distance than falling (release 260 ms).
    expect(up).toBeGreaterThan(0.5);
    expect(down).toBeGreaterThan(0.5); // release is slow, so it barely falls
    expect(up).toBeCloseTo(1 - Math.exp(-1)); // ~0.632
    expect(up).toBeGreaterThan(1 - down); // rising distance > falling distance
  });

  it("releases slowly (260 ms) after the sound stops", () => {
    const after = envelopeStep(1, 0, 0.1); // 100 ms step, within the dt clamp
    expect(after).toBeGreaterThan(0.5); // release is slow, barely decays in 100 ms
    expect(after).toBeCloseTo(Math.exp(-0.1 / 0.26)); // ~0.681 remaining
  });

  it("clamps dt to 100 ms so a stalled tab cannot overshoot", () => {
    const stalled = envelopeStep(0, 1, 5);
    const clamped = envelopeStep(0, 1, 0.1);
    expect(stalled).toBeCloseTo(clamped);
    expect(stalled).toBeLessThan(1);
  });

  it("never moves away from the goal", () => {
    let value = 0;
    for (let i = 0; i < 60; i++) {
      const next = envelopeStep(value, 0.8, 1 / 60);
      expect(next).toBeGreaterThanOrEqual(value);
      value = next;
    }
    expect(value).toBeGreaterThan(0.7);
  });
});

describe("sizes", () => {
  it("maps presets to their pixel diameters", () => {
    expect(resolveSizePx("sm")).toBe(64);
    expect(resolveSizePx("md")).toBe(112);
    expect(resolveSizePx("lg")).toBe(176);
    expect(resolveSizePx("xl")).toBe(248);
  });

  it("clamps numeric sizes to a ~24px minimum", () => {
    expect(resolveSizePx(320)).toBe(320);
    expect(resolveSizePx(10)).toBe(24);
  });
});

describe("labels and states", () => {
  it("ships exactly the five documented states", () => {
    expect(ORB_STATES).toEqual([
      "idle",
      "listening",
      "thinking",
      "speaking",
      "muted",
    ]);
  });

  it("uses the documented default labels", () => {
    expect(DEFAULT_ORB_LABELS.idle).toBe("Ready");
    expect(DEFAULT_ORB_LABELS.listening).toBe("Listening");
    expect(DEFAULT_ORB_LABELS.thinking).toBe("Thinking");
    expect(DEFAULT_ORB_LABELS.speaking).toBe("Speaking");
    expect(DEFAULT_ORB_LABELS.muted).toBe("Microphone off");
  });
});
