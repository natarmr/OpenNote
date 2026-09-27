"""Plugin loader tests.

The loader executes Python found in working-tree-derived directories, so these
cover the opt-in gate, the directories it searches, and the isolation contract
that ``load()`` documents. Before this file the loader had **zero** coverage, so
a trust-decision fix here would have landed with no harness to detect breakage.
"""
import sys
from pathlib import Path

import pytest

import opennote.plugins.loader as loader_mod
from opennote.plugins.loader import PluginLoader, plugins_allowed

PLUGIN_SRC = """
def register(ctx):
    return {"tools": {"probe_tool": {
        "description": "probe",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "execute": lambda ctx, **kw: "ok",
    }}}
"""


@pytest.fixture(autouse=True)
def _no_builtin_env(monkeypatch):
    """Keep the supermemory builtin out of these tests."""
    monkeypatch.delenv("SUPERMEMORY_API_KEY", raising=False)
    monkeypatch.setattr(loader_mod, "_unavailable_warned", set())
    yield


def _write_plugin(directory: Path, name: str = "evil.py", src: str = PLUGIN_SRC) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    p.write_text(src, encoding="utf-8")
    return p


# --- the gate ---------------------------------------------------------------


def test_plugins_allowed_reads_the_env_var(monkeypatch):
    for value, expected in [("1", True), ("true", True), ("YES", True), ("on", True)]:
        monkeypatch.setenv("OPENNOTE_ALLOW_PLUGINS", value)
        assert plugins_allowed() is expected
    for value in ["", "0", "false", "no", "off", "maybe"]:
        monkeypatch.setenv("OPENNOTE_ALLOW_PLUGINS", value)
        assert plugins_allowed() is False
    monkeypatch.delenv("OPENNOTE_ALLOW_PLUGINS", raising=False)
    assert plugins_allowed() is False


def test_file_plugin_is_not_executed_without_opt_in(tmp_path, monkeypatch):
    """The whole point: a .py next to the working tree must not run by default."""
    plugin = _write_plugin(tmp_path / ".opennote" / "plugins", "evil.py", "raise AssertionError('executed')")
    monkeypatch.setattr(loader_mod, "_plugin_dirs", lambda cwd=None: [plugin.parent])
    monkeypatch.delenv("OPENNOTE_ALLOW_PLUGINS", raising=False)
    ldr = PluginLoader()
    ldr.load()
    assert ldr.tools == {}
    assert ldr.hooks == []


def test_file_plugin_loads_with_opt_in(tmp_path, monkeypatch):
    _write_plugin(tmp_path / "plugins", "good.py")
    monkeypatch.setattr(loader_mod, "_plugin_dirs", lambda cwd=None: [tmp_path / "plugins"])
    monkeypatch.setenv("OPENNOTE_ALLOW_PLUGINS", "1")
    ldr = PluginLoader()
    ldr.load()
    assert "probe_tool" in ldr.tools
    assert ldr.get_dispatch("probe_tool") is not None


def test_skipped_plugins_are_recorded_and_warned_once(tmp_path, monkeypatch, caplog):
    _write_plugin(tmp_path / "plugins", "a.py")
    _write_plugin(tmp_path / "plugins", "b.py")
    monkeypatch.setattr(loader_mod, "_plugin_dirs", lambda cwd=None: [tmp_path / "plugins"])
    monkeypatch.delenv("OPENNOTE_ALLOW_PLUGINS", raising=False)
    with caplog.at_level("WARNING"):
        first = PluginLoader()
        first.load()
        assert len(first.skipped) == 2
        warnings = [r for r in caplog.records if "OPENNOTE_ALLOW_PLUGINS" in r.getMessage()]
        assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
        caplog.clear()
        # Second loader in the same process must not re-warn.
        PluginLoader().load()
        assert not [r for r in caplog.records if "OPENNOTE_ALLOW_PLUGINS" in r.getMessage()]


def test_underscore_and_package_admission_rules(tmp_path, monkeypatch):
    """Underscore files stay excluded; a package __init__ is admitted."""
    plugins = tmp_path / "plugins"
    _write_plugin(plugins, "_private.py", "raise AssertionError('executed')")
    (plugins / "pkg").mkdir()
    (plugins / "pkg" / "__init__.py").write_text(PLUGIN_SRC, encoding="utf-8")
    monkeypatch.setattr(loader_mod, "_plugin_dirs", lambda cwd=None: [plugins])
    monkeypatch.setenv("OPENNOTE_ALLOW_PLUGINS", "1")
    ldr = PluginLoader()
    ldr.load()
    assert "probe_tool" in ldr.tools


