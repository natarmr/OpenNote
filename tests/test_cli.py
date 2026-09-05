"""CLI regression tests."""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from opennote.notebooks import NotebookManager
from opennote.transcript import append_messages

runner = CliRunner()


@pytest.fixture
def cli_mod(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("OPENNOTE_HOME", str(home))
    import opennote.cli as mod

    monkeypatch.setattr(mod, "get_manager", lambda: NotebookManager(home=home))
    return mod


class FakeClient:
    def __init__(self, provider_id="groq", model="openai/gpt-oss-120b"):
        self.provider_id = provider_id
        self.model = model


def test_auth_verify_unknown_provider_friendly_error(cli_mod):
    result = runner.invoke(cli_mod.app, ["auth", "verify", "nope"])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "Traceback" not in result.output


def test_search_empty_notebook_friendly_error(cli_mod):
    cli_mod.get_manager().create("default")
    result = runner.invoke(cli_mod.app, ["search", "-n", "default", "anything"])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "Traceback" not in result.output


def test_golden_empty_notebook_friendly_error(cli_mod, tmp_path):
    cli_mod.get_manager().create("default")
    golden = tmp_path / "g.tsv"
    golden.write_text("q\tsrc\n", encoding="utf-8")
    result = runner.invoke(cli_mod.app, ["golden", "-n", "default", str(golden)])
    assert result.exit_code == 1
    assert "Error:" in result.output


def test_chat_loads_transcript_history(cli_mod, monkeypatch):
    nb = cli_mod.get_manager().create("default")
    append_messages(nb, [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}])
    monkeypatch.setattr(cli_mod, "default_provider", lambda: "groq")
    monkeypatch.setattr(cli_mod, "get_client", lambda pid: FakeClient())
    result = runner.invoke(cli_mod.app, ["chat", "-n", "default"], input="/exit\n")
    assert result.exit_code == 0
    assert "2 messages" in result.output or "history loaded" in result.output


def test_chat_slash_model_tab_parsed(cli_mod, monkeypatch):
    nb = cli_mod.get_manager().create("default")
    monkeypatch.setattr(cli_mod, "default_provider", lambda: "groq")
    monkeypatch.setattr(cli_mod, "get_client", lambda pid: FakeClient(provider_id=pid))
    result = runner.invoke(cli_mod.app, ["chat", "-n", "default"], input="/model\tgroq\n/exit\n")
    assert "Provider switched to groq" in result.output


def test_chat_slash_model_updates_notebook_metadata(cli_mod, monkeypatch):
    nb = cli_mod.get_manager().create("default")
    monkeypatch.setattr(cli_mod, "default_provider", lambda: "groq")
    monkeypatch.setattr(cli_mod, "get_client", lambda pid: FakeClient(provider_id=pid))
    runner.invoke(cli_mod.app, ["chat", "-n", "default"], input="/model openai\n/exit\n")
    reloaded = cli_mod.get_manager().get("default")
    assert reloaded.provider_id == "openai"
    assert reloaded.model == "openai/gpt-oss-120b"


def test_chat_survives_loop_network_error(cli_mod, monkeypatch):
    import opennote.agents.loop as loop_mod

    nb = cli_mod.get_manager().create("default")
    monkeypatch.setattr(cli_mod, "default_provider", lambda: "groq")
    monkeypatch.setattr(cli_mod, "get_client", lambda pid: FakeClient())

    calls = []

    def flaky_agent_turn(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("gateway timeout")
        return None

    monkeypatch.setattr(loop_mod, "agent_turn", flaky_agent_turn)
    result = runner.invoke(cli_mod.app, ["chat", "-n", "default"], input="hello\n/exit\n")
    assert result.exit_code == 0
    assert "Error: gateway timeout" in result.output


def test_ingest_cap_rejected(cli_mod, tmp_path, monkeypatch):
    """Ingesting a 6th distinct source is rejected."""
    from unittest.mock import MagicMock

    nb = cli_mod.get_manager().create("default")
    for i in range(5):
        nb.sources.append(f"/tmp/src{i}.txt")
    nb.save()
    mock_manifest = MagicMock()
    mock_manifest.is_indexed.return_value = False
    mock_vm = MagicMock()
    mock_vm.manifest = mock_manifest
    monkeypatch.setattr("opennote.ingest.pipeline.VectorStoreManager", lambda *a, **kw: mock_vm)
    # Mock file hash and parser to avoid real embedding
    monkeypatch.setattr("opennote.ingest.pipeline.compute_file_hash", lambda p: "abc")
    monkeypatch.setattr("opennote.ingest.pipeline.get_parser_for_file", lambda *a, **kw: MagicMock(parse=lambda p, s: [MagicMock()]))
    mock_vm.add_chunks.return_value = 1
    f = tmp_path / "extra.txt"
    f.write_text("hello", encoding="utf-8")
    result = runner.invoke(cli_mod.app, ["ingest", str(f), "-n", "default"])
    assert result.exit_code == 1
    assert "limit" in result.output.lower()


def test_remove_source_cmd(cli_mod):
    nb = cli_mod.get_manager().create("default")
    nb.sources.append("/tmp/a.txt")
    nb.save()
    result = runner.invoke(cli_mod.app, ["remove", "a.txt", "-n", "default"])
    assert result.exit_code == 0
    assert "Removed" in result.output
    reloaded = cli_mod.get_manager().get("default")
    assert "/tmp/a.txt" not in reloaded.sources
