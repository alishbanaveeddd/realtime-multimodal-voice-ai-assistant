import { useEffect, useRef, useState } from "react";
import { MysaClient } from "./lib/mysaClient";
import { MysaOrb, type OrbPhase } from "./orb/MysaOrb";
import { Conversation } from "./components/Conversation";
import { Pipeline } from "./components/Pipeline";
import { StatusPanel } from "./components/StatusPanel";
import "./styles.css";

const PHASE_COPY: Record<string, string> = {
  idle: "Talk to Mysa",
  listening: "Mysa is listening...",
  thinking: "Mysa is thinking...",
  speaking: "Mysa is speaking...",
  error: "Something interrupted us",
};

function OrbCanvas({
  client,
  phase,
}: {
  client: MysaClient;
  phase: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const orbRef = useRef<MysaOrb | null>(null);
  useEffect(() => {
    if (!canvasRef.current) return;
    const levels = client.levels();
    const orb = new MysaOrb(canvasRef.current, {
      mic: levels.mic,
      speak: levels.speak,
    });
    orbRef.current = orb;
    return () => {
      orb.dispose();
      orbRef.current = null;
    };
  }, [client]);
  useEffect(() => {
    orbRef.current?.setPhase(phase as OrbPhase);
  }, [phase]);
  return <canvas ref={canvasRef} className="orb-canvas" aria-hidden="true" />;
}

function Waveform({
  client,
  phase,
}: {
  client: MysaClient;
  phase: string;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const levels = client.levels();
    let raf = 0;
    const history: number[] = new Array(96).fill(0);
    const draw = (): void => {
      raf = requestAnimationFrame(draw);
      const w = (canvas.width = canvas.clientWidth * 2);
      const h = (canvas.height = 80);
      ctx.clearRect(0, 0, w, h);
      const level =
        phase === "listening"
          ? levels.mic()
          : phase === "speaking"
            ? levels.speak()
            : 0;
      history.push(level);
      history.shift();
      ctx.strokeStyle = "rgba(224, 160, 220, 0.55)";
      ctx.lineWidth = 2.5;
      ctx.lineCap = "round";
      ctx.beginPath();
      const mid = h / 2;
      history.forEach((v, i) => {
        const x = (i / (history.length - 1)) * w;
        const amp = v * h * 0.42 + Math.sin(i * 0.35) * 2;
        if (i === 0) ctx.moveTo(x, mid - amp);
        else ctx.lineTo(x, mid - amp);
      });
      ctx.stroke();
    };
    draw();
    return () => cancelAnimationFrame(raf);
  }, [client, phase]);
  return <canvas ref={ref} className="waveform" aria-hidden="true" />;
}

export default function App() {
  const clientRef = useRef<MysaClient | null>(null);
  const [, force] = useState(0);
  const [showTech, setShowTech] = useState(false);
  if (!clientRef.current) clientRef.current = new MysaClient();
  const client = clientRef.current;

  useEffect(() => {
    const unsub = client.subscribe(() => force((n) => n + 1));
    client.connect();
    return () => {
      unsub();
      client.dispose();
    };
  }, [client]);

  const s = client.getState();
  const busy = s.phase === "listening";

  const onMicClick = (): void => {
    if (busy) client.stopTurn();
    else if (s.phase === "idle" || s.phase === "error") void client.startTurn();
  };
  const onMicKey = (e: React.KeyboardEvent): void => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onMicClick();
    }
  };

  return (
    <div className="mysa-root">
      <div className="bg-glow bg-glow-1" />
      <div className="bg-glow bg-glow-2" />
      <header className="brand">
        <h1>MYSA</h1>
        <p>your real-time AI voice companion</p>
      </header>

      <main className="stage">
        <div className="orb-wrap">
          <OrbCanvas client={client} phase={s.phase} />
        </div>

        <section className="status-line" aria-live="polite">
          {s.phase === "listening" && s.partial ? (
            <p className="partial">“{s.partial}”</p>
          ) : (
            <p className={s.phase}>{PHASE_COPY[s.phase] ?? s.phase}</p>
          )}
          <Waveform client={client} phase={s.phase} />
        </section>

        <button
          type="button"
          className={`mic-btn ${s.phase}`}
          onClick={onMicClick}
          onKeyDown={onMicKey}
          disabled={s.phase === "thinking" || s.phase === "speaking"}
          aria-label={
            busy ? "Stop recording" : "Start speaking to Mysa"
          }
        >
          <span className="mic-icon" aria-hidden="true">🎙</span>
          <span>{busy ? "Stop" : PHASE_COPY[s.phase]}</span>
        </button>
        {s.phase === "listening" && (
          <p className="hint">press the button or say nothing… then press Stop</p>
        )}
        {s.phase === "idle" && s.offerPending && (
          <p className="hint">Mysa is waiting — answer “yes” for more, or ask something new.</p>
        )}
        {s.error && (
          <p className="error" role="alert">{s.error}</p>
        )}

        <div className="lower">
          <Conversation messages={s.messages} partial={s.partial} phase={s.phase} />
          <div className="side">
            <Pipeline stages={s.stages} />
            {s.metrics && (
              <div className="glass metrics">
                <h3>Latency</h3>
                <ul>
                  {Object.entries(s.metrics).map(([k, v]) => (
                    <li key={k}>
                      <span>{k.replace(/_/g, " ").replace(" ms", "")}</span>
                      <b>{Math.round(v as number)} ms</b>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <button type="button" className="tech-toggle" onClick={() => setShowTech((v) => !v)}>
              {showTech ? "hide" : "show"} system status
            </button>
            {showTech && <StatusPanel connected={s.connected} metrics={s.metrics} />}
          </div>
        </div>
      </main>
    </div>
  );
}