def test_builtin_loads_without_the_opt_in(tmp_path, monkeypatch):
    """Built-ins ship in-repo and are exempt — supermemory is keyed separately."""
    import opennote.plugins.builtin.supermemory as sm

    monkeypatch.setenv("SUPERMEMORY_API_KEY", "test-key")
    monkeypatch.setattr(loader_mod, "_plugin_dirs", lambda cwd=None: [])
    monkeypatch.delenv("OPENNOTE_ALLOW_PLUGINS", raising=False)
    ldr = PluginLoader()
    ldr.load()
    assert "supermemory" in [h._name for h in ldr.hooks]
    assert "memory_search" in ldr.tools
    assert sm is not None


# --- discovery surface ------------------------------------------------------


def test_plugin_dirs_include_cwd_home_and_config(tmp_path, monkeypatch):
    monkeypatch.setattr(loader_mod, "default_home", lambda: tmp_path / "home")
    dirs = [str(d) for d in loader_mod._plugin_dirs(cwd=tmp_path)]
    assert any(d.endswith("plugins") for d in dirs)
    assert str(tmp_path / "home" / "plugins") in dirs
    assert any(".config" in d for d in dirs)


def test_plugin_dirs_are_deduped(tmp_path, monkeypatch):
    monkeypatch.setattr(loader_mod, "default_home", lambda: tmp_path)
    dirs = [str(d) for d in loader_mod._plugin_dirs(cwd=tmp_path)]
    assert len(dirs) == len(set(dirs))


def test_walk_worktree_roots_includes_the_git_root(tmp_path, monkeypatch):
    """The git root is appended *before* the .git break, so its .opennote/plugins is in scope."""
    from opennote import fsutil

    (tmp_path / ".git").mkdir()
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    roots = fsutil.walk_worktree_roots(nested)
    assert tmp_path in roots


# --- isolation contract -----------------------------------------------------


def test_plugin_calling_sys_exit_at_import_does_not_abort_the_process(tmp_path, monkeypatch):
    """_import_file re-raises BaseException; load() must not let that kill the CLI."""
    _write_plugin(tmp_path / "plugins", "boom.py", "import sys\nsys.exit(3)\n")
    _write_plugin(tmp_path / "plugins", "good.py")
    monkeypatch.setattr(loader_mod, "_plugin_dirs", lambda cwd=None: [tmp_path / "plugins"])
    monkeypatch.setenv("OPENNOTE_ALLOW_PLUGINS", "1")
    ldr = PluginLoader()
    ldr.load()  # must not raise SystemExit
    assert "probe_tool" in ldr.tools, "a later good plugin should still load"
    assert not any("boom" in str(m) for m in sys.modules if "boom" in str(m))


def test_isolated_module_is_removed_from_sys_modules_on_failure(tmp_path, monkeypatch):
    _write_plugin(tmp_path / "plugins", "broken.py", "raise ValueError('nope')\n")
    monkeypatch.setattr(loader_mod, "_plugin_dirs", lambda cwd=None: [tmp_path / "plugins"])
    monkeypatch.setenv("OPENNOTE_ALLOW_PLUGINS", "1")
    PluginLoader().load()
    assert not [m for m in sys.modules if "_file_broken_" in m]


def test_candidate_files_helper_reports_what_would_run(tmp_path):
    plugins = tmp_path / "plugins"
    _write_plugin(plugins, "a.py")
    _write_plugin(plugins, "_skip.py")
    (plugins / "pkg").mkdir()
    (plugins / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (plugins / "notes.txt").write_text("x", encoding="utf-8")
    found = {p.name for p in loader_mod._candidate_plugin_files(plugins)}
    assert found == {"a.py", "__init__.py"}


# --- capabilities surface ---------------------------------------------------


def test_capabilities_reports_plugins_allowed_and_skipped(tmp_path, monkeypatch):
    from opennote.capabilities import Capabilities

    caps = Capabilities()
    assert caps.plugins_allowed is False
    assert caps.plugins_skipped == []
