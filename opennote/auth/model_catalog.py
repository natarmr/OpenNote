"""Model-list resolution with a last-good live cache.

Built-in providers carry **no** hardcoded model names. The ``preferred_models``
field now exists only so user-defined providers in ``opennote.json`` can supply
optional hints; it is empty for every built-in. The TUI used to show those
hints whenever live validation failed, with no indication the list was a guess
— which is exactly how a user ended up picking a model that doesn't exist.

This module gives one answer to "which models can I actually pick?":

- ``live``      — models just fetched from the provider's ``/models`` endpoint
- ``cache``     — the last successfully fetched live list (kept in-process)
- ``hardcoded`` — provider's preferred list, ONLY as a clearly-labelled last
                  resort when we have never had a live list

Every caller must surface ``source`` in the UI so an offline list is never
mistaken for a live one.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, List, Optional

from opennote.auth.models import rank_models

logger = logging.getLogger("opennote.auth.model_catalog")


@dataclass
class ModelCatalog:
    models: List[str]
    source: str  # "live" | "cache" | "hardcoded"
    error: Optional[str] = None
    fetched_at: float = 0.0
    #: Preferred models that were dropped by the "chat model" filter. Kept so
    #: a live model never silently disappears from the menu.
    filtered: List[str] = None  # type: ignore[assignment]

    @property
    def is_live(self) -> bool:
        return self.source == "live"

    def describe(self) -> str:
        if self.source == "live":
            return "Live models from the provider"
        if self.source == "cache":
            age = max(0, int(time.time() - self.fetched_at))
            return f"Last live list ({age}s old) - refresh with 'opennote auth models <provider>'"
        return "Offline suggestions (not verified - may not be available)"


#: Last successful live inventory per provider, kept for the process lifetime.
_LAST_GOOD: dict = {}


def clear_cache() -> None:
    _LAST_GOOD.clear()


def _cache_key(provider: Any) -> str:
    return f"{getattr(provider, 'id', '?')}|{getattr(provider, 'models_url', '')}"


def resolve(provider: Any, result: Any) -> ModelCatalog:
    """Turn a ``validate_key`` result into an honestly-labelled model catalog.

    ``result`` may be ``None`` (validation raised) or a ValidationResult.
    """
    ok = result is not None and bool(getattr(result, "ok", False))
    models = list(getattr(result, "models", None) or []) if ok else []
    error = getattr(result, "error", None) if result is not None else "validation failed"

    if ok and models:
        ranked = rank_models(provider, models)
        # Never hide a live model just because a keyword filter disliked its
        # name (e.g. "...-audio" or "...-vision"). Ranking is a preference,
        # not an availability gate.
        filtered = [m for m in models if m not in ranked]
        catalog = ModelCatalog(
            models=ranked + filtered,
            source="live",
            fetched_at=time.time(),
            filtered=filtered,
        )
        _LAST_GOOD[_cache_key(provider)] = catalog
        return catalog

    cached = _LAST_GOOD.get(_cache_key(provider))
    if cached is not None and cached.models:
        return ModelCatalog(
            models=list(cached.models),
            source="cache",
            error=error,
            fetched_at=cached.fetched_at,
        )

    return ModelCatalog(
        models=list(getattr(provider, "preferred_models", ()) or ()),
        source="hardcoded",
        error=error,
    )
