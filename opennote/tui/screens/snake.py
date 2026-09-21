"""Waiting-room snake game (``/snake``): play while ingest/ask runs behind it.

The screen is a thin renderer over :mod:`opennote.tui.games.snake_logic`.
It is intentionally openable while the prompt is busy; background
completions surface as toasts (see the ``notify`` calls in
:mod:`opennote.tui.screens.chat`) above this modal.
"""

from __future__ import annotations

import random
from typing import Callable, Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, Static

from opennote.tui.games.snake_logic import (
    DOWN,
    LEFT,
    RIGHT,
    UP,
    new_game,
    render,
    set_direction,
    step,
)

_KEY_DIRECTIONS = {
    "up": UP,
    "w": UP,
    "down": DOWN,
    "s": DOWN,
    "left": LEFT,
    "a": LEFT,
    "right": RIGHT,
    "d": RIGHT,
}


class SnakeScreen(ModalScreen):
    """Modal snake game. ``esc``/``q`` quits, ``r`` restarts after game over."""

    BINDINGS = [
        Binding("escape", "quit_game", "Quit", show=False),
        Binding("q", "quit_game", "Quit", show=False),
        Binding("r", "restart_game", "Restart", show=False),
    ]

    def __init__(
        self,
        status_fn: Optional[Callable[[], str]] = None,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._status_fn = status_fn
        self._rng = random.Random()
        self._state = new_game()
        self._timer = None

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="dialog"):
            yield Label("Snake", id="dialog-title")
            yield Static("", id="snake-board")
            yield Label("", id="snake-status", classes="muted")
            yield Label(
                "arrows/wasd move - r restart - q/esc quit",
                id="dialog-hint",
                classes="muted",
            )

    def on_mount(self) -> None:
        self._draw()
        self._timer = self.set_interval(0.12, self._tick)

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def on_key(self, event) -> None:
        direction = _KEY_DIRECTIONS.get(event.key)
        if direction is not None:
            event.stop()
            event.prevent_default()
            set_direction(self._state, direction)

    def action_quit_game(self) -> None:
        self.dismiss(None)

    def action_restart_game(self) -> None:
        if not self._state.alive:
            self._state = new_game()
            if self._timer is None:
                self._timer = self.set_interval(0.12, self._tick)
            self._draw()

    def _tick(self) -> None:
        if not self._state.alive:
            return
        outcome = step(self._state, self._rng)
        if outcome in ("died", "won"):
            if self._timer is not None:
                self._timer.stop()
                self._timer = None
        self._draw()

    def _draw(self) -> None:
        try:
            board = self.query_one("#snake-board", Static)
            status = self.query_one("#snake-status", Label)
        except Exception:
            return
        board.update(render(self._state))
        if not self._state.alive:
            status.update(f"Game over - score {self._state.score} - press r to restart")
        else:
            background = ""
            if self._status_fn is not None:
                try:
                    background = self._status_fn() or ""
                except Exception:
                    background = ""
            score = f"Score: {self._state.score}"
            status.update(f"{score}   {background}".rstrip())
