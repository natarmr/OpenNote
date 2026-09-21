"""Pure snake game logic (no I/O) for the ``/snake`` waiting-room game.

Kept dependency-free and widget-free so it can be unit-tested
deterministically; the Textual screen in
:mod:`opennote.tui.screens.snake` is a thin renderer over this module.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

UP = (0, -1)
DOWN = (0, 1)
LEFT = (-1, 0)
RIGHT = (1, 0)

_OPPOSITE = {UP: DOWN, DOWN: UP, LEFT: RIGHT, RIGHT: LEFT}


@dataclass
class SnakeState:
    """Mutable game state; the head is ``snake[0]``."""

    width: int = 24
    height: int = 16
    snake: list = field(default_factory=list)
    direction: tuple = RIGHT
    food: tuple = (0, 0)
    score: int = 0
    alive: bool = True


def _spawn_food(state: SnakeState, rng: random.Random) -> tuple:
    free = [
        (x, y)
        for y in range(state.height)
        for x in range(state.width)
        if (x, y) not in state.snake
    ]
    if not free:
        return (-1, -1)  # board full: player wins, no more food
    return rng.choice(free)


def new_game(width: int = 24, height: int = 16, seed=None) -> SnakeState:
    """Create a fresh game with the snake centered and food placed."""
    rng = random.Random(seed)
    state = SnakeState(width=width, height=height)
    state.snake = [(width // 2 - i, height // 2) for i in range(3)]
    state.direction = RIGHT
    state.food = _spawn_food(state, rng)
    state.score = 0
    state.alive = True
    return state


def set_direction(state: SnakeState, direction: tuple) -> None:
    """Change direction unless it is a direct reversal (or the game is over)."""
    if not state.alive:
        return
    if direction != _OPPOSITE.get(state.direction):
        state.direction = direction


def step(state: SnakeState, rng: random.Random) -> str:
    """Advance one tick. Returns ``"moved"``, ``"ate"``, ``"died"`` or ``"won"``."""
    if not state.alive:
        return "died"
    dx, dy = state.direction
    hx, hy = state.snake[0]
    head = (hx + dx, hy + dy)
    if not (0 <= head[0] < state.width and 0 <= head[1] < state.height):
        state.alive = False
        return "died"
    if head in state.snake:
        state.alive = False
        return "died"
    state.snake.insert(0, head)
    if head == state.food:
        state.score += 1
        if len(state.snake) >= state.width * state.height:
            state.alive = False
            return "won"
        state.food = _spawn_food(state, rng)
        return "ate"
    state.snake.pop()
    return "moved"


def render(state: SnakeState) -> str:
    """Render the board as plain ASCII text (head ``@``, body ``o``, food ``*``)."""
    grid = [[" "] * state.width for _ in range(state.height)]
    fx, fy = state.food
    if 0 <= fx < state.width and 0 <= fy < state.height:
        grid[fy][fx] = "*"
    for i, (x, y) in enumerate(state.snake):
        grid[y][x] = "@" if i == 0 else "o"
    top = "+" + "-" * state.width + "+"
    rows = [top] + ["|" + "".join(row) + "|" for row in grid] + [top]
    return "\n".join(rows)
