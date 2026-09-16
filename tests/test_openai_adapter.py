"""Offline tests for the OpenAI streaming adapter (deterministic, no network).

The OpenAI API is never contacted: ``httpx.MockTransport`` serves canned SSE
responses. No API key is required; the key never appears in error messages.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from assistant.llm.interface import (
    LLMConfigurationError,
    LLMStreamError,
)
from assistant.llm.openai_adapter import (
    DEFAULT_MODEL,
    GROQ_BASE_URL,
    OpenAILLMProvider,
)


class Collector:
    def __init__(self) -> None:
        self.tokens: list[str] = []

    async def __call__(self, token: str) -> None:
        self.tokens.append(token)


def _sse(*deltas: str) -> bytes:
    lines = [
        f"data: {json.dumps({'choices': [{'delta': {'content': d}}]})}\n\n"
        for d in deltas
    ]
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def _transport(
    handler: Any, captured: list[httpx.Request]
) -> httpx.MockTransport:
    def wrapped(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    return httpx.MockTransport(wrapped)


def _provider(transport: httpx.MockTransport) -> OpenAILLMProvider:
    return OpenAILLMProvider(
        api_key="test-key",
        http_client=httpx.AsyncClient(transport=transport),
    )


def test_blank_api_key_raises_configuration_error() -> None:
    for key in ("", "   "):
        with pytest.raises(LLMConfigurationError):
            OpenAILLMProvider(api_key=key)


def test_from_env_missing_key_raises_configuration_error(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(LLMConfigurationError):
        OpenAILLMProvider.from_env()


def test_from_env_defaults_to_groq_base_url(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.delenv("GROQ_BASE_URL", raising=False)

    provider = OpenAILLMProvider.from_env()

    assert provider._base_url == GROQ_BASE_URL


def test_from_env_base_url_override(monkeypatch: Any) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

    provider = OpenAILLMProvider.from_env()

    assert provider._base_url == "https://api.groq.com/openai/v1"


async def test_stream_delivers_tokens_in_order_and_posts_contract() -> None:
    captured: list[httpx.Request] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse("Hel", "lo"),
            headers={"content-type": "text/event-stream"},
        )

    provider = _provider(_transport(handler, captured))
    collector = Collector()
    stream = await provider.open_stream("say hi", on_token=collector)
    await stream.wait()
    await stream.close()

    assert collector.tokens == ["Hel", "lo"]
    request = captured[0]
    assert request.url == f"{GROQ_BASE_URL}/chat/completions"
    assert request.headers["Authorization"] == "Bearer test-key"
    body = json.loads(request.content)
    assert body["stream"] is True
    assert body["model"] == DEFAULT_MODEL
    assert body["messages"] == [{"role": "user", "content": "say hi"}]


async def test_done_and_empty_deltas_are_ignored() -> None:
    captured: list[httpx.Request] = []
    payload = (
        b'data: {"choices": [{"delta": {"content": ""}}]}\n\n'
        b'data: {"choices": [{"delta": {}}]}\n\n'
        b'data: {"choices": []}\n\n'
        b"not-sse-line\n\n"
        b'data: {"choices": [{"delta": {"content": "ok"}}]}\n\n'
        b"data: [DONE]\n\n"
        b'data: {"choices": [{"delta": {"content": "never"}}]}\n\n'
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    provider = _provider(_transport(handler, captured))
    collector = Collector()
    stream = await provider.open_stream("x", on_token=collector)
    await stream.wait()
    await stream.close()

    assert collector.tokens == ["ok"]


async def test_http_error_includes_status_not_api_key() -> None:
    captured: list[httpx.Request] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"error": {"message": "invalid credentials"}}
        )

    provider = _provider(_transport(handler, captured))
    with pytest.raises(LLMStreamError) as excinfo:
        await provider.open_stream("x", on_token=Collector())

    assert "401" in str(excinfo.value)
    assert "test-key" not in str(excinfo.value)


async def test_network_failure_raises_llm_stream_error() -> None:
    captured: list[httpx.Request] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    provider = _provider(_transport(handler, captured))
    with pytest.raises(LLMStreamError, match="connection refused"):
        await provider.open_stream("x", on_token=Collector())


async def test_model_env_override(monkeypatch: Any) -> None:
    captured: list[httpx.Request] = []
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-120b")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse("a"))

    provider = _provider(_transport(handler, captured))
    stream = await provider.open_stream("x", on_token=Collector())
    await stream.wait()
    await stream.close()

    assert json.loads(captured[0].content)["model"] == "openai/gpt-oss-120b"


async def test_cancel_prevents_further_tokens() -> None:
    captured: list[httpx.Request] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse("a", "b", "c"))

    provider = _provider(_transport(handler, captured))
    collector = Collector()
    stream = await provider.open_stream("x", on_token=collector)
    await stream.cancel()
    await stream.wait()
    await stream.close()

    assert collector.tokens == []


async def test_close_is_repeatable_and_releases_client() -> None:
    captured: list[httpx.Request] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse("a"))

    provider = OpenAILLMProvider(
        api_key="k",
        http_client=httpx.AsyncClient(transport=_transport(handler, captured)),
    )
    stream = await provider.open_stream("x", on_token=Collector())
    await stream.wait()
    await stream.close()
    await stream.close()

    assert stream._task is None or stream._task.done()
    assert stream._response is None or stream._response.is_closed
