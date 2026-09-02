"""Runtime capability probe — determines what features are available at call time.

Used by agents/loop.py to advertise only tools whose requirements are met,
and by chat/prompt.py to tailor the system prompt to the model's actual
capabilities so it refuses gracefully rather than hallucinating success.

Probe results are serializable and can be injected for testing (FakeCapability).
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from opennote.auth.registry import Provider


@dataclass
class Capabilities:
    """What the runtime environment can do."""

    # --- Web search ---
    web_search: bool = False  # True when TAVILY_API_KEY is set

    # --- TTS ---
    tts_backend: Optional[str] = None  # one of: "groq", "openai", "gemini", "edge-tts"
    tts_available: bool = False  # at least one backend resolvable

    # --- Video ---
    video_available: bool = False  # tts_available AND ffmpeg on PATH

    # --- Embedding / retrieval ---
    retrieval_always_available: bool = True  # local embedding, ChromaDB

    # --- Artifacts dir ---
    artifacts_dir: Optional[str] = None  # path as string, or None


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes", "on")


def _probe() -> Capabilities:
    """Build the capability snapshot for the current process."""
    caps = Capabilities()
    caps.web_search = bool(os.environ.get("TAVILY_API_KEY"))
    try:
        from opennote.audio.tts import probe_tts_backend

        tts_backend = probe_tts_backend()
    except Exception:
        tts_backend = None
    caps.tts_backend = tts_backend
    caps.tts_available = tts_backend is not None

    # --- Video: TTS available AND ffmpeg on PATH ---
    caps.video_available = caps.tts_available and shutil.which("ffmpeg") is not None

    # --- Artifacts dir ---
    # Convention: notebook/<name>/artifacts/ — set by the caller (loop.py)
    caps.artifacts_dir = None  # filled in per invocation

    return caps


# Module-level cached probe; overridden in tests via FakeCapability
_cached: Optional[Capabilities] = None


def get_capabilities() -> Capabilities:
    """Return the current capability probe; test hook: set FakeCapability first."""
    global _cached
    if _cached is not None:
        return _cached
    return _probe()


def set_cached(caps: Capabilities) -> None:
    """For test use only — replace the global probe with a stub."""
    global _cached
    _cached = caps


# ---------------------------------------------------------------------------
# Test stub
# ---------------------------------------------------------------------------

class FakeCapability:  # noqa: N801
    """Minimal stand-in so callers can check attributes without importing Capabilities."""

    def __init__(self, **attrs):
        for k, v in attrs.items():
            setattr(self, k, v)


# Convenience: allow `caps.web_search` etc. when FakeCapability is injected
def _make_fake(**attrs) -> FakeCapability:
    return FakeCapability(**attrs)


# ---------------------------------------------------------------------------
# CLI helper (optional): print current capabilities for debugging
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    caps = get_capabilities()
    lines = [
        f"web_search: {caps.web_search}",
        f"tts_backend: {caps.tts_backend}",
        f"tts_available: {caps.tts_available}",
        f"video_available: {caps.video_available}",
    ]
    print("\n".join(lines))