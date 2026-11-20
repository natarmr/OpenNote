"""Tests for the single provider/model resolver and global last-used persistence."""
import json

import pytest

from opennote.auth.resolve import (
    SOURCE_CONFIG,
    SOURCE_DISCOVERY,
    SOURCE_FLAG,
    SOURCE_RECENT,
    SOURCE_SESSION,
    resolve_selection,
    set_last_used,
)
from opennote.chat.client import ChatError
from opennote.config import (
    clear_cached,
    get_config,
    global_config_path,
    resolve_env_placeholders,
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("OPENNOTE_HOME", str(home))
    (home / "auth.json").write_text("{}", encoding="utf-8")
    clear_cached()
    yield
    clear_cached()


def _auth(providers):
    home = get_config().source_path
    p = __import__("pathlib").Path(str(__import__("os").environ["OPENNOTE_HOME"])) / "auth.json"
    data = {}
    for pid, model in providers.items():
        data[pid] = {"model": model, "base_url_override": None}
    p.write_text(json.dumps(data), encoding="utf-8")


def test_no_providers_raises():
    with pytest.raises(ChatError, match="No provider is configured"):
        resolve_selection()


def test_explicit_flag_wins(monkeypatch):
    sel = resolve_selection(provider="groq")
    assert sel.provider == "groq"
    assert sel.source == SOURCE_FLAG
    assert sel.explicit


def test_session_selection_wins_over_recent(monkeypatch):
    set_last_used("groq", "qwen/qwen3.8-27b")
    nb = type("NB", (), {"provider_id": "meta", "model": "muse-spark-1.2"})()
    sel = resolve_selection(notebook=nb)
    assert sel.provider == "meta"
    assert sel.model == "muse-spark-1.2"
    assert sel.source == SOURCE_SESSION


def test_global_last_used_overrides_configured_default(tmp_path, monkeypatch):
    p = global_config_path()
    p.write_text(json.dumps({"model": "google/models/gemini-flash", "last_used": {"provider": "groq", "model": "qwen/qwen3.8-27b"}}), encoding="utf-8")
    clear_cached()
    _auth({"groq": "qwen/qwen3.8-27b", "google": "models/gemini-flash"})
    monkeypatch.setattr("opennote.auth.resolve.resolve_key", lambda pid: True)
    sel = resolve_selection()
    assert sel.provider == "groq"
    assert sel.source == SOURCE_RECENT


def test_configured_default_used_when_no_recent(tmp_path, monkeypatch):
    p = global_config_path()
    p.write_text(json.dumps({"model": "groq/qwen/qwen3.8-27b"}), encoding="utf-8")
    clear_cached()
    _auth({"groq": "qwen/qwen3.8-27b"})
    monkeypatch.setattr("opennote.auth.resolve.resolve_key", lambda pid: True)
    sel = resolve_selection()
    assert sel.provider == "groq"
    assert sel.model == "qwen/qwen3.8-27b"
    assert sel.source == SOURCE_CONFIG


def test_discovery_uses_last_validated_not_alphabetical(monkeypatch):
    from opennote.auth.config import AuthConfig

    cfg = AuthConfig()
    cfg.mark_added("google")
    cfg.set_model("google", "models/gemini-flash")
    cfg.mark_added("groq")
    cfg.set_model("groq", "qwen/qwen3.8-27b")
    cfg.mark_validated("groq")
    monkeypatch.setattr("opennote.auth.resolve.resolve_key", lambda pid: True)
    sel = resolve_selection()
    assert sel.provider == "groq"  # last validated, not 'google'
    assert sel.source == SOURCE_DISCOVERY


def test_set_last_used_writes_global_config():
    set_last_used("meta", "muse-spark-1.3")
    clear_cached()
    assert get_config().last_used == {"provider": "meta", "model": "muse-spark-1.3"}
    assert "last_used" in json.loads(global_config_path().read_text(encoding="utf-8"))


def test_env_substitution():
    import os

    os.environ["_TEST_MODEL_KEY"] = "sk-test-123"
    obj = {"model": "{env:_TEST_MODEL_KEY}", "nested": [{"k": "{env:_MISSING}"}]}
    out = resolve_env_placeholders(obj)
    assert out["model"] == "sk-test-123"
    assert out["nested"][0]["k"] == ""
