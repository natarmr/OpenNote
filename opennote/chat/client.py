"""LLM client adapters for the chat layer.

Two adapters cover every provider in the registry:
- ``OpenAICompatClient`` — the OpenAI SDK pointed at any OpenAI-compatible
  base URL (openai, opencode/Zen, cerebras, groq, google).
- ``AnthropicClient`` — the Anthropic SDK for native /v1/messages.

Secrets are never passed in here; ``get_client`` resolves them via the
keychain/env (see ``opennote.auth.keychain.resolve_key``).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from opennote.auth.config import AuthConfig
from opennote.auth.keychain import resolve_key
from opennote.auth.registry import Provider, get_provider
from opennote.auth.models import select_default


class ChatError(RuntimeError):
    """Configuration error raised before any network call."""


def _parse_arguments(raw: str) -> Dict[str, Any]:
    """Parse a tool-call ``arguments`` JSON string, tolerating truncation.

    Some gateways return invalid/truncated JSON; we salvage what we can rather
    than crashing the turn.
    """
    if not raw or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    # Salvage: extract the first quoted "key": "value" pair(s) best-effort.
    try:
        return dict(
            re.findall(r'"(\w+)"\s*:\s*"([^"]*)"', raw, flags=re.DOTALL)
        )
    except Exception:  # pragma: no cover - defensive
        return {}


@dataclass
class ToolCall:
    """A single tool invocation requested by the model."""

    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class ChatResponse:
    """A model turn in a tool-calling conversation."""

    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)


class LLMClient:
    provider_id: str
    model: str

    def complete(
        self,
        system: str,
        messages: List[Dict[str, str]],
        max_tokens: int = 1024,
    ) -> str:
        raise NotImplementedError

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[Dict[str, Dict[str, Any]]] = None,
        system: Optional[str] = None,
        max_tokens: int = 1024,
    ) -> ChatResponse:
        """Send *messages* (neutral format) and return text + requested tool calls.

        Neutral message roles: ``user``, ``assistant``, and ``tool`` (carries
        ``tool_call_id`` + ``content``). ``tools`` maps name -> {description,
        parameters} JSON-schema; converted to each provider's flavor internally.
        """
        raise NotImplementedError


class OpenAICompatClient(LLMClient):
    def __init__(
        self,
        provider: Provider,
        api_key: str,
        model: str,
        base_url: Optional[str] = None,
        _client=None,
    ):
        from openai import OpenAI

        self.provider_id = provider.id
        self.model = model
        self.base_url = base_url or provider.base_url
        self._max_tokens_kwarg = (
            "max_completion_tokens" if provider.id == "openai" else "max_tokens"
        )
        self._client = _client or OpenAI(api_key=api_key, base_url=self.base_url)

    def complete(
        self,
        system: str,
        messages: List[Dict[str, str]],
        max_tokens: int = 1024,
    ) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, *messages],
            **{self._max_tokens_kwarg: max_tokens},
        )
        return response.choices[0].message.content or ""

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[Dict[str, Dict[str, Any]]] = None,
        system: Optional[str] = None,
        max_tokens: int = 1024,
    ) -> ChatResponse:
        sdk_messages: List[Dict[str, Any]] = []
        if system:
            sdk_messages.append({"role": "system", "content": system})
        for msg in messages:
            role = msg.get("role")
            if role == "tool":
                sdk_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.get("tool_call_id", ""),
                        "content": msg.get("content", ""),
                    }
                )
                continue
            if role == "assistant" and msg.get("tool_calls"):
                sdk_messages.append(
                    {
                        "role": "assistant",
                        "content": msg.get("content") or None,
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {
                                    "name": tc["name"],
                                    "arguments": json.dumps(tc.get("arguments", {})),
                                },
                            }
                            for tc in msg["tool_calls"]
                        ],
                    }
                )
                continue
            if role in ("user", "assistant", "system"):
                sdk_messages.append({"role": role, "content": msg.get("content", "")})
                continue
            raise ChatError(f"Unknown message role {role!r} in neutral message.")

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": sdk_messages,
            **{self._max_tokens_kwarg: max_tokens},
        }
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {"name": name, **spec},
                }
                for name, spec in tools.items()
            ]
        response = self._client.chat.completions.create(**kwargs)
        message = response.choices[0].message
        tool_calls: List[ToolCall] = []
        for tc in message.tool_calls or []:
            tool_calls.append(
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=_parse_arguments(tc.function.arguments or ""),
                )
            )
        return ChatResponse(content=message.content or "", tool_calls=tool_calls)


class AnthropicClient(LLMClient):
    def __init__(
        self,
        provider: Provider,
        api_key: str,
        model: str,
        base_url: Optional[str] = None,
        _client=None,
    ):
        from anthropic import Anthropic

        self.provider_id = provider.id
        self.model = model
        self.base_url = base_url or provider.base_url
        self._client = _client or Anthropic(api_key=api_key, base_url=self.base_url)

    def complete(
        self,
        system: str,
        messages: List[Dict[str, str]],
        max_tokens: int = 1024,
    ) -> str:
        filtered = [m for m in messages if m.get("role") in ("user", "assistant")]
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=filtered,
        )
        return "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[Dict[str, Dict[str, Any]]] = None,
        system: Optional[str] = None,
        max_tokens: int = 1024,
    ) -> ChatResponse:
        sdk_messages: List[Dict[str, Any]] = []
        pending_tool_results: List[Dict[str, Any]] = []

        def flush_results() -> None:
            nonlocal pending_tool_results
            if pending_tool_results:
                sdk_messages.append({"role": "user", "content": pending_tool_results})
                pending_tool_results = []

        def append_user(content: Any) -> None:
            """Append a user turn, merging with a trailing user turn if present.

            Anthropic rejects consecutive user messages; this is a defensive
            safety net for sessions persisted before the loop started merging.
            """
            if sdk_messages and sdk_messages[-1]["role"] == "user":
                last_content = sdk_messages[-1]["content"]
                if isinstance(last_content, list):
                    if isinstance(content, str):
                        last_content.append({"type": "text", "text": content})
                    else:
                        last_content.extend(content)
                elif isinstance(content, str):
                    sdk_messages[-1]["content"] = [
                        {"type": "text", "text": last_content},
                        {"type": "text", "text": content},
                    ]
                else:
                    sdk_messages[-1]["content"] = [
                        {"type": "text", "text": last_content},
                        *content,
                    ]
            else:
                sdk_messages.append({"role": "user", "content": content})

        for msg in messages:
            role = msg.get("role")
            if role == "tool":
                pending_tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_call_id", ""),
                        "content": msg.get("content", ""),
                    }
                )
                continue
            flush_results()
            if role == "assistant" and msg.get("tool_calls"):
                blocks: List[Dict[str, Any]] = []
                content = msg.get("content")
                if content:
                    blocks.append({"type": "text", "text": content})
                blocks.extend(
                    {
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": tc.get("arguments", {}),
                    }
                    for tc in msg["tool_calls"]
                )
                sdk_messages.append({"role": "assistant", "content": blocks})
                continue
            if role == "user":
                append_user(msg.get("content", ""))
                continue
            sdk_messages.append({"role": role, "content": msg.get("content", "")})
        flush_results()

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": sdk_messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [
                {
                    "name": name,
                    "description": spec.get("description", ""),
                    "input_schema": spec.get("parameters", {"type": "object", "properties": {}}),
                }
                for name, spec in tools.items()
            ]
        response = self._client.messages.create(**kwargs)
        content_parts: List[str] = []
        tool_calls: List[ToolCall] = []
        for block in response.content:
            btype = getattr(block, "type", "")
            if btype == "text":
                content_parts.append(block.text)
            elif btype == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=dict(block.input or {}),
                    )
                )
        return ChatResponse(content="".join(content_parts), tool_calls=tool_calls)


def default_provider() -> str:
    """First configured provider with a key and a selected model (alphabetical)."""
    config = AuthConfig()
    from opennote.auth.local import get_active as _ga

    local_active = bool(_ga())
    candidates = [
        pid
        for pid in sorted(config.providers())
        if resolve_key(pid) and config.get(pid) and config.get(pid).model
    ]
    if candidates:
        return candidates[0]
    if local_active:
        return "local"
    raise ChatError(
        "No provider is configured with a key and a model. "
        "Run 'opennote auth add <provider>' first."
    )


def get_client(provider_id: str) -> LLMClient:
    """Build the client for ``provider_id`` using keychain/env key + stored model."""
    provider = get_provider(provider_id)
    if provider.flavor == "local":
        # Local GGUF model — no API key required; model path stored in local config.
        from opennote.auth.local import get_active as _get_active

        active = _get_active()
        if not active:
            raise ChatError(
                "No local model configured. "
                "Run ``opennote local add <path-to-gguf>`` to register one, "
                "then ``opennote local use <name>`` to activate it."
            )
        from opennote.chat.local import LocalLlamaClient  # noqa: E501 (cyclic import guard)

        return LocalLlamaClient(
            model_name=active["name"],
            model_path=active["path"],
            n_ctx=active.get("n_ctx", 4096),
            threads=active.get("threads"),
        )
    api_key = resolve_key(provider_id)
    if not api_key:
        raise ChatError(
            f"No API key for {provider.label}. Run 'opennote auth add {provider_id}', "
            f"or set the {provider.env_var} environment variable."
        )
    config = AuthConfig()
    settings = config.get(provider_id)
    model = settings.model if settings else None
    if not model:
        raise ChatError(
            f"No model selected for {provider.label}. Run 'opennote auth models {provider_id}'."
        )
    base_url = settings.base_url_override if settings else None
    if provider.flavor == "anthropic":
        return AnthropicClient(provider, api_key, model, base_url=base_url)
    return OpenAICompatClient(provider, api_key, model, base_url=base_url)