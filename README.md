# MYSA — Real-Time Multimodal Voice AI Assistant

**MYSA** is a real-time voice AI companion: a holographic web client over a
production-oriented streaming backend. Microphone audio streams to ASR, transcripts
stream to an LLM, tokens stream to TTS, and synthesized audio streams back to your
speakers — over a single WebSocket connection with structured events, latency
telemetry, and deterministic offline tests.

> Talk to Mysa in the browser: a soft, iridescent holographic orb that listens,
> thinks, and speaks — driven entirely by real pipeline state.

```
MYSA browser client (React + TypeScript + Three.js)  ──or──  PCM file / mic script
   │  binary PCM16 · 16 kHz · mono frames (10 ms)
   ▼
FastAPI WebSocket Gateway ──► Deepgram ASR ──► partial/final transcripts
   │
   ├──► Groq LLM (OpenAI-compatible SSE) ──► llm.token events
   │         │
   │         └──► TextSegmenter (sentence units) ──► ElevenLabs TTS
   │                                                      │
   └──◄── binary TTS audio frames ◄── PCM16/16 kHz/mono ──┘
   ▼
Browser / speaker playback + latency metrics (TTFT, TTFB, total)
```

## Features

- **MYSA holographic web client** — React + TypeScript + Three.js. An organic,
  translucent *pearl* orb with soft pink/lavender iridescence, glassmorphic panels,
  a live transcript, an elegant pipeline indicator, and latency telemetry — all bound
  to real backend events (no simulated state, no fake text).
- **Fully streaming pipeline** — audio in, transcripts, tokens, and audio out are all
  streamed; TTS starts speaking sentences while the LLM is still generating.
- **Real providers behind clean interfaces** — Deepgram ASR, Groq LLM (OpenAI-compatible),
  ElevenLabs TTS. Each vendor is isolated in a single adapter module behind
  `ASRProvider` / `LLMProvider` / `TTSProvider` interfaces.
- **Deterministic fakes** for every provider, so the entire test suite runs offline
  with zero credentials and zero network.
- **Structured WebSocket event contract** — versioned envelopes, monotonic sequence
  numbers, per-session state machine, validation errors, cancellation, and timeouts.
- **Conversational voice UX** — concise spoken answers, a natural
  *"Do you need more information?"* follow-up, and "yes"/"no" handling with minimal
  in-memory conversation state (no database, no Redis).
- **Latency telemetry** — `asr_first_transcript_ms`, `asr_final_transcript_ms`,
  `llm_ttft_ms`, `tts_ttfb_ms`, `request_total_ms` emitted with every request.
- **Client-side playback** — buffered response played through Windows speakers with
  zero extra audio dependencies (Windows SoundPlayer, FFmpeg fallback); in the browser,
  Mysa's speech is decoded and played with the Web Audio API.

## Architecture

```
frontend/                      # MYSA web client (React + TypeScript + Three.js)
├── src/App.tsx               # screen composition: orb + status + mic + conversation
├── src/components/           # VoiceOrb, Conversation, Pipeline, StatusPanel
├── src/orb/                  # HoloOrb (Three.js) + orbState + orbShaders
├── src/lib/mysaClient.ts     # WebSocket client + real application state
├── src/lib/protocol.ts       # event envelope parsing (shared contract types)
├── src/lib/mic.ts            # browser mic capture → PCM16/16 kHz/mono framing
├── src/lib/playback.ts       # Web Audio playback of streamed TTS audio
└── src/styles.css            # global styles
src/assistant/
├── asr/           # ASR interface, Deepgram adapter, deterministic fake, audio contract
├── llm/           # LLM interface, Groq (OpenAI-compatible) adapter, fake, factory
├── tts/           # TTS interface, ElevenLabs streaming adapter, fake, factory
├── transport/     # FastAPI app + WebSocket gateway (protocol & orchestration)
├── core/          # session & request lifecycle managers
├── events/        # event catalog, envelopes, state machine, validation
├── text.py        # TextSegmenter: token stream → sentence segments for TTS
└── latency.py     # per-request latency measurement
scripts/
├── run_server.py            # serve the backend for the browser client (port 8000)
├── _smoke_e2e_live.py       # real end-to-end smoke test driven by a PCM file
└── _voice_mic_live.py       # live microphone voice assistant (same pipeline)
tests/                       # 214 deterministic offline tests
```

