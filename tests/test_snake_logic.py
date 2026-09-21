"""Unit tests for the pure snake game logic (no Textual involved)."""

import random

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


def test_new_game_centered_with_food():
    state = new_game(width=10, height=8, seed=1)
    assert state.alive
    assert state.score == 0
    assert len(state.snake) == 3
    assert state.snake[0] == (5, 4)
    assert state.food not in state.snake


def test_step_moves_head():
    state = new_game(width=10, height=8, seed=1)
    head = state.snake[0]
    assert step(state, random.Random(0)) == "moved"
    assert state.snake[0] == (head[0] + 1, head[1])
    assert len(state.snake) == 3  # no growth without food


def test_eating_grows_and_scores():
    state = new_game(width=10, height=8, seed=1)
    hx, hy = state.snake[0]
    state.food = (hx + 1, hy)  # place food right ahead
    assert step(state, random.Random(0)) == "ate"
    assert state.score == 1
    assert len(state.snake) == 4


def test_wall_collision_dies():
    state = new_game(width=5, height=5, seed=1)
    state.snake = [(4, 2), (3, 2), (2, 2)]
    state.direction = RIGHT
    assert step(state, random.Random(0)) == "died"
    assert not state.alive


def test_self_collision_dies():
    state = new_game(width=10, height=10, seed=1)
    # Head moving down into its own body.
    state.snake = [(5, 5), (5, 6), (4, 6), (4, 5)]
    state.direction = DOWN
    assert step(state, random.Random(0)) == "died"
    assert not state.alive


def test_reversal_ignored():
    state = new_game(width=10, height=8, seed=1)
    set_direction(state, LEFT)  # opposite of RIGHT: ignored
    assert state.direction == RIGHT
    set_direction(state, UP)
    assert state.direction == UP


def test_dead_game_stays_dead():
    state = new_game(width=5, height=5, seed=1)
    state.alive = False
    assert step(state, random.Random(0)) == "died"
    set_direction(state, UP)  # no-op, must not raise


def test_render_contains_head_body_food():
    state = new_game(width=6, height=4, seed=1)
    text = render(state)
    assert "@" in text
    assert "o" in text
    assert "*" in text
    assert text.count("\n") == 4 + 1  # 4 rows + top/bottom borders
