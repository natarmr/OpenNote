"""In-process GGUF inference via ``llama-cpp-python``.

This implements the ``LLMClient`` interface so that a local model can be used
by the agent loop, the TUI, and single‑turn ``ask``.  The design follows the
existing patterns in ``opennote/chat/client.py`` and ``opennote/agents/loop.py``.

Key design choices
-----------------
* **One‑process load** — the ``Llama`` constructor is called once (module‑level
  cache keyed by ``(resolved_path, n_ctx, threads)``).  Subsequent calls reuse
  the same object, avoiding the several‑second cold start on every turn.
* **Small context budget** — local models typically have ``n_ctx`` of 4096 or
  8192.  Before every generation we trim the conversation history so the total
  prompt stays comfortably below the limit (using the existing
  ``trim_messages`` helper from ``opennote/agents/session.py``).
* **JSON tool protocol** — when the model is asked to call tools it replies
  with a JSON object ``{"tool": "<name>", "arguments": {...}}`` embedded in the
  text.  The client parses the first JSON object it finds; if the model cannot
  produce valid JSON the reply is returned as plain text and the agent loop’s
  normal corrective path handles it.
* **No up‑front tool‑schema listing** — the system prompt tells the model the
  *format* of a tool call but does not enumerate available tools; the model
  learns the pattern from the instruction and the loop’s corrective path handles
  invented or missing tools.

.. note::
    ``llama-cpp-python`` must be installed ``pip install -e ".[local]"``
    (prebuilt CPU wheels are available for Python 3.10‑3.12 on Windows; on 3.13
    a newer wheel or a source build may be needed).
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from opennote.transcript import trim_messages
from opennote.chat.client import LLMClient, ChatResponse

# --------------------------------------------------------------------------- #
# JSON‑tool‑call protocol (generic; does not list specific tool names)
# --------------------------------------------------------------------------- #
_TOOL_PROTOCOL = "\nYou have access to tools.  When you need to call one, reply with ONLY a JSON object of the form:  {\"tool\": \"<tool_name>\", \"arguments\": {...}}\nDo NOT include any other text in the same reply.  If you have enough information, answer normally in plain text."

# --------------------------------------------------------------------------- #
# Module‑level Llama cache  (key: (resolved_path, n_ctx, threads) -> Llama)
# --------------------------------------------------------------------------- #
_llama_cache: Dict[Tuple[Path, int, Optional[int]], Any] = {}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _resolve_path(path: str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = Path.cwd() / p
    return p


def _llama_key(path: Path, n_ctx: int, threads: Optional[int]) -> Tuple[Path, int, Optional[int]]:
    return (path.resolve(), n_ctx, threads)


def _llama_load(path: Path, n_ctx: int, threads: Optional[int]) -> Any:
    """Import *llama-cpp-python* and instantiate a ``Llama`` object."""
    try:
        import llama_cpp  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "llama-cpp-python is not installed.  Install with:\n"
            "    pip install -e \".[local]\"\n"
            "If your Python version (3.13+) does not have a prebuilt wheel, "
            "consider using an LM Studio / Ollama server instead."
        ) from exc

    n_gpu_layers = int(os.environ.get("LLAMA_N_GPU_LAYERS", "0"))
    chat_format = os.environ.get("LLAMA_CHAT_FORMAT", "default")

    llm = llama_cpp.Llama(
        model_path=str(path),
        n_ctx=n_ctx,
        n_threads=threads or os.cpu_count() or 1,
        n_gpu_layers=n_gpu_layers,
        chat_format=chat_format,
        verbose=False,
    )
    return llm


# --------------------------------------------------------------------------- #
# LLMClient implementation
# --------------------------------------------------------------------------- #


class LocalLlamaClient(LLMClient):
    """LLM client that runs a GGUF model in‑process via ``llama-cpp-python``."""

    def __init__(
        self,
        model_name: str,
        model_path: str,
        n_ctx: int = 4096,
        threads: Optional[int] = None,
    ) -> None:
        self.provider_id = "local"
        self.model = model_name
        self._path = _resolve_path(model_path)
        self._n_ctx = n_ctx
        self._threads = threads or (os.cpu_count() or 1)
        key = _llama_key(self._path, self._n_ctx, self._threads)
        if key not in _llama_cache:
            _llama_cache[key] = _llama_load(self._path, self._n_ctx, self._threads)
        self._llm = _llama_cache[key]

    # -----------------------------------------------------------------
    # ``LLMClient`` contract
    # -----------------------------------------------------------------
    def complete(
        self,
        system: str,
        messages: List[Dict[str, str]],
        max_tokens: int = 1024,
    ) -> str:
        """Single‑turn completion (used by studio generators etc.)."""
        msgs: List[Dict[str, str]] = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)
        out = self._llm.create_chat_completion(
            messages=msgs, max_tokens=max_tokens, temperature=0.2
        )
        return out["choices"][0]["message"]["content"] or ""

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[Dict[str, Dict[str, Any]]] = None,
        system: Optional[str] = None,
        max_tokens: int = 1024,
    ) -> ChatResponse:
        """Multi‑round turn with optional tool calling.

        * ``messages`` are *neutral* format (roles: ``user``, ``assistant``,
          ``tool`` with ``tool_call_id`` + ``content``).
        * ``tools`` maps ``name`` → ``{description, parameters}`` JSON‑schema.
        * Returns a ``ChatResponse`` whose ``tool_calls`` list may be filled
          if the model emitted a valid JSON tool call.
        """
        # 1. Clip history to fit inside n_ctx (very important for local models)
        budget = self._n_ctx * 3 - 2000
        trimmed = trim_messages(messages, max_chars=budget)

        # 2. Build the chat‑ml prompt for llama-cpp
        sdk_msgs: List[Dict[str, Any]] = []
        if system:
            system = system + _TOOL_PROTOCOL
        sdk_msgs.append({"role": "system", "content": system})

        for msg in trimmed:
            role = msg.get("role")
            if role == "tool":
                content = msg.get("content", "")
                sdk_msgs.append({"role": "assistant", "content": content})
                continue
            if role == "user":
                sdk_msgs.append({"role": "user", "content": msg.get("content", "")})
                continue
            if role == "assistant":
                content = msg.get("content")
                tool_calls = msg.get("tool_calls")
                if tool_calls:
                    for tc in tool_calls:
                        sdk_msgs.append(
                            {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": tc.get("id", uuid.uuid4().hex[:8]),
                                        "name": tc.get("name", ""),
                                        "arguments": json.dumps(tc.get("arguments", {})),
                                    }
                                ],
                            }
                        )
                else:
                    sdk_msgs.append({"role": "assistant", "content": content or ""})
                continue
            sdk_msgs.append({"role": "user", "content": msg.get("content", "")})

        # 3. Call Llama
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": sdk_msgs,
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        try:
            out = self._llm.create_chat_completion(**kwargs)
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"llama-cpp error: {exc}") from exc

        choice = out["choices"][0]["message"]
        raw_text: str = choice.get("content") or ""

        # 4. Parse tool call if present
        tool_calls: List[Any] = []
        if tools:
            json_text = self._first_json(raw_text)
            if json_text is not None:
                try:
                    obj = json.loads(json_text)
                except json.JSONDecodeError:
                    obj = None
                if obj and isinstance(obj, dict) and obj.get("tool") in tools:
                    tool_name = str(obj["tool"])
                    args = obj.get("arguments", {})
                    if not isinstance(args, dict):
                        args = {}
                    from opennote.chat.client import ToolCall

                    tool_calls.append(
                        ToolCall(
                            id=uuid.uuid4().hex[:8],
                            name=tool_name,
                            arguments=args,
                        )
                    )
                    # strip the JSON from the displayed answer
                    remaining = raw_text[: raw_text.index(json_text)] + raw_text[
                        raw_text.index(json_text) + len(json_text)
                    ]
                    raw_text = remaining.rstrip()

        # 5. Return ChatResponse
        content = raw_text if not tool_calls else ""
        return ChatResponse(content=content, tool_calls=tool_calls)

    # -------------------------------------------------------------------------
    # Helper: extract the first outermost JSON object from a string
    # -------------------------------------------------------------------------
    @staticmethod
    def _first_json(text: str) -> Optional[str]:
        """Return the first JSON object found in *text*, or ``None``.

        Looks for ``{...}`` pairs that are not nested inside a string literal.
        This is a best‑effort parser – it does NOT guarantee syntactic validity
        but works well with typical instruct‑model outputs.
        """
        # try fenced code block first
        fenced = re.search(r"```json\s*(\{.*?\})```", text, re.DOTALL)
        if fenced:
            return fenced.group(1)
        # try bare { … } – walk the string looking for a balanced pair
        depth = 0
        start = None
        in_str = False
        esc = False
        for i, ch in enumerate(text):
            if esc:
                esc = False
                continue
            if ch == "\\" and in_str:
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start is not None:
                    return text[start : i + 1]
        return None