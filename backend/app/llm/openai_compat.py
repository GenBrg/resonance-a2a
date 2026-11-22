"""OpenAI-compatible chat client.

Works against any endpoint that speaks ``/v1/chat/completions``: a hosted API, a
self-hosted vLLM / SGLang / Ollama server, or a routing gateway in between. Model
ids are configured per tier so the cheap rounds never touch the expensive one — the
per-attendee cost target is unreachable otherwise.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx

from app.config import settings
from app.core.errors import UpstreamError
from app.llm.base import Completion, Usage
from app.llm.meter import meter

logger = logging.getLogger("resonance.llm")


def price_of(tier: str, tokens_in: int, tokens_out: int) -> float:
    if tier == "large":
        pin, pout = settings.price_large_in, settings.price_large_out
    else:
        pin, pout = settings.price_small_in, settings.price_small_out
    return round(tokens_in / 1_000_000 * pin + tokens_out / 1_000_000 * pout, 8)


def estimate_tokens(text: str) -> int:
    """Cheap estimator used only for accounting when the gateway omits usage."""
    if not text:
        return 0
    cjk = sum(1 for ch in text if ord(ch) > 0x2E80)
    return max(1, int(cjk * 1.1 + (len(text) - cjk) / 3.6))


class OpenAICompatModel:
    name = "openai-compat"
    is_live = True

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        model_small: str | None = None,
        model_large: str | None = None,
        model_judge: str | None = None,
        timeout: float | None = None,
        max_concurrency: int | None = None,
    ):
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.model_small = model_small or settings.llm_model_small
        self.model_large = model_large or settings.llm_model_large
        self.model_judge = model_judge or settings.llm_model_judge or self.model_small
        self.timeout = timeout or settings.llm_timeout_seconds
        self._sem = asyncio.Semaphore(max_concurrency or settings.llm_max_concurrency)
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def model_for(self, tier: str) -> str:
        if tier == "large":
            return self.model_large
        if tier == "judge":
            return self.model_judge
        return self.model_small

    async def complete(
        self,
        *,
        system: str,
        user: str,
        tier: str = "small",
        json_mode: bool = False,
        max_tokens: int = 700,
        user_ref: str = "",
        session_ref: str = "",
    ) -> Completion:
        model = self.model_for(tier)
        payload: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.35 if json_mode else 0.65,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        last_error: Exception | None = None
        async with self._sem:
            for attempt in range(3):
                started = time.perf_counter()
                try:
                    response = await self._http().post("/chat/completions", json=payload)
                    if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                        await asyncio.sleep(0.6 * (2**attempt) + 0.2)
                        continue
                    response.raise_for_status()
                    data = response.json()
                    choice = data["choices"][0]
                    message = choice.get("message") or {}
                    text = (message.get("content") or "").strip()
                    if not text:
                        # Reasoning models can spend the whole budget in the reasoning
                        # channel and leave `content` empty. The structured answer is
                        # usually at the tail of that channel, so read it instead of
                        # declaring the call a failure — otherwise every judge call on
                        # such a gateway silently degrades to the fallback score.
                        reasoning = (message.get("reasoning") or message.get("reasoning_content") or "").strip()
                        text = reasoning[-6000:]
                    if not text and choice.get("finish_reason") == "length" and attempt < 2:
                        payload["max_tokens"] = int(payload["max_tokens"]) * 2
                        await asyncio.sleep(0.2)
                        continue
                    usage_raw = data.get("usage") or {}
                    tin = int(usage_raw.get("prompt_tokens") or estimate_tokens(system + user))
                    tout = int(usage_raw.get("completion_tokens") or estimate_tokens(text))
                    usage = Usage(
                        tokens_in=tin,
                        tokens_out=tout,
                        model=model,
                        tier=tier,
                        usd=price_of(tier, tin, tout),
                    )
                    logger.debug(
                        {
                            "event": "llm_call",
                            "model": model,
                            "tier": tier,
                            "ms": round((time.perf_counter() - started) * 1000),
                            "tokens_in": tin,
                            "tokens_out": tout,
                            "usd": usage.usd,
                            "session": session_ref,
                        }
                    )
                    meter.add(session_ref or user_ref, usage)
                    return Completion(text=text, usage=usage, raw=data)
                except (httpx.HTTPError, KeyError, json.JSONDecodeError) as exc:
                    last_error = exc
                    if attempt < 2:
                        await asyncio.sleep(0.6 * (2**attempt) + 0.2)
                        continue
        raise UpstreamError(f"model call failed: {type(last_error).__name__}: {last_error}")


async def probe_models(base_url: str, api_key: str, timeout: float = 10.0) -> dict:
    """Health probe used by `resonance doctor`."""
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout) as client:
        response = await client.get("/models", headers={"Authorization": f"Bearer {api_key}"})
        response.raise_for_status()
        return response.json()



