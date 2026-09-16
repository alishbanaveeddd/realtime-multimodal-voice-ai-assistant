"""M9 conversation-state tests for the gateway (Parts 3-5).

Deterministic offline tests using the fake ASR/LLM/TTS providers: verifies
the minimal "Do you need more information?" follow-up state machine without
any network or provider.
"""

from __future__ import annotations

import json

import pytest

from assistant.asr.fake import FakeASRProvider, FakeASRScript
from assistant.core.request import RequestManager
from assistant.core.session import SessionManager
from assistant.llm.fake import FakeLLMProvider, FakeLLMScript
from assistant.transport.gateway import SessionConnection
from assistant.tts.fake import FakeTTSProvider

_SILENCE = b"\x00\x00" * 160  # one valid 10 ms PCM16 frame
_ANSWER = (
    "The capital of Pakistan is Islamabad. It is the country's political "
    "and administrative center. Do you need more information?"
)
_MORE = (
    "Islamabad was built in the 1960s near the Margalla Hills. "
    "Do you need more information?"
)


class FakeSender:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def send_text(self, payload: str) -> None:
        self.texts.append(payload)


async def _connect(
    asr: FakeASRProvider, llm: FakeLLMProvider
) -> tuple[SessionConnection, FakeSender, str]:
    sender = FakeSender()
    conn = SessionConnection(
        SessionManager(), RequestManager(), sender.send_text,
        asr, llm, FakeTTSProvider(), clock=lambda: 1000.0,
    )
    sid = await conn.on_connect()
    return conn, sender, sid


async def _utterance(
    conn: SessionConnection, sid: str, seq: int, transcript: str
) -> None:
    """Run one mic turn: audio frame + audio.eos with a scripted final."""
    conn._asr._script = FakeASRScript(final=transcript)  # noqa: SLF001
    result = await conn.on_message(_SILENCE)
    assert result.kind is not None
    await conn.on_message(json.dumps({
        "type": "audio.eos", "version": "1.0", "session_id": sid,
        "request_id": None, "seq": seq, "timestamp": 0.0, "payload": {},
    }))


@pytest.mark.asyncio
async def test_first_question_reaches_llm_unchanged() -> None:
    llm = FakeLLMProvider(FakeLLMScript(tokens=(_ANSWER,)))
    asr = FakeASRProvider()
    conn, _sender, sid = await _connect(asr, llm)
    await _utterance(conn, sid, 1, "What is the capital of Pakistan?")
    assert llm.streams[0].text == "What is the capital of Pakistan?"
    assert conn._awaiting_followup is True  # answer offered more information
    assert conn._prev_question == "What is the capital of Pakistan?"


@pytest.mark.asyncio
async def test_yes_followup_uses_previous_question_and_answer() -> None:
    llm = FakeLLMProvider(FakeLLMScript(tokens=(_ANSWER,)))
    asr = FakeASRProvider()
    conn, _sender, sid = await _connect(asr, llm)
    await _utterance(conn, sid, 1, "What is the capital of Pakistan?")
    llm._script = FakeLLMScript(tokens=(_MORE,))  # noqa: SLF001
    await _utterance(conn, sid, 2, "Yes.")
    follow_prompt = llm.streams[1].text
    assert "What is the capital of Pakistan?" in follow_prompt
    assert _ANSWER in follow_prompt
    assert "yes" in follow_prompt
    # The original question remains the conversation topic.
    assert conn._prev_question == "What is the capital of Pakistan?"


@pytest.mark.asyncio
async def test_negative_followup_skips_llm_and_acknowledges() -> None:
    llm = FakeLLMProvider(FakeLLMScript(tokens=(_ANSWER,)))
    asr = FakeASRProvider()
    conn, sender, sid = await _connect(asr, llm)
    await _utterance(conn, sid, 1, "What is the capital of Pakistan?")
    streams_before = len(llm.streams)
    await _utterance(conn, sid, 2, "No thanks.")
    assert len(llm.streams) == streams_before  # no new generation
    token_texts = [
        json.loads(t)["payload"]["text"]
        for t in sender.texts
        if json.loads(t)["type"] == "llm.token"
    ]
    assert "Okay." in token_texts
    assert conn._awaiting_followup is False
    types = [json.loads(t)["type"] for t in sender.texts]
    assert types[-1] == "request.completed"


@pytest.mark.asyncio
async def test_yes_without_pending_offer_is_a_normal_utterance() -> None:
    llm = FakeLLMProvider(FakeLLMScript(tokens=("Sure, tell me more.",)))
    asr = FakeASRProvider()
    conn, _sender, sid = await _connect(asr, llm)
    await _utterance(conn, sid, 1, "Yes.")
    # No pending follow-up offer: "Yes." is passed through as a normal prompt.
    assert llm.streams[0].text == "Yes."
    assert conn._awaiting_followup is False


@pytest.mark.asyncio
async def test_followup_chain_can_continue() -> None:
    llm = FakeLLMProvider(FakeLLMScript(tokens=(_ANSWER,)))
    asr = FakeASRProvider()
    conn, _sender, sid = await _connect(asr, llm)
    await _utterance(conn, sid, 1, "What is the capital of Pakistan?")
    llm._script = FakeLLMScript(tokens=(_MORE,))  # noqa: SLF001
    await _utterance(conn, sid, 2, "Yeah.")
    await _utterance(conn, sid, 3, "yes")
    third_prompt = llm.streams[2].text
    assert "What is the capital of Pakistan?" in third_prompt
    assert _MORE in third_prompt  # context advanced to the latest answer