**Selection is environment-driven:** each factory picks the real adapter when its API
key is present, otherwise a deterministic fake — so the app always runs, and tests
never touch the network.

## Frontend — MYSA web client

The browser client is a single-page React + TypeScript app with a Three.js holographic
orb. It connects to the existing backend over one WebSocket and reflects **real pipeline
state only** — never simulated text, never fake events.

### How it fits together

```
Browser
  │
  ├── src/App.tsx              orchestrates the screen
  │     │
  │     ├── src/components/VoiceOrb.tsx   CSS 3D orb, amplitude-driven
  │     ├── src/components/Conversation.tsx  transcript log
  │     ├── src/components/Pipeline.tsx     stage indicators
  │     └── src/components/StatusPanel.tsx  connected / phase / error
  │
  ├── src/orb/                 Three.js holographic orb (alternative visual layer)
  │     ├── HoloOrb.ts         MysaOrb: WebGL renderer + noise shaders + dust
  │     ├── orbState.ts        pure, testable state→uniform model
  │     └── orbShaders.ts      vertex/fragment/dust GLSL
  │
  └── src/lib/
        ├── mysaClient.ts      single WebSocket connection + real app state
        ├── protocol.ts        event envelope contract → UI state reducer
        ├── mic.ts             browser mic → PCM16/16 kHz/mono framing
        └── playback.ts        Web Audio TTS playback + level analyser
```

### Data flow (one conversation turn)

1. **Connection.** `MysaClient` opens `ws://host/ws` (or `wss:` on HTTPS) and listens
   for `session.started`. The `StatusPanel` shows *connected* and the orb settles into
   its idle character.

2. **Capture.** On "Push to talk" the frontend calls `startTurn()`. `MicCapture` grabs
   the mic via `getUserMedia`, runs an `AudioContext` with an `AnalyserNode` for live
   amplitude, and a `ScriptProcessorNode` that resamples whatever the hardware gives
   (usually 44.1/48 kHz Float32) down to **16 kHz mono**, quantizes to **PCM16 LE**,
   and frames it into **320-byte / 10 ms** messages. Every frame is sent as a binary
   WebSocket message.

3. **Streaming in.** Backend events arrive as JSON envelopes (`transcript.partial`,
   `transcript.final`, `llm.token`, `tts.done`, `metrics`, `request.completed`,
   `request.error`, …). The pure reducer in `protocol.ts` turns each envelope into the
   next `MysaState` — phase, recording flag, partial transcript, message log, stage
   statuses, metrics, error. `MysaClient` emits the new state to every listener.

4. **Streaming out.** TTS audio comes back as **binary PCM16/16 kHz/mono** frames on the
   same WebSocket. `TtsPlayer` schedules each chunk on a single 16 kHz `AudioContext` in
   order with no gaps, so Mysa's voice plays exactly as synthesized. An `AnalyserNode`
   after the gain stage gives the real playback level that drives the orb's "speaking"
   animation — nothing is faked.

