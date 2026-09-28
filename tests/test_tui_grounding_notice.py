"""TUI surfacing of claims the grounding validator dropped (ledger L180).

Only the surviving claims are rendered into the answer, so a filtered answer is
otherwise indistinguishable from a complete one -- the user sees a confident,
shorter answer with no signal that anything was removed. The count now travels
out on ``AskResult`` and is shown as a transcript info line, mirroring the
existing "Skill applied:" line.

The handler is synchronous, so the screen method is called directly inside
``run_test()`` rather than posted from a worker -- no idle-polling, which is the
timing-sensitive pattern that flakes elsewhere in this suite.
"""
from __future__ import annotations

from opennote.tui.app import OpenNoteApp
from opennote.tui.screens.chat import TurnResult
from opennote.tui.theme import DARK


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


def _text(transcript) -> str:
    return "\n".join("".join(seg.text for seg in strip) for strip in transcript.lines)


def _make_app(tmp_path):
    return OpenNoteApp(notebook_name="t", palette=DARK, manager=_manager(tmp_path), client=_StubClient())


async def test_dropped_claims_are_surfaced_as_an_info_line(tmp_path):
    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.on_turn_result(
            TurnResult("q", "the answer [1]", "groq", "m", None, None, 2)
        )
        await pilot.pause()
        out = _text(app.screen.transcript)
        assert "2 claims omitted" in out, out
        assert "not sufficiently supported" in out, out
        # The answer itself is untouched.
        assert "the answer [1]" in out


async def test_single_drop_uses_singular_wording(tmp_path):
    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.on_turn_result(
            TurnResult("q", "the answer [1]", "groq", "m", None, None, 1)
        )
        await pilot.pause()
        out = _text(app.screen.transcript)
        assert "1 claim omitted" in out, out
        assert "1 claims" not in out


async def test_no_info_line_when_nothing_was_dropped(tmp_path):
    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.on_turn_result(
            TurnResult("q", "the answer [1]", "groq", "m", None, None, 0)
        )
        await pilot.pause()
        assert "omitted" not in _text(app.screen.transcript)


async def test_legacy_message_without_the_field_is_tolerated(tmp_path):
    """An older/other producer may omit the field; the handler must not crash."""
    app = _make_app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        msg = TurnResult("q", "the answer [1]", "groq", "m", None, None)
        del msg.dropped_claims
        app.screen.on_turn_result(msg)
        await pilot.pause()
        assert "the answer [1]" in _text(app.screen.transcript)
        assert "omitted" not in _text(app.screen.transcript)
