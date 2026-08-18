"""CLI regression tests (L09, L11, L24, L31, L37, L41)."""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from opennote.agents.session import append_messages, new_session
from opennote.notebooks import NotebookManager

runner = CliRunner()


@pytest.fixture
def cli_mod(monkeypatch, tmp_path):
    """A clean CLI module bound to an isolated OPENNOTE_HOME."""
    home = tmp_path / "home"
    monkeypatch.setenv("OPENNOTE_HOME", str(home))
    import opennote.cli as mod

    monkeypatch.setattr(mod, "manager", NotebookManager(home=home))
    return mod


class FakeClient:
    def __init__(self, provider_id="groq", model="openai/gpt-oss-120b"):
        self.provider_id = provider_id
        self.model = model


# --- L09: auth verify with an unknown provider is a friendly error ---

def test_auth_verify_unknown_provider_friendly_error(cli_mod):
    result = runner.invoke(cli_mod.app, ["auth", "verify", "nope"])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "Traceback" not in result.output


# --- L11: searching an empty notebook does not traceback ---

def test_search_empty_notebook_friendly_error(cli_mod):
    cli_mod.manager.create("default")
    result = runner.invoke(cli_mod.app, ["search", "-n", "default", "anything"])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "Traceback" not in result.output


def test_golden_empty_notebook_friendly_error(cli_mod, tmp_path):
    cli_mod.manager.create("default")
    golden = tmp_path / "g.tsv"
    golden.write_text("q\tsrc\n", encoding="utf-8")
    result = runner.invoke(cli_mod.app, ["golden", "-n", "default", str(golden)])
    assert result.exit_code == 1
    assert "Error:" in result.output


# --- L41: resume must pick the most recent session (not always start fresh) ---

def test_chat_resumes_most_recent_session(cli_mod, monkeypatch):
    nb = cli_mod.manager.create("default")
    old = new_session(nb, "groq", "m")
    append_messages(nb, old["id"], [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}])
    recent = new_session(nb, "groq", "m")

    monkeypatch.setattr(cli_mod, "default_provider", lambda: "groq")
    monkeypatch.setattr(cli_mod, "get_client", lambda pid: FakeClient())
    result = runner.invoke(cli_mod.app, ["chat", "-n", "default"], input="/exit\n")
    assert result.exit_code == 0
    assert "Resumed session" in result.output
    assert recent["id"][:8] in result.output
    assert old["id"][:8] not in result.output


# --- L37: /model with tab-separated input still parses the arg ---

def test_chat_slash_model_tab_parsed(cli_mod, monkeypatch):
    nb = cli_mod.manager.create("default")
    new_session(nb, "groq", "m")
    monkeypatch.setattr(cli_mod, "default_provider", lambda: "groq")
    monkeypatch.setattr(cli_mod, "get_client", lambda pid: FakeClient(provider_id=pid))
    result = runner.invoke(cli_mod.app, ["chat", "-n", "default"], input="/model\tgroq\n/exit\n")
    assert "Provider switched to groq" in result.output


# --- L24: /model updates session metadata on disk ---

def test_chat_slash_model_updates_session_metadata(cli_mod, monkeypatch):
    from opennote.agents.session import load_session, list_sessions

    nb = cli_mod.manager.create("default")
    new_session(nb, "groq", "m")
    monkeypatch.setattr(cli_mod, "default_provider", lambda: "groq")
    monkeypatch.setattr(cli_mod, "get_client", lambda pid: FakeClient(provider_id=pid))
    runner.invoke(cli_mod.app, ["chat", "-n", "default"], input="/model openai\n/exit\n")
    session = load_session(nb, list_sessions(nb)[0]["id"])
    assert session["provider_id"] == "openai"
    assert session["model"] == "openai/gpt-oss-120b"


# --- L31: a network error in the loop does not kill the REPL ---

def test_chat_survives_loop_network_error(cli_mod, monkeypatch):
    import opennote.agents.loop as loop_mod

    nb = cli_mod.manager.create("default")
    new_session(nb, "groq", "m")
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