5. **Visual state.** The orb is the visual anchor. Two implementations exist:
   - **`VoiceOrb`** (`src/components/VoiceOrb.tsx`) — a pure HTML/CSS 3D orb built from
     layered gradient volumes (atmospheric glow, three drifting colour volumes, a central
     core, a glass highlight, a hairline rim, and state-specific effects). No canvas, no
     SVG, no animation library. Amplitude is written to a `--zy-orb-level` CSS variable
     from one `requestAnimationFrame` loop running an envelope follower (attack ≈ 70 ms,
     release ≈ 260 ms, noise gate 0.02), so per-frame animation never touches React state.
   - **`HoloOrb`** (`src/orb/HoloOrb.ts`) — a Three.js holographic orb: deformed soft
     sphere, fractal-noise surface ripple, thin-film iridescence (cyan/magenta/peach),
     dark-magenta striations, color-fringed edges, and floating dust motes. Every animation
     parameter is resolved by the pure `orbState` module and eased each frame.

   Both read the same real amplitude sources: microphone level while listening, playback
   level while speaking, and zero otherwise — so silence really does mean stillness.

6. **Conversation log.** `Conversation` shows the message history built from
   `transcript.final` (user turns) and `llm.token` + `tts.done` (Mysa turns). Tokens
   stream in live as `llm.token` events arrive, so the answer types out as it is generated.

7. **Pipeline indicator.** `Pipeline` renders each stage (`microphone`, `deepgram`, `groq`,
   `segmenter`, `elevenlabs`, `speaker`) as idle / active / done, so the user can see
   exactly where the request is.

8. **Completion.** `request.completed` returns the UI to idle; if TTS is still playing,
   `MysaClient` holds the phase on `speaking` until the scheduled audio finishes, then
   transitions to idle. Errors (`request.error`, `llm.error`, `error`) push the phase to
   `error` and surface the message in `StatusPanel`.

## Event contract (WebSocket `/ws`)
| → | `request.error` / `llm.error` / `request.canceled` / `error` | failures |

## Quick start

```powershell
# 1. Python 3.11+ and a virtual environment
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"

# 2. Configure credentials (never committed)
copy .env.example .env   # then fill in the real values

# 3. Run the offline test suite (no credentials needed)
.venv\Scripts\python -m pytest -q
```

### Environment variables

| Variable | Used by | Required for |
|---|---|---|
| `DEEPGRAM_API_KEY` | ASR | real transcription |
| `GROQ_API_KEY` | LLM | real generation (Groq, OpenAI-compatible endpoint) |
| `GROQ_BASE_URL` *(optional)* | LLM | point the OpenAI-compatible adapter at another compatible endpoint (default `https://api.groq.com/openai/v1`) |
| `GROQ_MODEL` *(optional)* | LLM | model override (default `openai/gpt-oss-20b`) |
| `ELEVENLABS_API_KEY` | TTS | real synthesis |
| `ELEVENLABS_VOICE_ID` | TTS | voice selection |

The entrypoint scripts (`scripts/run_server.py`, `scripts/_smoke_e2e_live.py`,
`scripts/_voice_mic_live.py`) load the local `.env` into the environment before
the provider factories run, so the `.env` you created above actually selects the
real providers. A variable already exported in your shell always wins over the
file. The loader never prints or logs values — only the names it loaded.

To confirm which providers are actually active (never prints keys):

```powershell
.venv\Scripts\python -c "from assistant.config.env_file import load_env_file as e; e(); from assistant.asr.factory import build_asr_provider as a; from assistant.llm.factory import build_llm_provider as l; from assistant.tts.factory import build_tts_provider as t; print(type(a()).__name__, type(l()).__name__, type(t()).__name__)"
```

Without any key the pipeline runs with deterministic fakes — useful for
development. Any provider that has no credential falls back to its fake
*individually*, so `DeepgramASRProvider FakeLLMProvider ElevenLabsTTSProvider`
means the LLM key is the missing one.

## Using the assistant

### MYSA web app (browser)

Two terminals — backend first, then the dev server:

