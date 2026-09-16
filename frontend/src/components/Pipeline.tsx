import type { StageId } from "../lib/protocol";

const STAGES: { id: StageId; label: string }[] = [
  { id: "microphone", label: "Microphone" },
  { id: "deepgram", label: "Deepgram" },
  { id: "groq", label: "Groq" },
  { id: "segmenter", label: "Text Segmenter" },
  { id: "elevenlabs", label: "ElevenLabs" },
  { id: "speaker", label: "Speaker" },
];

export function Pipeline({
  stages,
}: {
  stages: Record<StageId, "idle" | "active" | "done">;
}) {
  return (
    <div className="glass pipeline" aria-label="How Mysa thinks">
      <h3>How Mysa thinks</h3>
      <ol>
        {STAGES.map(({ id, label }, i) => (
          <li key={id} className={stages[id]}>
            <span className="dot" aria-hidden="true" />
            <span className="label">{label}</span>
            {stages[id] === "done" && (
              <span className="check" aria-hidden="true">✓</span>
            )}
            {i < STAGES.length - 1 && (
              <span className="arrow" aria-hidden="true">↓</span>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
