"""Model adapters for chat-LLM APIs, using stdlib HTTP and verified HTTPS.

- :class:`OpenAIModel`   -- any OpenAI-compatible Chat Completions endpoint
  (OpenAI, OpenRouter, Together, vLLM, and Gemini via its OpenAI-compatible
  endpoint ``https://generativelanguage.googleapis.com/v1beta/openai``).
  Supports text + image (base64) + audio (``input_audio``).
- :class:`AnthropicModel` -- Anthropic Messages API (text + image; audio rows
  are rejected with :class:`APIModelError` because the API has no audio input).

Use :func:`get_model` with a spec string like ``"ollama:llama3.1:8b"``,
``"openrouter:openai/gpt-4o-mini"``, ``"gemini:gemini-2.5-flash"``,
``"openai:gpt-4o"`` or ``"anthropic:claude-sonnet-4-5"``. Ollama needs no key
(local server); OpenRouter uses ``OPENROUTER_API_KEY``.

Any other provider prefix is treated as a self-hosted OpenAI-compatible server
and needs ``base_url=`` (e.g. a Colab + vLLM tunnel:
``get_model("colab:qwen2.5-7b-instruct", base_url="https://xxx.trycloudflare.com/v1")``).
Such endpoints default the key to ``SYNHALEES_API_KEY`` or a placeholder.

API keys come from environment variables (``OPENAI_API_KEY``,
``ANTHROPIC_API_KEY``) unless passed explicitly.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import time
import urllib.error
from pathlib import Path
from typing import Any

from synhalees.http import open_url


class APIModelError(RuntimeError):
    """Raised when a provider call fails permanently or a modality is unsupported."""


class BaseModel:
    """Minimal chat-model interface used by the runner."""

    name: str = "unknown"
    supports_audio: bool = False
    supports_vision: bool = False

    def complete(
        self,
        prompt: str,
        *,
        image_path: str | None = None,
        audio_path: str | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        raise NotImplementedError


def _b64(path: str) -> tuple[str, str]:
    """Return (base64_data, mime_type) for a media file."""
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return base64.b64encode(Path(path).read_bytes()).decode("ascii"), mime


def _post_json(url: str, headers: dict, payload: dict, *, timeout: int,
               max_retries: int) -> dict:
    """POST JSON with exponential backoff on 429/5xx and network errors."""
    body = json.dumps(payload).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        request = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json", **headers},
        )
        try:
            with open_url(request, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in (408, 409, 429) and not 500 <= exc.code < 600:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                raise APIModelError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
        if attempt < max_retries:
            time.sleep(min(2 ** attempt, 30))
    raise APIModelError(f"request failed after {max_retries + 1} attempts: {last_error}")

class OpenAIModel(BaseModel):
    """Any OpenAI-compatible Chat Completions endpoint."""

    supports_audio = True
    supports_vision = True

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        timeout: int = 120,
        max_retries: int = 3,
    ) -> None:
        self.name = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not self.api_key:
            raise APIModelError(
                "No API key: pass api_key= or set the OPENAI_API_KEY env var."
            )
        self.timeout = timeout
        self.max_retries = max_retries

    def complete(
        self,
        prompt,
        *,
        image_path=None,
        audio_path=None,
        reasoning_effort=None,
    ) -> str:
        message, _usage = self.complete_with_usage(
            prompt,
            image_path=image_path,
            audio_path=audio_path,
            reasoning_effort=reasoning_effort,
        )
        return message

    def complete_with_usage(
        self,
        prompt,
        *,
        image_path=None,
        audio_path=None,
        reasoning_effort: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        content: list[dict] = [{"type": "text", "text": prompt}]
        if image_path:
            data, mime = _b64(image_path)
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{data}"},
            })
        if audio_path:
            data, _ = _b64(audio_path)
            content.append({
                "type": "input_audio",
                "input_audio": {"data": data, "format": "mp3"},
            })
        payload = {
            "model": self.name,
            "messages": [{"role": "user", "content": content}],
        }
        if reasoning_effort and reasoning_effort not in ("none", "off"):
            payload["reasoning_effort"] = reasoning_effort
        result = _post_json(
            f"{self.base_url}/chat/completions",
            {"Authorization": f"Bearer {self.api_key}"},
            payload,
            timeout=self.timeout,
            max_retries=self.max_retries,
        )
        try:
            choice = result["choices"][0]
            message_obj = choice.get("message", {})
            message = message_obj.get("content", "")
            reasoning_content = message_obj.get("reasoning_content") or choice.get("reasoning_content")
        except (KeyError, IndexError, TypeError) as exc:
            raise APIModelError(f"unexpected response shape: {result!r:.300}") from exc
        if isinstance(message, list):  # some providers return content blocks
            message = " ".join(
                part.get("text", "") for part in message if isinstance(part, dict)
            )
        message = (message or "").strip()
        # For thinking models emitting thought tags (e.g. DeepSeek-R1 / QwQ)
        thought_match = re.search(
            r"(?is)<\s*(?:think|thought|reasoning)\s*>(.*?)<\s*/\s*(?:think|thought|reasoning)\s*>",
            message,
        )
        if thought_match and not reasoning_content:
            reasoning_content = thought_match.group(1).strip()
        cleaned = re.sub(
            r"(?is)<\s*(?:think|thought|reasoning)\s*>.*?<\s*/\s*(?:think|thought|reasoning)\s*>",
            "",
            message,
        ).strip()
        cleaned = re.sub(r"(?is)^<\s*(?:think|thought|reasoning)\s*>.*$", "", cleaned).strip()
        cleaned = re.sub(r"(?is)^.*?<\s*/\s*(?:think|thought|reasoning)\s*>", "", cleaned).strip()
        if cleaned:
            message = cleaned
        usage = result.get("usage")
        usage_dict = dict(usage) if isinstance(usage, dict) else {}
        if reasoning_content:
            usage_dict["reasoning_content"] = reasoning_content
        return message, usage_dict


class AnthropicModel(BaseModel):
    """Anthropic Messages API (text + vision; no audio input support)."""

    supports_audio = False
    supports_vision = True

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str = "https://api.anthropic.com",
        timeout: int = 120,
        max_retries: int = 3,
    ) -> None:
        self.name = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise APIModelError(
                "No API key: pass api_key= or set the ANTHROPIC_API_KEY env var."
            )
        self.timeout = timeout
        self.max_retries = max_retries

    def complete(
        self,
        prompt,
        *,
        image_path=None,
        audio_path=None,
        reasoning_effort=None,
    ) -> str:
        if audio_path:
            raise APIModelError(
                "Anthropic Messages API has no audio input; run audio rows "
                "with an audio-capable provider (e.g. openai)."
            )
        content: list[dict] = []
        if image_path:
            data, mime = _b64(image_path)
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": data},
            })
        content.append({"type": "text", "text": prompt})
        payload: dict[str, Any] = {
            "model": self.name,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": content}],
        }
        if reasoning_effort and str(reasoning_effort).lower() not in ("none", "off", "0"):
            budget_map = {"low": 1024, "medium": 2048, "high": 4096}
            budget = budget_map.get(str(reasoning_effort).lower(), 2048)
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
            payload["max_tokens"] = budget + 2048
        result = _post_json(
            f"{self.base_url}/v1/messages",
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            payload,
            timeout=self.timeout,
            max_retries=self.max_retries,
        )
        try:
            blocks = result["content"]
            text_blocks = [
                b.get("text", "") for b in blocks
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            if not text_blocks:
                text_blocks = [
                    b.get("text", "") for b in blocks
                    if isinstance(b, dict) and "text" in b
                ]
            return " ".join(text_blocks).strip()
        except (KeyError, TypeError) as exc:
            raise APIModelError(f"unexpected response shape: {result!r:.300}") from exc


class GeminiModel(BaseModel):
    """Google Gemini native generateContent API (text + vision + audio).

    API key from ``GEMINI_API_KEY`` (or ``GOOGLE_API_KEY``); free keys at
    https://aistudio.google.com/apikey
    """

    supports_audio = True
    supports_vision = True

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str = "https://generativelanguage.googleapis.com",
        timeout: int = 120,
        max_retries: int = 3,
    ) -> None:
        self.name = model
        self.base_url = base_url.rstrip("/")
        self.api_key = (
            api_key
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or ""
        )
        if not self.api_key:
            raise APIModelError(
                "No API key: pass api_key= or set GEMINI_API_KEY "
                "(free key: https://aistudio.google.com/apikey)."
            )
        self.timeout = timeout
        self.max_retries = max_retries

    def complete(
        self,
        prompt,
        *,
        image_path=None,
        audio_path=None,
        reasoning_effort=None,
    ) -> str:
        parts: list[dict] = [{"text": prompt}]
        if image_path:
            data, mime = _b64(image_path)
            parts.append({"inline_data": {"mime_type": mime, "data": data}})
        if audio_path:
            data, mime = _b64(audio_path)
            if audio_path.lower().endswith(".mp3"):
                mime = "audio/mp3"
            parts.append({"inline_data": {"mime_type": mime, "data": data}})
        payload: dict[str, Any] = {"contents": [{"role": "user", "parts": parts}]}
        gen_config: dict[str, Any] = {}
        effort = str(reasoning_effort).lower() if reasoning_effort else ""
        if effort in ("none", "off", "0"):
            gen_config["thinkingConfig"] = {"thinkingBudget": 0}
        elif effort == "low":
            gen_config["thinkingConfig"] = {"thinkingBudget": 1024}
        elif effort == "medium":
            gen_config["thinkingConfig"] = {"thinkingBudget": 2048}
        elif effort == "high":
            gen_config["thinkingConfig"] = {"thinkingBudget": 8192}
        elif "thinking" in self.name.lower():
            gen_config["thinkingConfig"] = {"thinkingBudget": -1}
        if gen_config:
            payload["generationConfig"] = gen_config
        url = (
            f"{self.base_url}/v1beta/models/{self.name}:generateContent"
            f"?key={self.api_key}"
        )
        result = _post_json(
            url, {}, payload, timeout=self.timeout, max_retries=self.max_retries
        )
        try:
            out_parts = result["candidates"][0]["content"]["parts"]
            # Exclude thought parts from the scoreable text answer
            text_parts = [
                p.get("text", "") for p in out_parts
                if not p.get("thought", False)
            ]
            if not text_parts:
                text_parts = [p.get("text", "") for p in out_parts]
            return "".join(text_parts).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise APIModelError(f"unexpected response shape: {result!r:.300}") from exc

def _ollama(model, **kw):
    """Ollama local server -- OpenAI-compatible, no real API key needed."""
    return OpenAIModel(
        model,
        base_url=kw.pop("base_url", "http://localhost:11434/v1"),
        api_key=kw.pop("api_key", None) or "ollama",
        **kw,
    )


_PROVIDERS = {
    "openai": OpenAIModel,
    "openrouter": lambda model, **kw: OpenAIModel(
        model, base_url="https://openrouter.ai/api/v1",
        api_key=kw.pop("api_key", None) or os.environ.get("OPENROUTER_API_KEY"),
        **kw,
    ),
    "gemini": GeminiModel,
    "ollama": _ollama,
    "anthropic": AnthropicModel,
}


def get_model(spec: str, **kwargs) -> BaseModel:
    """Build a model adapter from ``"provider:model-name"`` (or bare name).

    Bare names default to the OpenAI-compatible endpoint. ``provider`` may be
    ``openai``, ``openrouter`` or ``anthropic``; any other value is treated as
    a custom OpenAI-compatible provider and requires ``base_url=``.
    """
    if ":" in spec:
        provider, _, model = spec.partition(":")
    else:
        provider, model = "openai", spec
    factory = _PROVIDERS.get(provider)
    if factory is None:
        base_url = kwargs.pop("base_url", None)
        if not base_url:
            raise APIModelError(
                f"Unknown provider {provider!r}; pass base_url= for a custom "
                "OpenAI-compatible endpoint."
            )
        # Self-hosted endpoints (Colab + vLLM, llama.cpp, LM Studio) usually
        # ignore the bearer token, but the HTTP client still needs one.
        api_key = (
            kwargs.pop("api_key", None)
            or os.environ.get("SYNHALEES_API_KEY")
            or "local"
        )
        return OpenAIModel(model, base_url=base_url, api_key=api_key, **kwargs)
    return factory(model, **kwargs)


__all__ = [
    "APIModelError",
    "AnthropicModel",
    "GeminiModel",
    "BaseModel",
    "OpenAIModel",
    "get_model",
]