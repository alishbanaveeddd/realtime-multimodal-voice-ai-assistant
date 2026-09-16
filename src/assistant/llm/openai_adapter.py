"""OpenAI-compatible streaming Chat Completions adapter (approved decision D4).

This is the only module that knows about OpenAI's wire protocol. The transport
/ session layer depends solely on :class:`assistant.llm.interface.LLMProvider`.

Configuration comes from the environment (``GROQ_API_KEY``); credentials are
never hardcoded or logged. Transport: ``httpx`` (already a project dependency)
drives the documented Server-Sent-Events streaming of ``POST /chat/completions``
with ``stream: true`` — no ``openai`` SDK dependency is added. If the key is
missing, constructing :class:`OpenAILLMProvider` raises
:class:`LLMConfigurationError` immediately.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx

from assistant.llm.interface import (
    LLMConfigurationError,
    LLMProvider,
    LLMStream,
    LLMStreamError,
    TokenCallback,
)

#: Groq's OpenAI-compatible Chat Completions base URL.
GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
#: Env var holding the Groq credential (name only, never a value).
GROQ_API_KEY_ENV: str = "GROQ_API_KEY"
#: Optional env var overriding the base URL for compatible endpoints.
GROQ_BASE_URL_ENV: str = "GROQ_BASE_URL"
#: Default chat model; overridable via ``GROQ_MODEL`` or constructor.
DEFAULT_MODEL: str = "openai/gpt-oss-20b"

_SSE_PREFIX: str = "data: "
_SSE_DONE: str = "[DONE]"


class OpenAILLMProvider(LLMProvider):
    """Provider opening OpenAI streaming generation sessions."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = GROQ_BASE_URL,
        model: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise LLMConfigurationError(
                "Groq API key is missing or blank; set GROQ_API_KEY"
            )
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model or os.environ.get("GROQ_MODEL", DEFAULT_MODEL)
        self._http_client = http_client

    @classmethod
    def from_env(cls) -> OpenAILLMProvider:
        """Build a provider from ``GROQ_API_KEY`` (raises if missing).

        An optional ``GROQ_BASE_URL`` env var overrides the default endpoint.
        """
        return cls(
            api_key=os.environ.get(GROQ_API_KEY_ENV, ""),
            base_url=os.environ.get(GROQ_BASE_URL_ENV, GROQ_BASE_URL),
        )

    async def open_stream(
        self, text: str, *, on_token: TokenCallback
    ) -> OpenAILLMStream:
        stream = OpenAILLMStream(
            api_key=self._api_key,
            base_url=self._base_url,
            model=self._model,
            text=text,
            on_token=on_token,
            http_client=self._http_client,
        )
        await stream._start()
        return stream

class OpenAILLMStream(LLMStream):
    """Streaming generation over one OpenAI chat completion response."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        text: str,
        on_token: TokenCallback,
        http_client: httpx.AsyncClient | None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._text = text
        self._on_token = on_token
        self._own_client = http_client is None
        self._client = http_client
        self._response: httpx.Response | None = None
        self._task: asyncio.Task[None] | None = None
        self._error: LLMStreamError | None = None
        self._canceled = False

    async def _start(self) -> None:
        """Open the SSE response and start the background token reader."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0))
        request = self._client.build_request(
            "POST",
            f"{self._base_url}/chat/completions",
            json={
                "model": self._model,
                "stream": True,
                "messages": [{"role": "user", "content": self._text}],
            },
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        status = 0
        try:
            self._response = await self._client.send(request, stream=True)
            status = self._response.status_code
        except httpx.HTTPError as exc:
            raise LLMStreamError(f"Groq request failed: {exc}") from exc
        if status != 200:
            assert self._response is not None
            body = (await self._response.aread()).decode(errors="replace")
            await self._response.aclose()
            self._response = None
            raise LLMStreamError(f"Groq returned HTTP {status}: {body[:500]}")
        self._task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        assert self._response is not None
        try:
            async for line in self._response.aiter_lines():
                if self._canceled:
                    return
                if not line.startswith(_SSE_PREFIX):
                    continue
                data = line[len(_SSE_PREFIX):].strip()
                if data == _SSE_DONE:
                    return
                try:
                    chunk: dict[str, Any] = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    await self._on_token(str(content))
        except httpx.HTTPError as exc:
            self._error = LLMStreamError(f"Groq stream failed: {exc}")
        finally:
            await self._response.aclose()

    async def wait(self) -> None:
        if self._task is not None:
            await self._task
        if self._error is not None:
            raise self._error

    async def cancel(self) -> None:
        self._canceled = True

    async def close(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        if self._response is not None and not self._response.is_closed:
            await self._response.aclose()
        if self._own_client and self._client is not None:
            if not self._client.is_closed:
                await self._client.aclose()

