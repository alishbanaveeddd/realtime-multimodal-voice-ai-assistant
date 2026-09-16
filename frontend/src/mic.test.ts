import { describe, expect, it } from "vitest";
import {
  floatToPcm16,
  framePcm16,
  resampleTo16k,
  rmsLevel,
  TARGET_RATE,
} from "./lib/mic";

describe("mic → backend audio conversion", () => {
  it("targets PCM16 / 16 kHz / mono", () => {
    expect(TARGET_RATE).toBe(16000);
  });

  it("resamples 48 kHz → 16 kHz (exact 3:1 decimation)", () => {
    const input = new Float32Array(480);
    for (let i = 0; i < input.length; i++) input[i] = Math.sin(i * 0.1);
    const out = resampleTo16k(input, 48000);
    expect(out.length).toBe(160);
    // Sinusoid shape is preserved (peaks align within one output sample).
    expect(Math.max(...out)).toBeGreaterThan(0.8);
    expect(Math.min(...out)).toBeLessThan(-0.8);
  });

  it("leaves 16 kHz input untouched", () => {
    const input = new Float32Array([0.1, -0.2, 0.3]);
    expect(resampleTo16k(input, 16000)).toBe(input);
  });

  it("quantizes to signed 16-bit little-endian", () => {
    const pcm = floatToPcm16(new Float32Array([0, 1, -1]));
    expect(pcm.length).toBe(6);
    // 0 → 0x0000; 1 → 0x7FFF (LE: FF 7F); -1 → 0x8001-ish (LE: 01 80)
    expect([...pcm.slice(0, 2)]).toEqual([0, 0]);
    expect([...pcm.slice(2, 4)]).toEqual([0xff, 0x7f]);
    expect([...pcm.slice(4, 6)]).toEqual([0x01, 0x80]);
  });

  it("clamps out-of-range samples before quantization", () => {
    const pcm = floatToPcm16(new Float32Array([2.0, -2.0]));
    expect([...pcm.slice(0, 2)]).toEqual([0xff, 0x7f]);
    expect([...pcm.slice(2, 4)]).toEqual([0x01, 0x80]);
  });

  it("frames PCM into 320-byte wire frames (10 ms @ 16 kHz)", () => {
    const pcm = floatToPcm16(new Float32Array(500)); // 1000 bytes
    const frames = framePcm16(pcm);
    expect(frames).toHaveLength(4);
    expect(frames.slice(0, 3).every((f) => f.length === 320)).toBe(true);
    expect(frames[3].length).toBe(320); // 1000 % 320 = 40 → zero-padded
    const total = frames.reduce((n, f) => n + f.length, 0);
    expect(total).toBe(4 * 320);
  });

  it("rms level reflects real amplitude and saturates at 1", () => {
    expect(rmsLevel(new Float32Array(1000))).toBe(0);
    expect(rmsLevel(new Float32Array(1000).fill(0.5))).toBe(1);
    const quiet = rmsLevel(new Float32Array(1000).fill(0.05));
    expect(quiet).toBeGreaterThan(0);
    expect(quiet).toBeLessThan(1);
  });
});
