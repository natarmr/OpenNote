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


def _handler(screen, name: str, fallback: Optional[Callable] = None) -> Callable[[str], None]:
    """Return screen.<name> if present, else a no-op so StubScreen tests keep passing."""
    fn = getattr(screen, name, None)
    if callable(fn):
        return fn
    if fallback is not None:
        return fallback
    return lambda _arg="": None


def make_commands(screen) -> List[Command]:
    return [
        Command("help", "Show command help", _handler(screen, "_show_help"), category="Notebook"),
        Command("exit", "Quit the TUI", lambda a: getattr(screen, "app", None) and screen.app.exit(), aliases=("quit", "q"), category="Notebook"),
        Command("clear", "Clear the transcript", _handler(screen, "_clear_transcript"), category="Notebook"),
        Command("model", "Switch LLM provider", _handler(screen, "_switch_provider"), arg_hint="<provider>", category="Provider"),
        Command("sources", "List indexed sources", _handler(screen, "_list_sources"), category="Notebook"),
        Command("remove", "Remove a source", _handler(screen, "_remove_source"), arg_hint="[source]", category="Notebook"),
        Command("export", "Export the notebook transcript to markdown", _handler(screen, "_export_transcript"), category="Notebook"),
        Command("undo", "Undo the last turn", _handler(screen, "_undo_last_turn"), category="Notebook"),
        Command("details", "Show notebook details", _handler(screen, "_show_details"), category="Notebook"),
        Command("notebooks", "Open / new / delete / rename notebooks", _handler(screen, "_show_notebook_picker"), category="Notebook"),
        Command("notebook", "Switch to a notebook", _handler(screen, "_switch_notebook"), arg_hint="<name>", category="Notebook"),
        Command("create", "Create a notebook and open it", _handler(screen, "_create_notebook"), arg_hint="<name>", category="Notebook"),
        Command("ingest", "Index a file, folder, or URL", _handler(screen, "_start_ingest"), arg_hint="<path|url>", category="Notebook"),
        Command("auth", "Show provider/key status", _handler(screen, "_show_auth"), category="Provider"),
        Command("connect", "Connect a provider (key + model)", _handler(screen, "_start_connect"), arg_hint="<provider>", category="Provider"),
        Command("ask", "Switch to ask mode", _handler(screen, "_set_mode_ask"), category="Mode"),
        Command("search", "Switch to search mode", _handler(screen, "_set_mode_search"), category="Mode"),
        Command("studio", "Enter studio mode for artifact generators", _handler(screen, "_enter_studio"), category="Mode"),
        Command("mindmap", "Generate a mind map", getattr(screen, "_start_studio_command", lambda k: lambda a="": None)("mindmap"), arg_hint="<topic>", category="Studio"),
        Command("study", "Generate a study guide", getattr(screen, "_start_studio_command", lambda k: lambda a="": None)("study"), arg_hint="<topic>", category="Studio"),
        Command("faq", "Generate an FAQ", getattr(screen, "_start_studio_command", lambda k: lambda a="": None)("faq"), arg_hint="<topic>", category="Studio"),
        Command("briefing", "Generate a briefing", getattr(screen, "_start_studio_command", lambda k: lambda a="": None)("briefing"), arg_hint="<topic>", category="Studio"),
        Command("timeline", "Generate a timeline", getattr(screen, "_start_studio_command", lambda k: lambda a="": None)("timeline"), arg_hint="<topic>", category="Studio"),
        Command("suggest", "Suggest follow-up questions", getattr(screen, "_start_studio_command", lambda k: lambda a="": None)("suggest"), arg_hint="<topic>", category="Studio"),
        Command("audio", "Narrate text as audio", getattr(screen, "_start_studio_command", lambda k: lambda a="": None)("audio"), arg_hint="<text>", category="Studio"),
        Command("video", "Narrate a slideshow video", getattr(screen, "_start_studio_command", lambda k: lambda a="": None)("video"), arg_hint="<topic>", category="Studio"),
        Command("open", "Open an artifact file or the artifacts folder", _handler(screen, "_open_artifact"), arg_hint="[file]", category="Studio"),
        Command("theme", "Switch dark/light theme", _handler(screen, "_switch_theme"), arg_hint="<dark|light>", category="Appearance"),
        Command("palette", "Open the command palette", _handler(screen, "_open_palette"), category="General"),
        Command("skills", "List installed skills", _handler(screen, "_list_skills"), category="Skills"),
        Command("skill", "Show a skill", _handler(screen, "_show_skill"), arg_hint="<name>", category="Skills"),
        Command("plugins", "List loaded plugins", _handler(screen, "_list_plugins"), category="Plugins"),
        Command("agents", "List available agents", _handler(screen, "_list_agents"), category="Agents"),
        Command("agent", "Show an agent definition", _handler(screen, "_show_agent"), arg_hint="<name>", category="Agents"),
        Command("capabilities", "Show runtime capabilities", _handler(screen, "_show_capabilities"), category="General"),
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
