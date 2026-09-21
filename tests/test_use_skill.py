"""Tests for /use skill arming + Studio/palette artifact entries."""
from __future__ import annotations

import time
from pathlib import Path

from opennote.skills.registry import Skill, SkillRegistry
from opennote.tui.app import OpenNoteApp
from opennote.tui.commands import lookup, make_commands
from opennote.tui.dialogs import InfoDialog, ItemListDialog
from opennote.tui.palette import make_palette
from opennote.tui.theme import DARK
from opennote.tui.widgets.prompt import PromptBar


def _manager(tmp_path):
    from opennote.notebooks import NotebookManager

    manager = NotebookManager(home=tmp_path)
    try:
        manager.get("t")
    except KeyError:
        manager.create("t")
    return manager


class _StubClient:
    provider_id = "groq"
    model = "m"


def _demo_registry():
    return SkillRegistry(
        [
            Skill(
                name="demo",
                description="Demo skill for tests",
                directory=Path("/tmp/demo-skill"),
                body="DEMO-SKILL-BODY",
                frontmatter={},
                files=["scripts/run.py"],
            )
        ]
    )


def _patch_registry(monkeypatch):
    reg = _demo_registry()
    monkeypatch.setattr(SkillRegistry, "discover", classmethod(lambda cls, cwd=None: reg))
    return reg


async def _wait_idle(pilot, bar, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await pilot.pause()
        if not bar.busy:
            return
    raise AssertionError("prompt bar never went idle")


def _transcript_text(transcript) -> str:
    return "\n".join("".join(seg.text for seg in strip) for strip in transcript.lines)


def _make_app(tmp_path):
    return OpenNoteApp(notebook_name="t", palette=DARK, manager=_manager(tmp_path), client=_StubClient())


# -- command registry --------------------------------------------------------

def test_use_command_registered():
    calls = []

    class Screen:
        def _use_skill(self, arg=""):
            calls.append(arg)

    cmds = make_commands(Screen())
    cmd = lookup("/use", cmds)
    assert cmd is not None
    assert cmd.arg_hint == "<skill> [task]"
    assert cmd.category == "Skills"
    cmd.handler("demo hi")
    assert calls == ["demo hi"]


def test_palette_has_studio_section():
    entries = make_palette(object())
    studio = [e for e in entries if e.section == "Studio"]
    titles = {e.title for e in studio}
    assert "Open Artifact" in titles
    assert {"Mind Map", "Study Guide", "FAQ", "Briefing", "Timeline"} <= titles
    assert "Narrated Audio" in titles and "Narrated Video" in titles


# -- /use behaviour (headless pilot) ------------------------------------------

async def test_use_unknown_skill_errors(tmp_path, monkeypatch):
    _patch_registry(monkeypatch)
    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen._use_skill("nope do things")
        await pilot.pause()
        assert app.screen._pending_skill is None
        assert "not found" in _transcript_text(app.screen.transcript)


async def test_use_no_arg_offers_picker_and_arms(tmp_path, monkeypatch):
    _patch_registry(monkeypatch)
    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen._use_skill("")
        await pilot.pause()
        from textual.widgets import ListView

        dlg = app.screen
        assert isinstance(dlg, ItemListDialog)
        assert dlg._items[0][0] == "demo"
        dlg.query_one("#item-list", ListView).focus()
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen._pending_skill == "demo"


async def test_use_name_only_arms_for_next_question(tmp_path, monkeypatch):
    _patch_registry(monkeypatch)
    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen._use_skill("demo")
        await pilot.pause()
        assert app.screen._pending_skill == "demo"
        assert "armed" in _transcript_text(app.screen.transcript)


async def test_use_with_task_injects_skill_into_turn(tmp_path, monkeypatch):
    _patch_registry(monkeypatch)
    import opennote.tui.screens.chat as chat_mod

    seen = []

    class _Result:
        answer = "done"
        provider_id = "groq"
        model = "m"
        usage = None

    class _Agent:
        messages = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "done"}]  # noqa: RUF012
        result = _Result()

    def _fake_turn(notebook, question, **kw):
        seen.append(question)
        return _Agent()

    monkeypatch.setattr(chat_mod, "agent_turn", _fake_turn)
    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.screen.query_one("#prompt-bar", PromptBar)
        app.screen._use_skill("demo draw a box")
        await _wait_idle(pilot, bar)
        assert app.screen._pending_skill is None
        assert len(seen) == 1
        assert "DEMO-SKILL-BODY" in seen[0]
        assert "draw a box" in seen[0]
        text = _transcript_text(app.screen.transcript)
        assert "Skill applied: demo" in text


async def test_clear_and_empty_undo_handle_pending(tmp_path, monkeypatch):
    _patch_registry(monkeypatch)
    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen._use_skill("demo")
        assert app.screen._pending_skill == "demo"
        app.screen._clear_transcript("")
        await pilot.pause()
        assert app.screen._pending_skill is None
        # Re-arm: empty undo must not drop it (nothing undone).
        app.screen._use_skill("demo")
        app.screen._undo_last_turn("")
        await pilot.pause()
        assert app.screen._pending_skill == "demo"


async def test_show_skill_explains_how_to_use(tmp_path, monkeypatch):
    _patch_registry(monkeypatch)
    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen._show_skill("demo")
        await pilot.pause()
        dlg = app.screen
        assert isinstance(dlg, InfoDialog)
        assert "/use demo" in dlg._body
        assert "OPENNOTE_ALLOW_SKILL_SCRIPTS" in dlg._body
        await pilot.press("escape")
        await pilot.pause()


async def test_studio_menu_offers_view_and_opens_picker(tmp_path, monkeypatch):
    _patch_registry(monkeypatch)
    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen._studio_topic = "topic"
        app.screen._open_studio_menu()
        await pilot.pause()
        dlg = app.screen
        assert isinstance(dlg, ItemListDialog)
        assert dlg._items[0][0] == "view"
        await pilot.press("escape")
        await pilot.pause()
        # No artifacts in a fresh notebook: view explains what to do.
        app.screen._on_studio_picked("view")
        await pilot.pause()
        assert "No artifacts yet" in _transcript_text(app.screen.transcript)