```powershell
# terminal 1 — existing backend on http://127.0.0.1:8000
.venv\Scripts\python scripts\run_server.py

# terminal 2 — MYSA web client on http://localhost:5173
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173**, click **Talk to Mysa**, speak, then click **Stop**.
Mysa transcribes you, thinks, and speaks the answer through your browser — the orb
reacts to real microphone amplitude while you talk and to real TTS audio while Mysa
talks. Ask **"yes"** after the *"Do you need more information?"* offer to hear more.

The Vite dev server proxies `/ws` and `/health` to `127.0.0.1:8000`, so the browser
talks to a single origin (no CORS) and the backend stays untouched. The browser
captures microphone audio at whatever rate the device provides, resamples it, and
sends **PCM16 / 16 kHz / mono** 320-byte frames — the exact existing wire contract.

Production build:

```powershell
cd frontend
npm run build     # type-check + bundle into frontend/dist
```

### Live microphone (Windows, Python)

```powershell
.venv\Scripts\python scripts\_voice_mic_live.py
```

Speak your question, press **Enter** to stop recording, and the answer is spoken
back through your speakers. Ask follow-ups in the same session:

> **You:** What is the capital of Pakistan?
> **Assistant:** The capital of Pakistan is Islamabad. It is the country's political
> and administrative center. Do you need more information?
> **You:** Yes.
> **Assistant:** Islamabad was purpose-built in the 1960s near the Margalla Hills...
> Do you need more information?
> **You:** No thanks.
> **Assistant:** Okay.

### PCM file end-to-end smoke test

```powershell
.venv\Scripts\python scripts\_smoke_e2e_live.py question.pcm
```

`question.pcm` must be PCM16 / 16 kHz / mono. On success:
`LIVE SMOKE: PASS <request_id> metrics={...}`.

## Testing & quality

Backend (Python):

```powershell
.venv\Scripts\python -m pytest -q                        # 231 deterministic tests
.venv\Scripts\python -m ruff check src scripts tests     # lint
.venv\Scripts\python -m mypy                             # strict type check
```

Frontend (TypeScript):

```powershell
cd frontend
npm test          # 40 Vitest tests (protocol parsing, mic framing, client state, orb)
npm run build     # tsc --noEmit + vite build
```

All provider interactions in tests are fake; no test requires credentials or network.
The frontend tests cover the PCM16/16 kHz/mono conversion, frame sizing, event
parsing, state-machine transitions, the orb's state→uniform wiring, and a static
GLSL contract check on the orb shaders — all without a browser or a running server.
Credentials are also covered on the backend: a configured key can never reach a
client-facing error message (`tests/test_credential_redaction.py`).

## Design principles

- **Stream at every stage** — waiting for a full turn before speaking is the #1
  latency killer in voice UX; nothing here batches.
- **Vendors behind interfaces** — swapping Deepgram/Groq/ElevenLabs touches exactly
  one adapter file each.
- **Fail fast, fail loud** — provider failures become typed exceptions translated to
  structured `request.error` events; credentials are never logged (error sanitization).
- **Bounded waits** — every long provider pole has a configurable timeout so a hung
  provider cannot stall a request.
- **Minimal state** — conversation memory is a handful of fields per connection;
  no external state store.
## Screenshots

### MYSA web client — main screen

The holographic orb centered on the stage, with the *"How can I help?"* hero, the live waveform, the mic button, and the partial-transcript hint below it.

<img width="768" height="810" alt="MYSA main screen: holographic orb, hero text, waveform, and mic button" src="https://github.com/user-attachments/assets/4d079c9c-73b0-41a2-ba54-ee64ef7ab1a8" />

### MYSA web client — full layout

The full lower panel open: conversation log, pipeline stages (Microphone → Deepgram → Groq → Text Segmenter → ElevenLabs → Speaker), the system status panel, and live latency metrics (TTFT, TTFB, total).

<img width="1459" height="859" alt="MYSA full layout: conversation log, pipeline stages, system status, and latency metrics" src="https://github.com/user-attachments/assets/d266a4cd-54d2-4605-abaa-71aa833b464b" />

## License

MIT — see [LICENSE](LICENSE).


