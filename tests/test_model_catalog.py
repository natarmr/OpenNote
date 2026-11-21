"""Model catalog: live list, last-good cache, and honestly-labelled fallback."""
import types

import pytest

from opennote.auth.model_catalog import ModelCatalog, clear_cache, resolve


def _provider():
    return types.SimpleNamespace(
        id="cerebras",
        models_url="https://api.cerebras.ai/v1/models",
        preferred_models=["llama-3.3-70b", "qwen3-coder-480b"],
        excluded_models=(),
    )


def _result(ok=True, models=None, error=None):
    return types.SimpleNamespace(ok=ok, models=models or [], error=error)


@pytest.fixture(autouse=True)
def _clean():
    clear_cache()
    yield
    clear_cache()


def test_live_models_are_used_and_ranked():
    p = _provider()
    cat = resolve(p, _result(models=["qwen-3.8-27b", "gpt-oss-120b"]))
    assert cat.source == "live"
    assert cat.is_live
    assert set(cat.models) == {"qwen-3.8-27b", "gpt-oss-120b"}


def test_live_models_are_not_hidden_by_keyword_filter():
    """A live model with 'audio' in its name must still be selectable."""
    p = _provider()
    cat = resolve(p, _result(models=["gpt-4o-audio-preview", "qwen-3.8-27b"]))
    assert "gpt-4o-audio-preview" in cat.models
    assert cat.filtered == ["gpt-4o-audio-preview"]


def test_failed_validation_uses_last_good_live_list():
    p = _provider()
    resolve(p, _result(models=["live-one", "live-two"]))
    cat = resolve(p, _result(ok=False, error="network"))
    assert cat.source == "cache"
    assert cat.models == ["live-one", "live-two"]
    assert "Last live list" in cat.describe()


def test_no_live_ever_falls_back_to_hardcoded_but_is_labelled():
    p = _provider()
    cat = resolve(p, None)
    assert cat.source == "hardcoded"
    assert not cat.is_live
    assert "OFFLINE" in cat.describe() or "not verified" in cat.describe()
    assert cat.models == ["llama-3.3-70b", "qwen3-coder-480b"]


def test_cache_is_per_provider():
    p = _provider()
    other = types.SimpleNamespace(
        id="groq", models_url="https://api.groq.com/openai/v1/models",
        preferred_models=["m1"], excluded_models=(),
    )
    resolve(p, _result(models=["cerebras-live"]))
    cat = resolve(other, _result(ok=False, error="network"))
    assert cat.source == "hardcoded"
    assert cat.models == ["m1"]


def test_empty_live_result_falls_through():
    p = _provider()
    cat = resolve(p, _result(models=[]))
    assert cat.source == "hardcoded"
