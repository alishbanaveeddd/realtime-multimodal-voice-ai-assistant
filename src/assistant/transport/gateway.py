"""The WebSocket gateway (architecture.md §6, §8) + M2 ASR stage.

The gateway owns one connection's protocol behavior:

* on connect it creates a session and emits ``session.started``;
* text messages are parsed as JSON, validated at the boundary, and routed —
  invalid events are rejected with a structured ``error`` event
  (``kind = validation``); ``audio.eos`` finishes the active ASR request;
* ``audio.frame`` (binary) messages are validated against the PCM16/16 kHz/mono
  contract and fed to the injected :class:`ASRProvider`. Transcription results
  are streamed back as ``transcript.partial``/``transcript.final`` events with
  the correct ``session_id``/``request_id``; ``request.error`` carries provider
  failures; a disconnect cancels the active request (``request.canceled``) and
  ends the session (``session.ended``).

The class is driven by any WebSocket server — the FastAPI app in
:mod:`assistant.transport.app` wires it to the real socket, while tests drive it
with fake send callbacks and a deterministic fake ASR (no network).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from assistant.asr.audio import AudioFrameInvalidError, validate_audio_frame
from assistant.asr.interface import (
    ASRProvider,
    ASRProviderError,
    ASRResult,
    ASRStream,
    TranscriptKind,
)
from assistant.core.request import RequestManager
from assistant.core.session import SessionManager
from assistant.events.catalog import EventOrigin
from assistant.events.envelope import EVENT_VERSION, EventEnvelope
from assistant.events.state import SessionState
from assistant.events.validator import validate_event, validation_error_payload
from assistant.latency import RequestLatency
from assistant.llm.interface import (
    LLMProvider,
    LLMProviderError,
    LLMStream,
)
from assistant.text import TextSegmenter
from assistant.tts.interface import (
    TTSProvider,
    TTSProviderError,
    TTSStream,
)

#: Callback used to send a text (JSON) event back to the client.
SendText = Callable[[str], Awaitable[None]]
#: Callback used to send raw binary audio to the client (reserved for TTS in M4).
SendBinary = Callable[[bytes], Awaitable[None]]

logger = logging.getLogger(__name__)


# == timeout configuration (M7 §4) ============================================
# Bounded waits on the long provider poles (LLM/TTS) so a hung provider cannot
# stall a request forever. Pure stdlib (asyncio.wait_for) — no new dependency.

@dataclass(frozen=True, slots=True)
class _Timeouts:
    """Per-operation timeouts in seconds; overridable via env for tuning."""

    llm_wait_seconds: float = 60.0
    tts_send_seconds: float = 30.0
    tts_finish_seconds: float = 10.0
    tts_wait_seconds: float = 60.0


def _env_timeout(name: str, default: float) -> float:
    """Read a positive float timeout from the environment, or fall back."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


class _TimeoutError(RuntimeError):
    """Raised when an awaited provider operation exceeds its time budget."""

    def __init__(self, *, kind: str, operation: str, limit: float) -> None:
        super().__init__(f"{kind} {operation} timed out after {limit:.1f}s")
        self.kind = kind
        self.operation = operation
        self.limit = limit


