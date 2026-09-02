"""Palette of segmented command entries (opencode-style)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

from textual.widget import Widget

__all__ = ["PaletteEntry", "make_palette"]


@dataclass
class PaletteEntry:
    title: str
    description: str
    section: str
    action: Optional[Callable[[], None]] = None
    submenu: Optional[Callable[[], None]] = None
    keywords: str = ""


def make_palette(screen) -> List[PaletteEntry]:
    """Build the palette entries keyed to *screen* methods.

    The returned list is ordered into sections; each section header
    is rendered by ``CommandPalette`` as a dim separator.
    """
    return [
        # ---- Session ----
        PaletteEntry(
            title="New Session",
            description="Start a fresh conversation",
            section="Session",
            action=lambda: screen._new_session(),
        ),
        PaletteEntry(
            title="Switch Session",
            description="Pick from saved sessions (id, model, msgs, updated)",
            section="Session",
            submenu=lambda: screen._open_sessions_dialog(),
        ),
        PaletteEntry(
            title="Export Session",
            description="Save this conversation as Markdown",
            section="Session",
            action=lambda: screen._export_session(),
        ),
        PaletteEntry(
            title="Undo Last Turn",
            description="Remove the last question and answer",
            section="Session",
            action=lambda: screen._undo_last_turn(),
        ),
        # ---- Mode ----
        PaletteEntry(
            title="Ask Mode",
            description="Grounded Q&A with citations",
            section="Mode",
            action=lambda: screen._set_mode("ask"),
        ),
        PaletteEntry(
            title="Search Mode",
            description="LLM-free keyword + vector search",
            section="Mode",
            action=lambda: screen._set_mode("search"),
        ),
        PaletteEntry(
            title="Studio Mode",
            description="Generate mind maps, guides, audio, video",
            section="Mode",
            action=lambda: screen._enter_studio(),
        ),
        # ---- Provider ----
        PaletteEntry(
            title="Connect Provider",
            description="Add an API key and pick a model",
            section="Provider",
            submenu=lambda: screen._start_connect(),
        ),
        PaletteEntry(
            title="Switch Provider",
            description="Change the active provider",
            section="Provider",
            submenu=lambda: screen._open_provider_dialog(),
        ),
        PaletteEntry(
            title="Switch Model",
            description="Pick a different model for the current provider",
            section="Provider",
            submenu=lambda: screen._open_model_dialog(),
        ),
        # ---- Notebook ----
        PaletteEntry(
            title="Switch Notebook",
            description="Open another notebook (sources shown)",
            section="Notebook",
            submenu=lambda: screen._open_notebooks_dialog(),
        ),
        PaletteEntry(
            title="New Notebook",
            description="Create a notebook",
            section="Notebook",
            action=lambda: screen._create_notebook_dialog(),
        ),
        # ---- Studio ----
        PaletteEntry(
            title="Mind Map",
            description="Generate a mind map from a topic",
            section="Studio",
            action=lambda: screen._start_studio_palette("mindmap"),
        ),
        PaletteEntry(
            title="Study Guide",
            description="Generate a study guide from a topic",
            section="Studio",
            action=lambda: screen._start_studio_palette("study"),
        ),
        PaletteEntry(
            title="FAQ",
            description="Generate an FAQ from a topic",
            section="Studio",
            action=lambda: screen._start_studio_palette("faq"),
        ),
        PaletteEntry(
            title="Briefing",
            description="Generate a briefing from a topic",
            section="Studio",
            action=lambda: screen._start_studio_palette("briefing"),
        ),
        PaletteEntry(
            title="Timeline",
            description="Generate a timeline from a topic",
            section="Studio",
            action=lambda: screen._start_studio_palette("timeline"),
        ),
        PaletteEntry(
            title="Suggested Questions",
            description="Get suggested questions on a topic",
            section="Studio",
            action=lambda: screen._start_studio_palette("suggest"),
        ),
        # ---- Appearance ----
        PaletteEntry(
            title="Toggle Theme",
            description="Switch between dark and light",
            section="Appearance",
            action=lambda: screen._switch_theme(),
        ),
        # ---- Help ----
        PaletteEntry(
            title="Help",
            description="Show keyboard shortcuts and commands",
            section="Help",
            action=lambda: screen._show_help(),
        ),
    ]