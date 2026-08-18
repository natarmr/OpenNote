"""Slash-command registry for the TUI.

Each command maps a set of names to a handler that receives the chat screen and
the rest of the input line. The registry powers the autocomplete popup, the
ctrl+p command palette, and ``/help`` in one place.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional


@dataclass
class Command:
    """A slash command the user can invoke from the prompt.

    ``handler`` is a bound method taking the argument string: ``handler(arg)``.
    """

    name: str
    description: str
    handler: Callable[[str], None]
    aliases: tuple = ()
    arg_hint: str = ""

    @property
    def names(self) -> tuple:
        return (self.name, *self.aliases)

    def matches(self, name: str) -> bool:
        return name in self.names

    def display(self) -> str:
        if self.arg_hint:
            return f"/{self.name} {self.arg_hint}"
        return f"/{self.name}"


def make_commands(screen) -> List[Command]:
    """Build the registry bound to a specific chat screen.

    Handlers delegate to screen methods so the registry stays declarative.
    """
    return [
        Command("help", "Show command help", screen._show_help),
        Command("exit", "Quit the TUI", lambda a: screen.app.exit(), aliases=("quit", "q")),
        Command("new", "Start a fresh session", screen._new_session),
        Command("clear", "Clear the transcript", screen._clear_transcript),
        Command("sessions", "List sessions and resume one", screen._open_sessions_dialog),
        Command("resume", "Resume a session", screen._resume_session, arg_hint="<id>"),
        Command("continue", "Resume the most recent session", screen._resume_last),
        Command(
            "model",
            "Switch LLM provider",
            screen._switch_provider,
            arg_hint="<provider>",
        ),
        Command("sources", "List indexed sources", screen._list_sources),
        Command("export", "Export the session to markdown", screen._export_session),
        Command("undo", "Undo the last turn", screen._undo_last_turn),
        Command("details", "Show session/notebook details", screen._show_details),
        Command("notebooks", "List notebooks and switch", screen._open_notebooks_dialog),
        Command("notebook", "Switch to a notebook", screen._switch_notebook, arg_hint="<name>"),
        Command("create", "Create a notebook and open it", screen._create_notebook, arg_hint="<name>"),
        Command(
            "ingest",
            "Index a file, folder, or URL",
            screen._start_ingest,
            arg_hint="<path|url>",
        ),
        Command("auth", "Show provider/key status", screen._show_auth),
        Command(
            "connect",
            "Connect a provider (key + model)",
            screen._start_connect,
            arg_hint="<provider>",
        ),
        Command("ask", "Switch to ask mode", screen._set_mode_ask),
        Command("search", "Switch to search mode", screen._set_mode_search),
        Command("theme", "Switch dark/light theme", screen._switch_theme, arg_hint="<dark|light>"),
        Command("palette", "Open the command palette", screen._open_palette),
    ]


def lookup(name: str, commands: List[Command]) -> Optional[Command]:
    """Find a command by name (with or without leading slash)."""
    stripped = name.lstrip("/").lower()
    for cmd in commands:
        if cmd.matches(stripped):
            return cmd
    return None


def matches_prefix(prefix: str, commands: List[Command]) -> List[Command]:
    """Commands whose name starts with *prefix* (after a leading slash)."""
    stripped = prefix.lstrip("/").lower()
    return [cmd for cmd in commands if cmd.name.startswith(stripped)]


def help_text(commands: List[Command]) -> str:
    """Render the command list for ``/help`` and the palette."""
    lines = ["Slash commands:"]
    for cmd in sorted(commands, key=lambda c: c.name):
        line = f"  {cmd.display()}"
        if len(line) < 24:
            line = line.ljust(24)
        lines.append(f"{line}  {cmd.description}")
    return "\n".join(lines)