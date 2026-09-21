"""Reasoning-model output cleaner (upstream text_utils.clean_thinking_content parity).

Strips <think>...</think> / <thinking>...</thinking> blocks from model output.
Bypasses extraction for content >100KB for performance. Tolerates malformed
output (missing opening tag: strip up to first closing tag).
"""
from __future__ import annotations

import re

_MAX_BYPASS_CHARS = 100_000

_THINK_RE = re.compile(
    r"<(?:think|thinking)\b[^>]*?>.*?</(?:think|thinking)\s*>",
    flags=re.DOTALL | re.IGNORECASE,
)
_CLOSE_RE = re.compile(r"</(?:think|thinking)\s*>", flags=re.IGNORECASE)
_OPEN_RE = re.compile(r"<(?:think|thinking)\b[^>]*?>", flags=re.IGNORECASE)


def clean_thinking_content(text: str) -> str:
    """Remove reasoning blocks; always returns a string."""
    if not text:
        return ""
    if len(text) > _MAX_BYPASS_CHARS:
        return text
    cleaned = _THINK_RE.sub("", text)
    if cleaned != text:
        text = cleaned
    # Malformed: closing tag without a matched opener (e.g. truncated stream).
    if _CLOSE_RE.search(text) and not _OPEN_RE.search(text):
        text = _CLOSE_RE.sub("", text)
    # Stray opener without closer (model left thinking open): drop it and before? No —
    # keep the visible content, just remove the tag itself.
    text = _OPEN_RE.sub("", text)
    text = _CLOSE_RE.sub("", text)
    return text
