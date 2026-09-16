/**
 * Pure, testable visual-state model for the Mysa holographic orb.
 *
 * The WebGL layer owns no behaviour of its own: every frame it asks this
 * module how the orb should look for the current interaction phase and the
 * live microphone / playback levels, then eases toward that answer so phase
 * changes never pop. Keeping the maths here means it can be unit-tested
 * without a canvas or a GPU.
 */

export type OrbPhase = "idle" | "listening" | "thinking" | "speaking" | "error";

export interface OrbTargets {
  /** Organic surface deformation amplitude. */
  deform: number;
  /** Speed of the internal noise shimmer. */
  flow: number;
  /** Thin-film iridescence (pink / lavender / cyan interference). */
  iridescence: number;
  /** Soft pink illumination bleeding through the body. */
  blush: number;
  /** Slow whole-body breathing pulse. */
  pulse: number;
  /** How strongly live audio is allowed to drive the shape. */
  audioDrive: number;
  /** Restrained soft-rose error tint. */
  errorMix: number;
  /** Iridescent edge sparkle / orbiting motes. */
  sparkle: number;
}

export const ORB_PHASES: readonly OrbPhase[] = [
  "idle",
  "listening",
  "thinking",
  "speaking",
  "error",
] as const;

/** Clamp to the 0..1 range used by every target field. */
export function clamp01(value: number): number {
  if (Number.isNaN(value)) return 0;
  if (value < 0) return 0;
  if (value > 1) return 1;
  return value;
}

/**
 * Per-phase baseline. All values stay in 0..1 and describe character, not
 * timing: idle floats, listening opens up, thinking slows and shimmers,
 * speaking pulses, error desaturates into a restrained rose.
 */
export const PHASE_TARGETS: Record<OrbPhase, OrbTargets> = {
  idle: {
    deform: 0.34,
    flow: 0.22,
    iridescence: 0.72,
    blush: 0.46,
    pulse: 0.16,
    audioDrive: 0.0,
    errorMix: 0.0,
    sparkle: 0.35,
  },
  listening: {
    deform: 0.5,
    flow: 0.34,
    iridescence: 0.95,
    blush: 0.78,
    pulse: 0.3,
    audioDrive: 1.0,
    errorMix: 0.0,
    sparkle: 0.62,
  },
  thinking: {
    deform: 0.26,
    flow: 0.13,
    iridescence: 0.66,
    blush: 0.42,
    pulse: 0.44,
    audioDrive: 0.0,
    errorMix: 0.0,
    sparkle: 0.3,
  },
  speaking: {
    deform: 0.58,
    flow: 0.42,
    iridescence: 1.0,
    blush: 0.7,
    pulse: 0.5,
    audioDrive: 1.0,
    errorMix: 0.0,
    sparkle: 0.75,
  },
  error: {
    deform: 0.2,
    flow: 0.1,
    iridescence: 0.3,
    blush: 0.5,
    pulse: 0.2,
    audioDrive: 0.0,
    errorMix: 1.0,
    sparkle: 0.18,
  },
};

/** A neutral starting point for the eased runtime values. */
export const NEUTRAL_TARGETS: OrbTargets = PHASE_TARGETS.idle;

const TARGET_KEYS: readonly (keyof OrbTargets)[] = [
  "deform",
  "flow",
  "iridescence",
  "blush",
  "pulse",
  "audioDrive",
  "errorMix",
  "sparkle",
];

/**
 * Live audio level that may drive the shape for ``phase``.
 *
 * Listening only reacts to the microphone and speaking only to playback, so
 * the orb can never be animated by a stage that is not actually running —
 * silence really does mean stillness.
 */
export function audioDriveFor(
  phase: OrbPhase,
  mic: number,
  speak: number,
): number {
  if (phase === "listening") return clamp01(mic);
  if (phase === "speaking") return clamp01(speak);
  return 0;
}

/**
 * Resolve the instantaneous target for a phase plus the current audio levels.
 *
 * Audio can only ever *add* life (up to the phase's ``audioDrive`` budget):
 * deformation, shimmer, illumination and sparkle grow with the voice, while
 * the error tint and the phase's own resting character are preserved.
 */
export function resolveTargets(
  phase: OrbPhase,
  mic: number,
  speak: number,
): OrbTargets {
  const base = PHASE_TARGETS[phase];
  const drive = audioDriveFor(phase, mic, speak);
  return {
    deform: clamp01(base.deform + base.audioDrive * drive * 0.42),
    flow: clamp01(base.flow + base.audioDrive * drive * 0.3),
    iridescence: clamp01(base.iridescence + base.audioDrive * drive * 0.22),
    blush: clamp01(base.blush + base.audioDrive * drive * 0.34),
    pulse: clamp01(base.pulse + base.audioDrive * drive * 0.2),
    audioDrive: clamp01(base.audioDrive * (0.35 + 0.65 * drive)),
    errorMix: base.errorMix,
    sparkle: clamp01(base.sparkle + base.audioDrive * drive * 0.33),
  };
}

/** Frame-rate independent easing factor: ``rate`` is "fraction per second". */
export function smoothingFactor(rate: number, dt: number): number {
  const k = clamp01(rate);
  const step = clamp01(dt) * 60;
  return clamp01(1 - Math.pow(1 - k, step));
}

/** Ease a single value toward its target. */
export function easeValue(
  current: number,
  target: number,
  factor: number,
): number {
  const f = clamp01(factor);
  return current + (target - current) * f;
}

/** Ease every orb parameter one frame toward the resolved target. */
export function ease(
  current: OrbTargets,
  target: OrbTargets,
  factor: number,
): OrbTargets {
  const next = {} as OrbTargets;
  for (const key of TARGET_KEYS) {
    next[key] = easeValue(current[key], target[key], factor);
  }
  return next;
}
