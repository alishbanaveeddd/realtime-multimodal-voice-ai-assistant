import { describe, expect, it } from "vitest";
import {
  ORB_PHASES,
  PHASE_TARGETS,
  audioDriveFor,
  clamp01,
  ease,
  easeValue,
  resolveTargets,
  smoothingFactor,
} from "./orbState";

describe("orb visual state", () => {
  it("defines a complete, in-range target for every phase", () => {
    for (const phase of ORB_PHASES) {
      const t = PHASE_TARGETS[phase];
      for (const value of Object.values(t)) {
        expect(value).toBeGreaterThanOrEqual(0);
        expect(value).toBeLessThanOrEqual(1);
      }
    }
  });

  it("only lets the running stage drive the shape", () => {
    // Listening reacts to the mic and ignores playback; speaking the reverse.
    expect(audioDriveFor("listening", 0.8, 0.9)).toBeCloseTo(0.8);
    expect(audioDriveFor("speaking", 0.9, 0.4)).toBeCloseTo(0.4);
    // Idle / thinking / error are never animated by audio.
    expect(audioDriveFor("idle", 1, 1)).toBe(0);
    expect(audioDriveFor("thinking", 1, 1)).toBe(0);
    expect(audioDriveFor("error", 1, 1)).toBe(0);
  });

  it("clamps out-of-range audio levels instead of overshooting", () => {
    expect(audioDriveFor("listening", 4, 0)).toBe(1);
    expect(audioDriveFor("listening", -3, 0)).toBe(0);
    expect(clamp01(Number.NaN)).toBe(0);
  });

  it("grows deformation and illumination with the voice", () => {
    const quiet = resolveTargets("listening", 0, 0);
    const loud = resolveTargets("listening", 1, 0);
    expect(loud.deform).toBeGreaterThan(quiet.deform);
    expect(loud.blush).toBeGreaterThan(quiet.blush);
    expect(loud.sparkle).toBeGreaterThan(quiet.sparkle);
    expect(loud.flow).toBeGreaterThan(quiet.flow);
  });

  it("stays within 0..1 even at full drive", () => {
    for (const phase of ORB_PHASES) {
      const t = resolveTargets(phase, 1, 1);
      for (const value of Object.values(t)) {
        expect(value).toBeGreaterThanOrEqual(0);
        expect(value).toBeLessThanOrEqual(1);
      }
    }
  });

  it("keeps the error tint exclusive to the error phase", () => {
    expect(resolveTargets("error", 1, 1).errorMix).toBe(1);
    for (const phase of ORB_PHASES.filter((p) => p !== "error")) {
      expect(resolveTargets(phase, 1, 1).errorMix).toBe(0);
    }
  });

  it("eases monotonically toward the target without overshooting", () => {
    const from = resolveTargets("idle", 0, 0);
    const to = resolveTargets("speaking", 1, 1);
    const factor = smoothingFactor(0.12, 1 / 60);
    const first = ease(from, to, factor);
    expect(first.deform).toBeGreaterThan(from.deform);
    expect(first.deform).toBeLessThan(to.deform);
    let current = first;
    for (let i = 0; i < 600; i += 1) current = ease(current, to, factor);
    expect(current.deform).toBeCloseTo(to.deform, 3);
  });

  it("never moves the eased value past its target", () => {
    expect(easeValue(0.2, 0.9, 1)).toBeCloseTo(0.9);
    expect(easeValue(0.2, 0.9, 0)).toBeCloseTo(0.2);
    // A factor of exactly 1 must land on the target, not beyond it.
    expect(easeValue(0.9, 0.2, 1)).toBeCloseTo(0.2);
  });

  it("keeps smoothing bounded and frame-rate aware", () => {
    expect(smoothingFactor(0.5, 0)).toBe(0);
    expect(smoothingFactor(0.5, 1)).toBeGreaterThan(smoothingFactor(0.5, 1 / 60));
    expect(smoothingFactor(4, 1)).toBeLessThanOrEqual(1);
  });
});
