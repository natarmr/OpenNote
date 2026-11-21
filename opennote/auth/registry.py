"""Provider registry for BYOK.

A deliberately small, curated set of providers. Each provider exposes a
``GET /models`` endpoint used both for key validation and for automated model
selection (so one uniform flow covers every provider).

There are **no hardcoded model names** for built-in providers. Provider model
inventories go stale quickly, so the only source of truth is the live
``/models`` response. The ``preferred_models`` / ``excluded_models`` /
``tts_models`` fields remain only so *user-defined* providers in
``opennote.json`` can supply hints; built-ins leave them empty.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    flavor: str  # "openai" (Authorization: Bearer) or "anthropic" (x-api-key)
    base_url: str
    models_url: str
    env_var: str
    console_url: str
    preferred_models: Tuple[str, ...] = ()
    excluded_models: Tuple[str, ...] = ()
    tts_models: Tuple[str, ...] = ()


PROVIDERS: List[Provider] = [
    Provider(
        id="anthropic",
        label="Anthropic",
        flavor="anthropic",
        base_url="https://api.anthropic.com",
        models_url="https://api.anthropic.com/v1/models",
        env_var="ANTHROPIC_API_KEY",
        console_url="https://console.anthropic.com/settings/keys",
    ),
    Provider(
        id="openai",
        label="OpenAI",
        flavor="openai",
        base_url="https://api.openai.com/v1",
        models_url="https://api.openai.com/v1/models",
        env_var="OPENAI_API_KEY",
        console_url="https://platform.openai.com/api-keys",
    ),
    Provider(
        id="opencode",
        label="OpenCode (Zen/Go)",
        flavor="openai",
        base_url="https://opencode.ai/zen/v1",
        models_url="https://opencode.ai/zen/v1/models",
        env_var="OPENCODE_API_KEY",
        console_url="https://opencode.ai/auth",
    ),
    Provider(
        id="cerebras",
        label="Cerebras",
        flavor="openai",
        base_url="https://api.cerebras.ai/v1",
        models_url="https://api.cerebras.ai/v1/models",
        env_var="CEREBRAS_API_KEY",
        console_url="https://inference.cerebras.ai/",
    ),
    Provider(
        id="groq",
        label="Groq",
        flavor="openai",
        base_url="https://api.groq.com/openai/v1",
        models_url="https://api.groq.com/openai/v1/models",
        env_var="GROQ_API_KEY",
        console_url="https://console.groq.com/keys",
    ),
    Provider(
        id="google",
        label="Google Gemini",
        flavor="openai",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        models_url="https://generativelanguage.googleapis.com/v1beta/openai/models",
        env_var="GEMINI_API_KEY",
        console_url="https://aistudio.google.com/apikey",
    ),
    Provider(
        id="meta",
        label="Meta",
        flavor="openai",
        base_url="https://api.meta.ai/v1",
        models_url="https://api.meta.ai/v1/models",
        env_var="MODEL_API_KEY",
        console_url="https://dev.meta.ai/",
    ),
]

BY_ID: Dict[str, Provider] = {p.id: p for p in PROVIDERS}

# Alias so the original "muse" request also resolves to Meta's API.
_ALIASES: Dict[str, str] = {"muse": "meta"}


def _config_providers() -> Dict[str, Provider]:
    """Build Provider entries from ``opennote.json`` ``providers:`` blocks.

    Minimal opencode-style custom providers: any OpenAI-compatible endpoint can
    be added with a base_url + model list. Keys come from the OS keychain or an
    env var (``env`` / ``api_key_env``), never from the config file itself.
    """
    try:
        from opennote.config import get_config

        cfg = get_config()
    except Exception:
        return {}
    out: Dict[str, Provider] = {}
    for pid, entry in cfg.providers.items():
        if not isinstance(entry, dict):
            continue
        base = str(entry.get("base_url") or entry.get("base_url_override") or "").rstrip("/")
        if not base:
            continue
        env = str(entry.get("env") or entry.get("api_key_env") or f"{pid.upper()}_API_KEY")
        name = str(entry.get("name") or pid.title())
        models = tuple(str(m) for m in (entry.get("models") or []))
        out[pid] = Provider(
            id=pid,
            label=name,
            flavor=str(entry.get("flavor") or "openai"),
            base_url=base,
            models_url=base + "/models",
            env_var=env,
            console_url=str(entry.get("console_url") or ""),
            preferred_models=models,
        )
    return out


def get_provider(provider_id: str) -> Provider:
    pid = _ALIASES.get(provider_id, provider_id)
    if pid in BY_ID:
        return BY_ID[pid]
    custom = _config_providers()
    if pid in custom:
        return custom[pid]
    available = ", ".join(p.id for p in PROVIDERS)
    raise ValueError(f"Unknown provider '{provider_id}'. Available: {available}")


def all_providers() -> List[Provider]:
    return list(PROVIDERS) + list(_config_providers().values())