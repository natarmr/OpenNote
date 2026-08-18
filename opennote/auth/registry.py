"""Provider registry for BYOK.

A deliberately small, curated set of providers. Each provider exposes a
``GET /models`` endpoint used both for key validation and for automated model
selection (so one uniform flow covers every provider).
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


PROVIDERS: List[Provider] = [
    Provider(
        id="anthropic",
        label="Anthropic",
        flavor="anthropic",
        base_url="https://api.anthropic.com",
        models_url="https://api.anthropic.com/v1/models",
        env_var="ANTHROPIC_API_KEY",
        console_url="https://console.anthropic.com/settings/keys",
        preferred_models=(
            "claude-sonnet-5",
            "claude-sonnet-4-6",
            "claude-sonnet-4-5",
            "claude-opus-5",
            "claude-opus-4-8",
            "claude-haiku-4-6",
            "claude-haiku-4-5",
        ),
    ),
    Provider(
        id="openai",
        label="OpenAI",
        flavor="openai",
        base_url="https://api.openai.com/v1",
        models_url="https://api.openai.com/v1/models",
        env_var="OPENAI_API_KEY",
        console_url="https://platform.openai.com/api-keys",
        preferred_models=(
            "gpt-5.4",
            "gpt-5.4-mini",
            "gpt-5.3",
            "gpt-5.3-mini",
            "gpt-4.1",
            "gpt-4o",
            "gpt-4o-mini",
        ),
    ),
    Provider(
        id="opencode",
        label="OpenCode (Zen/Go)",
        flavor="openai",
        base_url="https://opencode.ai/zen/v1",
        models_url="https://opencode.ai/zen/v1/models",
        env_var="OPENCODE_API_KEY",
        console_url="https://opencode.ai/auth",
        preferred_models=(
            "kimi-k3",
            "kimi-k2.6",
            "glm-5",
            "glm-5.1",
            "deepseek-v4-flash",
            "deepseek-v4-pro",
            "minimax-m3",
            "deepseek-v4-flash-free",
        ),
    ),
    Provider(
        id="cerebras",
        label="Cerebras",
        flavor="openai",
        base_url="https://api.cerebras.ai/v1",
        models_url="https://api.cerebras.ai/v1/models",
        env_var="CEREBRAS_API_KEY",
        console_url="https://inference.cerebras.ai/",
        preferred_models=(
            "llama-3.3-70b",
            "qwen3-coder-480b",
            "qwen3-coder-32b",
            "gpt-oss-120b",
            "gpt-oss-20b",
            "llama-3.1-8b",
        ),
    ),
    Provider(
        id="groq",
        label="Groq",
        flavor="openai",
        base_url="https://api.groq.com/openai/v1",
        models_url="https://api.groq.com/openai/v1/models",
        env_var="GROQ_API_KEY",
        console_url="https://console.groq.com/keys",
        preferred_models=(
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "groq/compound",
            "groq/compound-mini",
            "qwen/qwen3.6-27b",
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
        ),
        excluded_models=(
            "canopylabs/orpheus-v1-english",
            "canopylabs/orpheus-arabic-saudi",
            "allam-2-7b",
        ),
    ),
    Provider(
        id="google",
        label="Google Gemini",
        flavor="openai",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        models_url="https://generativelanguage.googleapis.com/v1beta/openai/models",
        env_var="GEMINI_API_KEY",
        console_url="https://aistudio.google.com/apikey",
        preferred_models=(
            "gemini-3.7-flash",
            "gemini-3.5-flash",
            "gemini-3-flash",
            "gemini-2.5-flash",
            "gemini-3-pro",
            "gemini-2.5-pro",
        ),
    ),
]

BY_ID: Dict[str, Provider] = {p.id: p for p in PROVIDERS}


def get_provider(provider_id: str) -> Provider:
    try:
        return BY_ID[provider_id]
    except KeyError:
        available = ", ".join(p.id for p in PROVIDERS)
        raise ValueError(
            f"Unknown provider '{provider_id}'. Available: {available}"
        )


def all_providers() -> List[Provider]:
    return list(PROVIDERS)