"""Tests for the layered opennote.json config loader."""
import json

from opennote.config import (
    OpenNoteConfig,
    clear_cached,
    config_paths,
    get_config,
    load_config,
    save_config,
    save_permission_rule,
)


def test_defaults_when_no_files(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENNOTE_CONFIG_HOME", str(tmp_path / "global"))
    cfg = load_config(cwd=tmp_path)
    assert cfg.model is None
    assert cfg.retrieval.bm25 is True
    assert cfg.retrieval.context_budget == 12000
    assert cfg.permissions == []
    assert cfg.tool_output.max_lines == 2000


def test_project_overrides_global(tmp_path, monkeypatch):
    g = tmp_path / "global"
    g.mkdir()
    monkeypatch.setenv("OPENNOTE_CONFIG_HOME", str(g))
    (g / "opennote.json").write_text(
        json.dumps({"model": "groq/global-model", "theme": "dark", "retrieval": {"top_k": 3, "bm25_alpha": 0.2}}),
        encoding="utf-8",
    )
    proj = tmp_path / ".opennote"
    proj.mkdir()
    (proj / "opennote.json").write_text(
        json.dumps({"model": "openai/project-model", "retrieval": {"top_k": 7}}),
        encoding="utf-8",
    )
    cfg = load_config(cwd=tmp_path)
    assert cfg.model == "openai/project-model"
    assert cfg.theme == "dark"  # preserved from global
    assert cfg.retrieval.top_k == 7  # overridden
    assert cfg.retrieval.bm25_alpha == 0.2  # preserved nested sibling


def test_jsonc_comments_and_trailing_commas(tmp_path, monkeypatch):
    g = tmp_path / "global"
    g.mkdir()
    monkeypatch.setenv("OPENNOTE_CONFIG_HOME", str(g))
    (g / "opennote.json").write_text(
        """{
          // top-level comment
          "model": "groq/qwen", // trailing comment
          "keybinds": {"leader": "ctrl+x",},
          "retrieval": {"bm25": false,},
        }""",
        encoding="utf-8",
    )
    cfg = load_config(cwd=tmp_path)
    assert cfg.model == "groq/qwen"
    assert cfg.keybinds["leader"] == "ctrl+x"
    assert cfg.retrieval.bm25 is False


def test_corrupt_file_is_backed_up_and_skipped(tmp_path, monkeypatch):
    g = tmp_path / "global"
    g.mkdir()
    monkeypatch.setenv("OPENNOTE_CONFIG_HOME", str(g))
    bad = g / "opennote.json"
    bad.write_text("{this is not json", encoding="utf-8")
    cfg = load_config(cwd=tmp_path)
    assert cfg.model is None
    backups = list(g.glob("opennote.json.corrupt.*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{this is not json"


def test_invalid_rule_shapes_are_dropped():
    cfg = OpenNoteConfig.from_dict(
        {
            "permissions": [
                {"action": "script", "effect": "deny"},
                {"action": "x"},  # no effect
                {"effect": "deny"},  # no action
                {"action": "y", "effect": "maybe"},  # bad effect
                "nonsense",
            ]
        }
    )
    assert cfg.permissions == [{"action": "script", "resource": "*", "effect": "deny"}]


def test_save_and_approval_rule(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENNOTE_CONFIG_HOME", str(tmp_path / "global"))
    monkeypatch.chdir(tmp_path)
    clear_cached()
    target = save_permission_rule({"action": "script", "resource": "diagrams/foo.py", "effect": "allow"})
    data = json.loads(target.read_text(encoding="utf-8"))
    assert {"action": "script", "resource": "diagrams/foo.py", "effect": "allow"} in data["permissions"]
    # idempotent
    save_permission_rule({"action": "script", "resource": "diagrams/foo.py", "effect": "allow"})
    data2 = json.loads(target.read_text(encoding="utf-8"))
    assert len(data2["permissions"]) == 1

    cfg = save_config(OpenNoteConfig(model="groq/qwen"), path=tmp_path / "custom.json")
    assert json.loads(cfg.read_text(encoding="utf-8"))["model"] == "groq/qwen"


def test_get_config_cached(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENNOTE_CONFIG_HOME", str(tmp_path / "global"))
    clear_cached()
    first = get_config()
    assert get_config() is first
    clear_cached()
    assert get_config() is not first


def test_config_paths_order(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENNOTE_CONFIG_HOME", str(tmp_path / "g"))
    paths = config_paths(tmp_path)
    assert paths[0].name == "opennote.json"
    assert paths[-1] == tmp_path / ".opennote" / "opennote.json"
