"""Automated model selection from a provider's live model list.

Strategy: filter to chat-capable, non-deprecated models, then prefer the
provider's curated preference order; any remaining live models fall back to a
lightweight heuristic ordering (reasoning/high-capacity hints first).
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional

from opennote.auth.registry import Provider

NON_CHAT_KEYWORDS = (
    "embedding",
    "embed",
    "whisper",
    "tts",
    "speech",
    "audio",
    "dall-e",
    "image",
    "moderation",
    "guard",
    "prompt-guard",
    "rerank",
    "realtime",
    "vision",
)

DEPRECATED_KEYWORDS = (
    "deprecated",
    "legacy",
    "old-",
    "eol",
)

PREFERRED_HINTS = (
    "sonnet",
    "flash",
    "mini",
    "latest",
    "fast",
    "instant",
)


def is_chat_model(model_id: str) -> bool:
    lowered = model_id.lower()
    return not any(k in lowered for k in NON_CHAT_KEYWORDS)


def is_deprecated(model_id: str) -> bool:
    lowered = model_id.lower()
    return any(k in lowered for k in DEPRECATED_KEYWORDS)


def usable_models(model_ids: Iterable[str], excluded: Iterable[str] = ()) -> List[str]:
    excluded = set(excluded)
    return [
        m
        for m in model_ids
        if m not in excluded and is_chat_model(m) and not is_deprecated(m)
    ]


def _fallback_score(model_id: str) -> int:
    """Heuristic ordering for models not in the curated preference list."""
    lowered = model_id.lower()
    score = 0
    if re.search(r"\b(70b|120b|480b)\b", lowered):
        score += 4
    elif re.search(r"pro|coder|ultra|large", lowered):
        score += 3
    if "mini" in lowered or "flash" in lowered or "instant" in lowered:
        score += 1
    if any(h in lowered for h in PREFERRED_HINTS):
        score += 2
    return score


def rank_models(provider: Provider, model_ids: Iterable[str]) -> List[str]:
    """Return usable models ordered best-first for ``provider``."""
    available = set(model_ids)
    preferred = [m for m in provider.preferred_models if m in available]
    fallback = usable_models(
        (m for m in available if m not in set(preferred)),
        excluded=provider.excluded_models,
    )
    fallback.sort(key=lambda m: (-_fallback_score(m), m))
    return preferred + fallback


def select_default(provider: Provider, model_ids: Iterable[str]) -> Optional[str]:
    ranked = rank_models(provider, model_ids)
    return ranked[0] if ranked else None