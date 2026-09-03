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
from typing import List, Optional


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

    # --- Skills ---
    skills_available: bool = False
    skills_count: int = 0

    # --- Plugins ---
    plugins_loaded: List[str] = field(default_factory=list)

    # --- Supermemory ---
    supermemory_available: bool = False  # True when SUPERMEMORY_API_KEY is set

    # --- Skill scripts ---
    skill_scripts_allowed: bool = False  # True when OPENNOTE_ALLOW_SKILL_SCRIPTS=1

    # --- Agents ---
    agents_available: List[str] = field(default_factory=list)


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

    # --- Supermemory ---
    caps.supermemory_available = bool(os.environ.get("SUPERMEMORY_API_KEY"))

    # --- Skill scripts ---
    caps.skill_scripts_allowed = _env_bool("OPENNOTE_ALLOW_SKILL_SCRIPTS")

    # --- Skills (lightweight probe — just count, no full parse) ---
    try:
        from opennote.skills.registry import SkillRegistry

        reg = SkillRegistry.discover()
        caps.skills_available = not reg.is_empty()
        caps.skills_count = len(reg.list())
    except Exception:
        pass

    # --- Plugins (store plugin names, not tool names) ---
    try:
        from opennote.plugins.loader import PluginLoader, PluginContext

        loader = PluginLoader(PluginContext(capabilities=caps))
        loader.load()
        caps.plugins_loaded = sorted(h._name for h in loader.hooks)
    except Exception:
        pass

    # --- Agents ---
    try:
        from opennote.agents.defs import AgentRegistry

        areg = AgentRegistry.discover()
        caps.agents_available = areg.names()
    except Exception:
        pass

    return caps


# Module-level cached probe; overridden in tests via FakeCapability
_cached: Optional[Capabilities] = None


def get_capabilities() -> Capabilities:
    """Return the current capability probe; test hook: set FakeCapability first."""
    global _cached
    if _cached is not None:
        return _cached
    return _probe()


def clear_cached() -> None:
    """Clear cached capabilities (e.g. after env change)."""
    global _cached
    _cached = None


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
        f"skills_available: {caps.skills_available} ({caps.skills_count})",
        f"plugins_loaded: {caps.plugins_loaded}",
        f"supermemory_available: {caps.supermemory_available}",
        f"skill_scripts_allowed: {caps.skill_scripts_allowed}",
        f"agents_available: {caps.agents_available}",
    ]
    print("\n".join(lines))