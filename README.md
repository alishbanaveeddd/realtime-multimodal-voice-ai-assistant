# Real-Time Multimodal Voice AI Assistant

A production-oriented, real-time voice assistant built on **streaming everything**:
microphone audio streams to ASR, transcripts stream to an LLM, tokens stream to TTS,
and synthesized audio streams back to your speakers — over a single WebSocket
connection with structured events, latency telemetry, and deterministic offline tests.

```
Microphone / PCM file
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
Speaker playback (Windows) + latency metrics (TTFT, TTFB, total)
```

## Features

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
  zero extra audio dependencies (Windows SoundPlayer, FFmpeg fallback).

## Architecture

```
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
├── _smoke_e2e_live.py      # real end-to-end smoke test driven by a PCM file
└── _voice_mic_live.py      # live microphone voice assistant (same pipeline)
tests/                       # 214 deterministic offline tests
```

**Selection is environment-driven:** each factory picks the real adapter when its API
key is present, otherwise a deterministic fake — so the app always runs, and tests
never touch the network.

## Event contract (WebSocket `/ws`)

Binary messages are PCM16/16 kHz/mono audio. Text messages are JSON envelopes:

| Direction | Event | Purpose |
|---|---|---|
| → | `session.started` | emitted on connect |
| ← | binary audio frames | PCM16/16 kHz/mono mic audio |
| ← | `audio.eos` | end of utterance |
| → | `request.started` / `transcript.partial` / `transcript.final` | ASR results |
| → | `llm.token` | streamed LLM tokens |
| → | binary audio frames | synthesized TTS audio (PCM16/16 kHz/mono) |
| → | `tts.done` / `metrics` / `request.completed` | completion + latency |
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
| `GROQ_MODEL` *(optional)* | LLM | model override (default `openai/gpt-oss-20b`) |
| `ELEVENLABS_API_KEY` | TTS | real synthesis |
| `ELEVENLABS_VOICE_ID` | TTS | voice selection |

Without any key the pipeline runs with deterministic fakes — useful for development.

## Using the assistant

### Live microphone (Windows)

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

```powershell
.venv\Scripts\python -m pytest -q                        # 214 deterministic tests
.venv\Scripts\python -m ruff check src scripts tests     # lint
.venv\Scripts\python -m mypy                             # strict type check
```

All provider interactions in tests are fake; no test requires credentials or network.

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

## License

MIT — see [LICENSE](LICENSE).