async def _with_timeout(
    coro: Awaitable[Any],
    *,
    timeout: float,
    kind: str,
    operation: str,
) -> Any:
    """Run ``coro`` bounded by ``timeout``; raise :class:`_TimeoutError` on expiry."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except TimeoutError:
        raise _TimeoutError(kind=kind, operation=operation, limit=timeout) from None



class InboundKind(StrEnum):
    """How an inbound message was handled by the gateway."""

    SESSION_STARTED = "session.started"
    SESSION_ENDED = "session.ended"
    VALIDATION_ERROR = "validation_error"
    AUDIO_INVALID = "audio_invalid"
    ACCEPTED = "accepted"
    BINARY_AUDIO = "binary_audio"
    REQUEST_FINISHED = "request.finished"
    REQUEST_ERROR = "request.error"


@dataclass(slots=True)
class InboundResult:
    """Result of processing one inbound message (for routing/observability)."""

    kind: InboundKind
    event_type: str | None = None
    session_id: str | None = None
    detail: str = ""
    seq: int = 0


class SessionConnection:
    """Protocol handler for a single WebSocket connection."""

    def __init__(
        self,
        sessions: SessionManager,
        requests: RequestManager,
        send_text: SendText,
        asr: ASRProvider,
        llm: LLMProvider,
        tts: TTSProvider,
        *,
        send_binary: SendBinary | None = None,
        clock: Callable[[], float] | None = None,
        timeouts: _Timeouts | None = None,
    ) -> None:
        import time as _time

        self._sessions = sessions
        self._requests = requests
        self._send_text = send_text
        self._send_binary = send_binary
        self._asr = asr
        self._llm = llm
        self._tts = tts
        self._clock: Callable[[], float] = clock or _time.monotonic
        self._timeouts = timeouts or _Timeouts(
            llm_wait_seconds=_env_timeout("VOICE_LLM_TIMEOUT_SECONDS", 60.0),
            tts_send_seconds=_env_timeout("VOICE_TTS_SEND_TIMEOUT_SECONDS", 30.0),
            tts_finish_seconds=_env_timeout("VOICE_TTS_FINISH_TIMEOUT_SECONDS", 10.0),
            tts_wait_seconds=_env_timeout("VOICE_TTS_WAIT_TIMEOUT_SECONDS", 60.0),
        )
        self._session_id: str | None = None
        self._out_seq = 0
        self._last_in_seq = -1
        self._binary_frames = 0
        self._request_id: str | None = None
        self._asr_stream: ASRStream | None = None
        self._asr_last_seq = -1
        self._final_transcript: str | None = None
        self._llm_stream: LLMStream | None = None
        self._latency = RequestLatency()
        self._segmenter = TextSegmenter()
        self._tts_stream: TTSStream | None = None
        self._tts_failed = False
        self._terminal = False
# -- lifecycle -----------------------------------------------------------
    async def on_connect(self) -> str:
        """Create the session, emit ``session.started``, return the session id."""
        session = self._sessions.create()
        self._session_id = session.session_id
        self._out_seq = 0
        await self._send_text(self._emit(session.session_id, "session.started"))
        return session.session_id

    async def on_close(self) -> None:
        """Cancel any active request, end the session, emit ``session.ended``."""
        if self._session_id is None:
            return
        await self._cancel_active_request()
        session = self._sessions.end(self._session_id)
        if session.state is SessionState.ENDED:
            try:
                await self._send_text(self._emit(self._session_id, "session.ended"))
            except Exception:
                # Best-effort teardown: the socket may already be gone.
                pass

    # -- inbound -------------------------------------------------------------
    async def on_message(self, payload: str | bytes) -> InboundResult:
        """Handle one inbound message (text JSON envelope or binary audio)."""
        if isinstance(payload, bytes):
            return await self._handle_binary(payload)
        return await self._handle_text(payload)

    async def _handle_binary(self, payload: bytes) -> InboundResult:
        self._binary_frames += 1
        sid = self._session_id
        if sid is None:
            return InboundResult(
                kind=InboundKind.VALIDATION_ERROR, detail="no active session"
            )

        try:
            validate_audio_frame(payload)
        except AudioFrameInvalidError as exc:
            return await self._reject(
                str(exc), result_kind=InboundKind.AUDIO_INVALID
            )

        if not await self._ensure_request():
            # A provider failure already surfaced as request.error.
            return InboundResult(
                kind=InboundKind.REQUEST_ERROR,
                session_id=sid,
                detail="ASR stream could not be opened",
            )
        assert self._asr_stream is not None

        try:
            await self._asr_stream.send_audio(payload)
        except ASRProviderError as exc:
            await self._fail_request(exc)
            return InboundResult(
                kind=InboundKind.REQUEST_ERROR,
                session_id=sid,
                detail=str(exc),
            )

        return InboundResult(
            kind=InboundKind.BINARY_AUDIO,
            event_type="audio.frame",
            session_id=sid,
            detail=f"audio frames received: {self._binary_frames}",
        )
# -- text event routing --------------------------------------------------
    async def _handle_text(self, payload: str) -> InboundResult:
        if self._session_id is None:
            return InboundResult(
                kind=InboundKind.VALIDATION_ERROR,
                detail="no active session",
            )

        try:
            data: Any = json.loads(payload)
            if not isinstance(data, dict):
                raise ValueError("event must be a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            return await self._reject(f"unparseable event: {exc}")

        validation = validate_event(data, sender=EventOrigin.CLIENT)
        if not validation.is_valid or validation.envelope is None:
            issues = validation.issues
            detail = "; ".join(
                f"{i.field}: {i.message}" for i in issues
            )
            return await self._reject(detail, payload=issues)

        envelope = validation.envelope

        # Enforce per-connection monotonic sequence ordering (§8.3).
        if envelope.seq <= self._last_in_seq:
            return await self._reject(
                f"non-monotonic seq {envelope.seq} after {self._last_in_seq}"
            )
        self._last_in_seq = envelope.seq

        if envelope.session_id != self._session_id:
            return await self._reject(
                f"session_id {envelope.session_id!r} does not match connection "
                f"{self._session_id!r}"
            )

        if envelope.type == "audio.eos":
            return await self._handle_audio_eos(envelope.seq)

        if envelope.type == "request.canceled":
            await self._cancel_active_request()
            return InboundResult(
                kind=InboundKind.ACCEPTED,
                event_type="request.canceled",
                session_id=self._session_id,
                detail="request canceled by client",
                seq=envelope.seq,
            )

        return InboundResult(
            kind=InboundKind.ACCEPTED,
            event_type=envelope.type,
            session_id=self._session_id,
            detail=f"accepted {envelope.type}",
            seq=envelope.seq,
        )

    async def _handle_audio_eos(self, seq: int) -> InboundResult:
        sid = self._session_id
        if self._asr_stream is None or self._request_id is None:
            # audio.eos with no active utterance is a benign no-op.
            return InboundResult(
                kind=InboundKind.ACCEPTED,
                event_type="audio.eos",
                session_id=sid,
                detail="audio.eos ignored (no active request)",
                seq=seq,
            )
        try:
            await self._asr_stream.finish()
        except ASRProviderError as exc:
            await self._fail_request(exc)
            return InboundResult(
                kind=InboundKind.REQUEST_ERROR,
                session_id=sid,
                detail=str(exc),
                seq=seq,
            )
        req = self._requests.get(self._request_id)
        assert sid is not None
        req_id = req.request_id
        if self._final_transcript:
            if not await self._run_llm_stage(self._final_transcript):
                return InboundResult(
                    kind=InboundKind.REQUEST_ERROR,
                    session_id=sid,
                    detail="LLM generation failed",
                    seq=seq,
                )
        # M5: finalize the request and emit the correlated latency metrics.
        self._terminal = True
        self._latency.completed_ts = self._clock()
        logger.info(
            "request_completed session_id=%s request_id=%s metrics=%s",
            sid, req_id, self._latency.describe(),
        )
        req.complete()
        await self._send_text(
            self._emit(
                sid, "metrics", self._latency.payload(), request_id=req_id
            )
        )
        await self._send_text(
            self._emit(sid, "request.completed", request_id=req_id)
        )
        await self._reset_request()
        return InboundResult(
            kind=InboundKind.REQUEST_FINISHED,
            event_type="audio.eos",
            session_id=sid,
            detail="request completed",
            seq=seq,
        )

    # -- LLM stage (M3) ------------------------------------------------------
    async def _run_llm_stage(self, text: str) -> bool:
        """Generate a response for ``text``; return False on provider error.

        Tokens stream out as ``llm.token`` events; failures emit ``llm.error``
        followed by a terminal ``request.error`` (kind ``llm``).
        """
        sid = self._session_id
        req_id = self._request_id
        if sid is None or req_id is None:
            return False
        self._latency.llm_start_ts = self._clock()
        logger.info(
            "llm_start session_id=%s request_id=%s", sid, req_id,
        )
        try:
            self._llm_stream = await self._llm.open_stream(
                text, on_token=self._on_llm_token
            )
            await _with_timeout(
                self._llm_stream.wait(),
                timeout=self._timeouts.llm_wait_seconds,
                kind="llm",
                operation="wait",
            )
        except _TimeoutError as exc:
            await self._fail_timeout(exc)
            return False
        except LLMProviderError as exc:
            self._terminal = True
            await self._send_text(
                self._emit(
                    sid,
                    "llm.error",
                    {"message": self._sanitize_error_message(str(exc))},
                    request_id=req_id,
                )
            )
            request = self._requests.get(req_id)
            request.fail()
            logger.info(
                "request_failed session_id=%s request_id=%s kind=llm metrics=%s",
                sid, req_id, self._latency.describe(),
            )
            await self._send_text(
                self._emit(
                    sid,
                    "request.error",
                    {"kind": "llm", "message": self._sanitize_error_message(str(exc))},
                    request_id=req_id,
                )
            )
            await self._reset_request()
            return False
        # If the active request changed while the LLM was generating
        # (e.g. the client disconnected and the request was reset/cancelled),
        # do not proceed to TTS / completion for the stale request.
        if self._request_id != req_id:
            return False
        # M4: flush any trailing (unterminated) segment, then finish TTS and
        # wait for all audio before completing the request.
        await self._tts_send_segment(self._segmenter.flush())
        if not self._tts_failed and self._tts_stream is not None:
            try:
                await _with_timeout(
                    self._tts_stream.finish(),
                    timeout=self._timeouts.tts_finish_seconds,
                    kind="tts",
                    operation="finish",
                )
                await _with_timeout(
                    self._tts_stream.wait(),
                    timeout=self._timeouts.tts_wait_seconds,
                    kind="tts",
                    operation="wait",
                )
            except _TimeoutError as exc:
                await self._fail_timeout(exc)
                return False
            except TTSProviderError as exc:
                await self._fail_tts(exc)
                return False
            else:
                # Guard: the request may have been cancelled or reached a
                # terminal state while TTS was finishing/waiting. Do not emit
                # tts.done after cancellation (M7 §8).
                if self._terminal or self._request_id != req_id:
                    return False
                await self._send_text(
                    self._emit(
                        sid, "tts.done", request_id=self._request_id
                    )
                )
        if self._tts_failed:
            await self._reset_request()
            return False
        # Guard: a cancellation or terminal failure may have been processed
        # while TTS was running. Ensure no late metrics/completion is emitted
        # after the request has already terminated (M7 §8).
        if self._terminal or self._request_id != req_id:
            return False
        return True

    # -- TTS stage (M4) ------------------------------------------------------
    async def _tts_send_segment(self, segment: str) -> None:
        """Stream one text segment to TTS; open the stream lazily."""
        if self._tts_failed or not segment:
            return
        sid = self._session_id
        req_id = self._request_id
        if sid is None or req_id is None:
            return
        try:
            if self._tts_stream is None:
                self._tts_stream = await self._tts.open_stream(
                    on_audio=self._on_tts_audio
                )
                if self._latency.tts_start_ts is None:
                    self._latency.tts_start_ts = self._clock()
                    logger.info(
                        "tts_start session_id=%s request_id=%s",
                        sid, req_id,
                    )
            await _with_timeout(
                self._tts_stream.send_text(segment),
                timeout=self._timeouts.tts_send_seconds,
                kind="tts",
                operation="send",
            )
        except TTSProviderError as exc:
            await self._fail_tts(exc)
        except _TimeoutError as exc:
            await self._fail_timeout(exc)

    async def _on_tts_audio(self, chunk: bytes) -> None:
        """Deliver one synthesized audio chunk to the client (binary frame)."""
        if self._terminal or self._request_id is None:
            return
        if self._latency.tts_first_audio_ts is None:
            self._latency.tts_first_audio_ts = self._clock()
            logger.info(
                "tts_first_audio session_id=%s request_id=%s tts_ttfb_ms=%.1f",
                self._session_id,
                self._request_id,
                self._latency.elapsed_ms(
                    self._latency.tts_start_ts, self._latency.tts_first_audio_ts
                )
                or 0.0,
            )
        if self._send_binary is None:
            return
        await self._send_binary(chunk)

    async def _fail_tts(self, exc: TTSProviderError) -> None:
        """Mark TTS failed and terminate the request with a tts error."""
        self._tts_failed = True
        self._terminal = True
        sid = self._session_id
        req_id = self._request_id
        if sid is None or req_id is None:
            return
        request = self._requests.get(req_id)
        request.fail()
        logger.info(
            "request_failed session_id=%s request_id=%s kind=tts metrics=%s",
            self._session_id, req_id, self._latency.describe(),
        )
        await self._send_text(
            self._emit(
                sid,
                "request.error",
                {"kind": "tts", "message": self._sanitize_error_message(str(exc))},
                request_id=req_id,
            )
        )

    async def _fail_timeout(self, exc: _TimeoutError) -> None:
        """Terminate the request when a provider operation exceeds its budget."""
        self._terminal = True
        sid = self._session_id
        req_id = self._request_id
        if sid is None or req_id is None:
            return
        request = self._requests.get(req_id)
        request.fail()
        logger.warning(
            "request_failed session_id=%s request_id=%s kind=timeout "
            "operation=%s limit_s=%.1f",
            sid, req_id, exc.operation, exc.limit,
        )
        await self._send_text(
            self._emit(
                sid,
                "request.error",
                {
                    "kind": "timeout",
                    "operation": exc.operation,
                    "message": self._sanitize_error_message(str(exc)),
                },
                request_id=req_id,
            )
        )
        await self._reset_request()

    @staticmethod
    def _sanitize_error_message(message: str) -> str:
        """Remove any configured credential values from an error message.

        Prevents an adapter that accidentally embeds an API key from leaking
        it into the user-facing ``request.error`` payload (M7 §6).
        """
        sanitized = message
        for name in (
            "DEEPGRAM_API_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY",
            "ELEVENLABS_VOICE_ID",
        ):
            value = os.environ.get(name, "").strip()
            if value:
                sanitized = sanitized.replace(value, "<redacted>")
        return sanitized

    async def _on_llm_token(self, token: str) -> None:
        if self._request_id is None or self._session_id is None:
            return
        if self._terminal or self._tts_failed:
            # The request already reached a terminal state (or TTS failed);
            # keep the event stream clean instead of streaming more tokens.
            return
        if self._latency.llm_first_token_ts is None:
            self._latency.llm_first_token_ts = self._clock()
            logger.info(
                "llm_first_token session_id=%s request_id=%s llm_ttft_ms=%.1f",
                self._session_id,
                self._request_id,
                self._latency.elapsed_ms(
                    self._latency.llm_start_ts, self._latency.llm_first_token_ts
                )
                or 0.0,
            )
        await self._send_text(
            self._emit(
                self._session_id,
                "llm.token",
                {"text": token},
                request_id=self._request_id,
            )
        )
        # M4: forward completed sentence segments to TTS while the LLM is
        # still generating (streaming pipeline, not batch-at-end).
        for segment in self._segmenter.feed(token):
            await self._tts_send_segment(segment)
# -- ASR request lifecycle -----------------------------------------------
    async def _ensure_request(self) -> bool:
        """Open an ASR stream for a new request; return False on provider error."""
        if self._request_id is not None:
            return True
        assert self._session_id is not None
        request = self._requests.create(self._session_id)
        request.run()
        req_id = request.request_id
        self._request_id = req_id
        self._final_transcript = None
        self._segmenter.flush()
        self._tts_failed = False
        self._latency.reset(clock=self._clock(), request_id=req_id)
        logger.info(
            "request_started session_id=%s request_id=%s",
            self._session_id, req_id,
        )
        await self._send_text(
            self._emit(self._session_id, "request.started", request_id=req_id)
        )
        self._asr_last_seq = -1

        try:
            self._asr_stream = await self._asr.open_stream(
                on_result=self._on_asr_result
            )
        except ASRProviderError as exc:
            request.fail()
            await self._send_text(
                self._emit(
                    self._session_id,
                    "request.error",
                    request_id=req_id,
                    payload={
                        "kind": "asr",
                        "message": self._sanitize_error_message(str(exc)),
                    },
                )
            )
            self._request_id = None
            self._asr_stream = None
            return False
        return True

    async def _on_asr_result(self, result: ASRResult) -> None:
        # Enforce ordered delivery of transcription results (§8.3).
        if self._request_id is None:
            return
        if result.sequence <= self._asr_last_seq:
            return
        self._asr_last_seq = result.sequence
        if self._latency.asr_first_ts is None:
            self._latency.asr_first_ts = self._clock()
            logger.info(
                "asr_first_transcript session_id=%s request_id=%s "
                "asr_first_transcript_ms=%.1f",
                self._session_id,
                self._request_id,
                self._latency.elapsed_ms(
                    self._latency.started_ts, self._latency.asr_first_ts
                )
                or 0.0,
            )
        event_type = (
            "transcript.final"
            if result.kind is TranscriptKind.FINAL
            else "transcript.partial"
        )
        if result.kind is TranscriptKind.FINAL:
            # Deepgram may emit an empty FINAL message while flushing/closing
            # the stream. Do not let that erase a valid transcript received earlier.
            self._latency.asr_final_ts = self._clock()

            if result.transcript:
                self._final_transcript = result.transcript

            logger.info(
                "asr_final_transcript session_id=%s request_id=%s "
                "asr_final_transcript_ms=%.1f",
                self._session_id,
                self._request_id,
                self._latency.elapsed_ms(
                    self._latency.started_ts, self._latency.asr_final_ts
                )
                or 0.0,
            )
        sid = self._session_id
        if sid is None:
            return
        await self._send_text(
            self._emit(
                sid,
                event_type,
                request_id=self._request_id,
                payload={"text": result.transcript, "sequence": result.sequence},
            )
        )

    async def _fail_request(self, exc: ASRProviderError) -> None:
        """Terminate the request on an ASR failure."""
        self._terminal = True
        req_id = self._request_id
        if self._session_id is None or req_id is None:
            return
        request = self._requests.get(req_id)
        request.fail()
        logger.info(
            "request_failed session_id=%s request_id=%s kind=asr metrics=%s",
            self._session_id, req_id, self._latency.describe(),
        )
        await self._send_text(
            self._emit(
                self._session_id,
                "request.error",
                request_id=req_id,
                payload={
                    "kind": "asr",
                    "message": self._sanitize_error_message(str(exc)),
                },
            )
        )
        await self._reset_request()

    async def _cancel_active_request(self) -> None:
        if self._request_id is None or self._session_id is None:
            return
        if self._asr_stream is not None:
            await self._asr_stream.cancel()
        if self._llm_stream is not None:
            await self._llm_stream.cancel()
        if self._tts_stream is not None:
            await self._tts_stream.cancel()
        request = self._requests.get(self._request_id)
        request.cancel()
        self._terminal = True
        logger.info(
            "request_canceled session_id=%s request_id=%s metrics=%s",
            self._session_id, request.request_id, self._latency.describe(),
        )
        await self._send_text(
            self._emit(
                self._session_id,
                "request.canceled",
                request_id=request.request_id,
            )
        )
        await self._reset_request()

    async def _reset_request(self) -> None:
        if self._asr_stream is not None:
            await self._asr_stream.close()
        if self._llm_stream is not None:
            await self._llm_stream.close()
        if self._tts_stream is not None:
            await self._tts_stream.close()
        self._asr_stream = None
        self._request_id = None
        self._asr_last_seq = -1
        self._final_transcript = None
        self._llm_stream = None
        self._latency.reset(clock=self._clock(), request_id=None)
        self._segmenter.flush()
        self._tts_stream = None
        self._tts_failed = False
        self._terminal = False

    async def _reject(
        self,
        detail: str,
        payload: list[Any] | None = None,
        result_kind: InboundKind = InboundKind.VALIDATION_ERROR,
    ) -> InboundResult:
        if payload is None:
            error_payload: dict[str, Any] = {"kind": "validation", "message": detail}
        else:
            error_payload = validation_error_payload(payload)

        sid = self._session_id
        if sid is not None:
            await self._send_text(self._emit(sid, "error", error_payload))

        return InboundResult(
            kind=result_kind,
            session_id=sid,
            detail=detail,
        )

    def _emit(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        request_id: str | None = None,
    ) -> str:
        self._out_seq += 1
        envelope = EventEnvelope(
            type=event_type,
            version=EVENT_VERSION,
            session_id=session_id,
            request_id=request_id,
            seq=self._out_seq,
            timestamp=self._clock() * 1000.0,
            payload=payload or {},
        )
        return json.dumps(envelope.to_wire())

    # -- read-only accessors -------------------------------------------------
    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def request_id(self) -> str | None:
        return self._request_id

    @property
    def binary_frames(self) -> int:
        return self._binary_frames
