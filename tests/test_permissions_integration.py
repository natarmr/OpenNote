"""Integration tests: permission engine wired into tool dispatch + agents."""
from types import SimpleNamespace

import pytest

from opennote.agents import tools as tools_mod
from opennote.agents.defs import AgentDef, AgentRegistry, _parse_agent_file
from opennote.agents.tools import ToolContext, execute_tool
from opennote.security.permissions import PermissionDenied, PermissionEngine


class FakeRetriever:
    def __init__(self):
        self.calls = []

    def search(self, query, top_k=None, source=None):
        self.calls.append(query)
        return []

    def sources(self):
        return []


def _ctx(engine):
    return ToolContext(retriever=FakeRetriever(), permission_engine=engine)


def test_execute_tool_denied_by_config():
    eng = PermissionEngine(rules=[{"action": "search", "resource": "*", "effect": "deny"}])
    ctx = _ctx(eng)
    with pytest.raises(PermissionDenied):
        execute_tool("search", ctx, {"query": "hello"})
    assert ctx.retriever.calls == []


def test_execute_tool_ask_without_handler_fails_closed():
    eng = PermissionEngine(rules=[{"action": "search", "resource": "*", "effect": "ask"}])
    ctx = _ctx(eng)
    with pytest.raises(PermissionDenied, match="no interactive approver"):
        execute_tool("search", ctx, {"query": "hello"})


def test_execute_tool_ask_with_handler_succeeds_and_can_save():
    saved = []
    eng = PermissionEngine(
        rules=[{"action": "search", "resource": "*", "effect": "ask"}],
        handler=lambda req: "always",
        save_approval=saved.append,
    )
    ctx = _ctx(eng)
    execute_tool("search", ctx, {"query": "hello"})
    assert saved and saved[0]["action"] == "search"


def test_execute_tool_engine_absent_is_backward_compatible():
    ctx = ToolContext(retriever=FakeRetriever())
    execute_tool("search", ctx, {"query": "hello"})
    assert ctx.retriever.calls == ["hello"]


def test_agent_frontmatter_steps_and_color(tmp_path):
    p = tmp_path / "reviewer.md"
    p.write_text(
        "---\ndescription: reviewer\nmode: subagent\nsteps: 8\ncolor: '#ff6b6b'\n"
        "permission:\n  script: deny\n---\nYou review things.\n",
        encoding="utf-8",
    )
    a = _parse_agent_file(p)
    assert a.steps == 8
    assert a.color == "#ff6b6b"
    assert a.permission["script"] == "deny"
    assert a.prompt == "You review things."

    bad = tmp_path / "bad.md"
    bad.write_text("---\nmode: subagent\ncolor: red\n---\nx", encoding="utf-8")
    with pytest.raises(ValueError, match="color"):
        _parse_agent_file(bad)


def test_task_tool_honors_agent_metadata(monkeypatch):
    """_task_execute passes prompt/model/steps from the subagent definition."""
    captured = {}

    def fake_agent_turn(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            result=SimpleNamespace(answer="ok", results=[]),
            messages=[],
        )

    monkeypatch.setattr(tools_mod, "_agent_turn", lambda: None, raising=False)
    from opennote.agents import loop as loop_mod

    monkeypatch.setattr(loop_mod, "agent_turn", fake_agent_turn)
    # _task_execute imports agent_turn from loop at call time, so patch works.

    sub = AgentDef(
        name="special",
        description="special",
        mode="subagent",
        prompt="You are special.",
        model="groq/special-model",
        steps=7,
    )
    reg = AgentRegistry([sub])
    ctx = ToolContext(
        retriever=FakeRetriever(),
        notebook=SimpleNamespace(name="nb"),
        agent_registry=reg,
        depth=0,
        agent_name="ask",
    )
    out = tools_mod._task_execute(ctx, "special", "do the thing")
    assert out == "ok"
    assert captured["system_prompt"] == "You are special."
    assert captured["model_override"] == "groq/special-model"
    assert captured["max_rounds"] == 7
    assert captured["agent_name"] == "special"
