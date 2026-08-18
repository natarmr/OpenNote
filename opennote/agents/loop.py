"""Multi-round agentic retrieval loop.

The loop repeatedly sends the conversation so far to the LLM together with a
set of available tools. If the model calls a tool, the loop executes it,
appends the result to the conversation, and repeats (up to ``max_rounds``).
When the model finally produces a plain answer (no tool calls) the loop
validates citations, appends the Sources footer, and returns an ``AskResult``.
"""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from opennote.agents.tools import TOOL_SCHEMAS, execute_tool, render_tool_results
from opennote.chat.ask import AskResult
from opennote.chat.citations import used_sources
from opennote.chat.client import ChatError, LLMClient, default_provider, get_client
from opennote.chat.prompt import SYSTEM_TEMPLATE
from opennote.notebooks import Notebook
from opennote.retrieval.retriever import Retriever, SearchResult

logger = logging.getLogger("opennote.agents.loop")

#: Maximum tool-calling rounds before we force "answer with what you have".
MAX_ROUNDS = 5

TOOLS_LIST = ", ".join(TOOL_SCHEMAS)

SYSTEM_TOOLS_HINT = (
    f"\nYou have access to these tools: {TOOLS_LIST}. Call a tool with the correct "
    "arguments when you need source content. NEVER invent a tool that is not listed."
)


def _is_bad_request(exc: Exception) -> bool:
    """True when the provider rejected the *shape* of our request (invented
    tool, malformed args, non-alternating roles) rather than a transient
    network/auth problem."""
    for module_name in ("openai", "anthropic"):
        module = sys.modules.get(module_name)
        if module is not None:
            bad = getattr(module, "BadRequestError", None)
            if bad is not None and isinstance(exc, bad):
                return True
    name = type(exc).__name__
    if name == "BadRequestError" or name.endswith(".BadRequestError"):
        return True
    message = str(exc).lower()
    return any(
        needle in message
        for needle in (
            "tool call validation failed",
            "not in request.tools",
            "invalid_request_error",
            "no tool use",
        )
    )


@dataclass
class AgentResult:
    """Outcome of an agentic turn, including the full neutral message history."""

    result: AskResult
    messages: List[Dict[str, Any]] = field(default_factory=list)
    rounds_used: int = 0


class TurnCancelled(RuntimeError):
    """Raised when a running agent turn is cancelled by the caller.

    The TUI watches this via ``should_cancel``; the chat CLI never passes
    ``should_cancel`` so this never fires there.
    """


def _tool_content(tool_name: str, payload: Any, offset: int = 0) -> str:
    """Serialize a tool's return value for the model's next turn."""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list) and all(isinstance(r, SearchResult) for r in payload):
        return render_tool_results(payload, offset=offset)
    return json.dumps(payload)


def _append_user_message(messages: List[Dict[str, Any]], content: str) -> None:
    """Append a user message, merging with a trailing user turn when present.

    The Anthropic API rejects consecutive user turns; the corrective path in
    the loop can otherwise produce them back-to-back.
    """
    if messages and messages[-1].get("role") == "user":
        messages[-1]["content"] += f"\n\n{content}"
    else:
        messages.append({"role": "user", "content": content})


def agent_turn(
    notebook: Notebook,
    question: str,
    provider_id: Optional[str] = None,
    top_k: int = 5,
    max_rounds: int = MAX_ROUNDS,
    history: Optional[List[Dict[str, Any]]] = None,
    client: Optional[LLMClient] = None,
    retriever: Optional[Retriever] = None,
    max_tokens: int = 1024,
    should_cancel: Optional[Callable[[], bool]] = None,
    on_round: Optional[Callable[[int, int], None]] = None,
) -> AgentResult:
    """Run one user *question* through the multi-round tool loop.

    ``history`` carries prior neutral messages (for resuming a session);
    ``client``/``retriever`` are injectable for tests. ``max_tokens`` bounds
    each model reply (raised by callers that want longer answers).
    ``should_cancel`` is polled before each round; when it returns True the
    turn raises :class:`TurnCancelled`. ``on_round(used, total)`` is invoked
    at the start of each round (progress reporting for the TUI).
    """
    if client is None:
        client = get_client(provider_id or default_provider())
    retriever = retriever or Retriever(notebook, top_k=top_k)

    messages: List[Dict[str, Any]] = list(history or [])
    messages.append({"role": "user", "content": question})

    retrieved: List[SearchResult] = []
    final_answer = ""
    rounds_used = 0

    for _ in range(max_rounds):
        if should_cancel is not None and should_cancel():
            raise TurnCancelled("Agent turn cancelled by caller.")
        rounds_used += 1
        if on_round is not None:
            on_round(rounds_used, max_rounds)
        try:
            response = client.chat(
                messages,
                tools=TOOL_SCHEMAS,
                system=SYSTEM_TEMPLATE + SYSTEM_TOOLS_HINT,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            # Only the provider rejecting the *shape* of our request is worth a
            # corrective round. Network/auth/timeout errors are surfaced so the
            # caller (the chat CLI) can report them cleanly.
            if not _is_bad_request(exc):
                raise
            logger.info("Provider rejected the request (%s); correcting the model.", exc)
            _append_user_message(
                messages,
                f"The previous model response was rejected by the provider ({exc}). "
                f"Retry using ONLY the available tools: {TOOLS_LIST}. Do not invent tools.",
            )
            continue

        if should_cancel is not None and should_cancel():
            raise TurnCancelled("Agent turn cancelled by caller.")

        if not response.tool_calls:
            final_answer = response.content
            break

        # Record the assistant's tool requests, then execute them.
        messages.append(
            {
                "role": "assistant",
                "content": response.content,
                "tool_calls": [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in response.tool_calls
                ],
            }
        )
        for tc in response.tool_calls:
            try:
                payload = execute_tool(tc.name, retriever, tc.arguments)
                if isinstance(payload, list) and all(
                    isinstance(r, SearchResult) for r in payload
                ):
                    # Render *before* extending so this call's indices are
                    # offset past the already-retrieved results — the flat
                    # ``retrieved`` list is what citation validation uses.
                    content = _tool_content(tc.name, payload, offset=len(retrieved))
                    retrieved.extend(payload)
                else:
                    content = _tool_content(tc.name, payload)
            except Exception as exc:  # tool failure must not kill the turn
                content = f"Error calling tool '{tc.name}': {exc}"
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": content}
            )

    if not final_answer:
        final_answer = (
            "I ran out of tool rounds without being able to answer. "
            "Try rephrasing the question."
        )

    answer = final_answer.strip()
    footer, sources_used = used_sources(answer, retrieved)
    if footer:
        answer = f"{answer}\n\n{footer}"

    result = AskResult(
        question=question,
        answer=answer,
        sources=sources_used,
        results=retrieved,
        provider_id=client.provider_id,
        model=client.model,
    )
    messages.append({"role": "assistant", "content": answer})
    return AgentResult(result=result, messages=messages, rounds_used=rounds_used)