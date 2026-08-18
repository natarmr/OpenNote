import pytest

from opennote.auth.models import (
    is_chat_model,
    is_deprecated,
    rank_models,
    select_default,
    usable_models,
)
from opennote.auth.registry import get_provider

JUNK_MODELS = [
    "text-embedding-3-small",
    "whisper-1",
    "tts-1",
    "dall-e-3",
    "gpt-image-1",
    "omni-moderation-latest",
    "gpt-4o-audio-preview",
    "gpt-4o-realtime-preview",
]


def test_non_chat_models_filtered():
    for m in JUNK_MODELS:
        assert not is_chat_model(m), m
    assert is_chat_model("gpt-4o")
    assert is_chat_model("claude-sonnet-5")
    assert is_chat_model("gemini-2.5-flash")


def test_deprecated_detected():
    assert is_deprecated("gpt-3.5-turbo-deprecated")
    assert is_deprecated("legacy-model")
    assert not is_deprecated("gpt-4o")


def test_usable_models_filters_both():
    ids = ["gpt-4o", "whisper-1", "gpt-3.5-turbo-deprecated", "claude-opus-5"]
    assert usable_models(ids) == ["gpt-4o", "claude-opus-5"]


def test_select_default_prefers_curated_order():
    provider = get_provider("openai")
    live = {"gpt-5.4-mini", "gpt-4o", "whisper-1", "gpt-5.4", "gpt-5.3"}
    assert select_default(provider, live) == "gpt-5.4"


def test_select_default_falls_back_to_heuristic():
    provider = get_provider("google")
    live = {"gemma3", "unknown-model-480b", "whisper-1"}
    assert select_default(provider, live) == "unknown-model-480b"


def test_select_default_none_when_only_junk():
    provider = get_provider("google")
    assert select_default(provider, set(JUNK_MODELS)) is None


def test_rank_models_order_preferred_first():
    provider = get_provider("anthropic")
    live = ["claude-opus-5", "claude-sonnet-5", "whisper-1", "claude-haiku-4-6"]
    ranked = rank_models(provider, live)
    assert ranked[0] == "claude-sonnet-5"
    assert "whisper-1" not in ranked
    assert set(ranked) == {"claude-sonnet-5", "claude-opus-5", "claude-haiku-4-6"}


def test_rank_models_dedupes():
    provider = get_provider("cerebras")
    ranked = rank_models(provider, ["llama-3.3-70b", "llama-3.3-70b", "gpt-oss-120b"])
    assert len(ranked) == len(set(ranked)) == 2


def test_rank_models_filters_non_chat():
    provider = get_provider("openai")
    ranked = rank_models(provider, {"gpt-4o", "whisper-1", "dall-e-3"})
    assert ranked == ["gpt-4o"]


def test_usable_models_excludes_exact_ids():
    ids = ["gpt-4o", "canopylabs/orpheus-v1-english", "allam-2-7b"]
    excluded = {"canopylabs/orpheus-v1-english", "allam-2-7b"}
    assert usable_models(ids, excluded=excluded) == ["gpt-4o"]
    assert usable_models(ids, excluded={"gpt-4o"}) == [
        "canopylabs/orpheus-v1-english",
        "allam-2-7b",
    ]


def test_groq_excludes_tts_models():
    provider = get_provider("groq")
    live = {
        "openai/gpt-oss-120b",
        "canopylabs/orpheus-v1-english",
        "canopylabs/orpheus-arabic-saudi",
        "allam-2-7b",
        "whisper-large-v3",
    }
    ranked = rank_models(provider, live)
    assert ranked == ["openai/gpt-oss-120b"]


def test_groq_prefers_namespaced_curated_id():
    provider = get_provider("groq")
    live = {"groq/compound", "openai/gpt-oss-20b", "qwen/qwen3.6-27b"}
    assert select_default(provider, live) == "openai/gpt-oss-20b"