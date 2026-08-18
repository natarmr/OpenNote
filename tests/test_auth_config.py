import json

from opennote.auth.config import AuthConfig


def _write_config(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_empty_config(tmp_path):
    config = AuthConfig(path=tmp_path / "auth.json")
    assert config.providers() == {}


def test_missing_file_loads_empty(tmp_path):
    assert AuthConfig(path=tmp_path / "auth.json").providers() == {}


def test_roundtrip(tmp_path):
    config = AuthConfig(path=tmp_path / "auth.json")
    config.set_model("groq", "llama-3.3-70b-versatile")
    config.mark_added("groq")
    config.mark_validated("groq")

    loaded = AuthConfig(path=tmp_path / "auth.json")
    settings = loaded.get("groq")
    assert settings is not None
    assert settings.model == "llama-3.3-70b-versatile"
    assert settings.added_at
    assert settings.last_validated_at


def test_no_secrets_written(tmp_path):
    config = AuthConfig(path=tmp_path / "auth.json")
    config.set_model("openai", "gpt-4o")
    raw = tmp_path / "auth.json"
    text = raw.read_text(encoding="utf-8")
    assert "sk-" not in text
    assert "API_KEY" not in text


def test_remove(tmp_path):
    config = AuthConfig(path=tmp_path / "auth.json")
    config.set_model("openai", "gpt-4o")
    assert config.remove("openai") is True
    assert config.remove("openai") is False
    assert AuthConfig(path=tmp_path / "auth.json").providers() == {}


def test_corrupt_config_loads_empty(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text("{not json", encoding="utf-8")
    assert AuthConfig(path=path).providers() == {}


def test_corrupt_file_backed_up(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text("{not json", encoding="utf-8")
    AuthConfig(path=path).providers() == {}
    backup = tmp_path / "auth.json.corrupt"
    assert backup.exists(), "corrupt config must be preserved, not overwritten"
    assert backup.read_text(encoding="utf-8") == "{not json"


def test_mark_added_sets_timestamp(tmp_path):
    config = AuthConfig(path=tmp_path / "auth.json")
    settings = config.mark_added("cerebras")
    assert settings.added_at
    assert config.get("cerebras").added_at == settings.added_at


def test_base_url_override_roundtrip(tmp_path):
    config = AuthConfig(path=tmp_path / "auth.json")
    config.set_base_url("opencode", "http://localhost:9999/v1")
    assert AuthConfig(path=tmp_path / "auth.json").get("opencode").base_url_override == "http://localhost:9999/v1"