import pytest

from opennote.auth.registry import PROVIDERS, all_providers, get_provider


def test_all_six_expected_providers_present():
    ids = {p.id for p in PROVIDERS}
    assert ids == {"anthropic", "openai", "opencode", "cerebras", "groq", "google"}


def test_registry_integrity():
    seen = set()
    for p in PROVIDERS:
        assert p.id not in seen, f"duplicate provider id {p.id}"
        seen.add(p.id)
        assert p.flavor in ("openai", "anthropic")
        assert p.base_url.startswith("https://")
        assert p.models_url.startswith("https://")
        assert p.env_var.endswith("_API_KEY") or p.env_var == "OPENCODE_API_KEY"
        assert p.console_url.startswith("https://")
        assert p.preferred_models, f"{p.id} needs a preferred-model list"


def test_models_url_based_on_base_url():
    for p in PROVIDERS:
        assert p.models_url.startswith(p.base_url)


def test_get_provider_known():
    assert get_provider("groq").label == "Groq"


def test_get_provider_unknown_raises_with_hint():
    with pytest.raises(ValueError, match="Unknown provider 'nope'"):
        get_provider("nope")


def test_all_providers_returns_copy():
    first = all_providers()
    assert first == PROVIDERS
    assert first is not PROVIDERS