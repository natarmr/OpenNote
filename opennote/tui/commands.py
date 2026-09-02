"""Slash-command registry for the TUI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional


@dataclass
class Command:
    name: str
    description: str
    handler: Callable[[str], None]
    aliases: tuple = ()
    arg_hint: str = ""
    category: str = "General"

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
    return [
        Command("help", "Show command help", screen._show_help, category="Notebook"),
        Command("exit", "Quit the TUI", lambda a: screen.app.exit(), aliases=("quit", "q"), category="Notebook"),
        Command("clear", "Clear the transcript", screen._clear_transcript, category="Notebook"),
        Command("model", "Switch LLM provider", screen._switch_provider, arg_hint="<provider>", category="Provider"),
        Command("sources", "List indexed sources", screen._list_sources, category="Notebook"),
        Command("remove", "Remove a source", screen._remove_source, arg_hint="[source]", category="Notebook"),
        Command("export", "Export the notebook transcript to markdown", screen._export_transcript, category="Notebook"),
        Command("undo", "Undo the last turn", screen._undo_last_turn, category="Notebook"),
        Command("details", "Show notebook details", screen._show_details, category="Notebook"),
        Command("notebooks", "Open / new / delete / rename notebooks", screen._show_notebook_picker, category="Notebook"),
        Command("notebook", "Switch to a notebook", screen._switch_notebook, arg_hint="<name>", category="Notebook"),
        Command("create", "Create a notebook and open it", screen._create_notebook, arg_hint="<name>", category="Notebook"),
        Command("ingest", "Index a file, folder, or URL", screen._start_ingest, arg_hint="<path|url>", category="Notebook"),
        Command("auth", "Show provider/key status", screen._show_auth, category="Provider"),
        Command("connect", "Connect a provider (key + model)", screen._start_connect, arg_hint="<provider>", category="Provider"),
        Command("ask", "Switch to ask mode", screen._set_mode_ask, category="Mode"),
        Command("search", "Switch to search mode", screen._set_mode_search, category="Mode"),
        Command("studio", "Enter studio mode for artifact generators", screen._enter_studio, category="Studio"),
        Command("mindmap", "Generate a mind map", screen._start_studio_command("mindmap"), arg_hint="<topic>", category="Studio"),
        Command("study", "Generate a study guide", screen._start_studio_command("study"), arg_hint="<topic>", category="Studio"),
        Command("faq", "Generate an FAQ", screen._start_studio_command("faq"), arg_hint="<topic>", category="Studio"),
        Command("briefing", "Generate a briefing", screen._start_studio_command("briefing"), arg_hint="<topic>", category="Studio"),
        Command("timeline", "Generate a timeline", screen._start_studio_command("timeline"), arg_hint="<topic>", category="Studio"),
        Command("suggest", "Suggest follow-up questions", screen._start_studio_command("suggest"), arg_hint="<topic>", category="Studio"),
        Command("audio", "Narrate text as audio", screen._start_studio_command("audio"), arg_hint="<text>", category="Studio"),
        Command("video", "Narrate a slideshow video", screen._start_studio_command("video"), arg_hint="<topic>", category="Studio"),
        Command("open", "Open an artifact file or the artifacts folder", screen._open_artifact, arg_hint="[file]", category="Studio"),
        Command("theme", "Switch dark/light theme", screen._switch_theme, arg_hint="<dark|light>", category="Appearance"),
        Command("palette", "Open the command palette", screen._open_palette, category="General"),
    ]


def lookup(name: str, commands: List[Command]) -> Optional[Command]:
    stripped = name.lstrip("/").lower()
    for cmd in commands:
        if cmd.matches(stripped):
            return cmd
    return None


def matches_prefix(prefix: str, commands: List[Command]) -> List[Command]:
    stripped = prefix.lstrip("/").lower()
    return [cmd for cmd in commands if cmd.name.startswith(stripped)]


def help_text(commands: List[Command]) -> str:
    categories: dict = {}
    for cmd in sorted(commands, key=lambda c: c.name):
        cat = cmd.category
        categories.setdefault(cat, []).append(cmd)
    lines: list = []
    for cat, cmds in categories.items():
        lines.append(f"  {cat}:")
        for cmd in cmds:
            line = f"    {cmd.display()}"
            if len(line) < 28:
                line = line.ljust(28)
            lines.append(f"{line}  {cmd.description}")
    return "\n".join(lines)
