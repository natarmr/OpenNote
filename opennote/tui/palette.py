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
        # ---- Notebook (session == notebook) ----
        PaletteEntry(
            title="Notebooks",
            description="Open / new / delete / rename notebooks",
            section="Notebook",
            submenu=lambda: screen._show_notebook_picker(),
        ),
        PaletteEntry(
            title="Export Notebook",
            description="Save this conversation as Markdown",
            section="Notebook",
            action=lambda: screen._export_transcript(),
        ),
        PaletteEntry(
            title="Undo Last Turn",
            description="Remove the last question and answer",
            section="Notebook",
            action=lambda: screen._undo_last_turn(),
        ),
        PaletteEntry(
            title="Remove Source",
            description="Remove an indexed source (frees a slot)",
            section="Notebook",
            submenu=lambda: screen._remove_source(),
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
        # ---- Studio ----
        PaletteEntry(
            title="Open Artifact",
            description="View a generated artifact in-terminal (mindmaps as a tree)",
            section="Studio",
            submenu=lambda: screen._open_artifact(""),
        ),
        PaletteEntry(
            title="Mind Map",
            description="Generate a mind map for a topic",
            section="Studio",
            submenu=lambda: screen._start_studio_palette("mindmap"),
            keywords="mindmap studio",
        ),
        PaletteEntry(
            title="Study Guide",
            description="Generate a study guide for a topic",
            section="Studio",
            submenu=lambda: screen._start_studio_palette("study"),
            keywords="study studio",
        ),
        PaletteEntry(
            title="FAQ",
            description="Generate an FAQ for a topic",
            section="Studio",
            submenu=lambda: screen._start_studio_palette("faq"),
            keywords="faq studio",
        ),
        PaletteEntry(
            title="Briefing",
            description="Generate a briefing for a topic",
            section="Studio",
            submenu=lambda: screen._start_studio_palette("briefing"),
            keywords="briefing studio",
        ),
        PaletteEntry(
            title="Timeline",
            description="Generate a timeline for a topic",
            section="Studio",
            submenu=lambda: screen._start_studio_palette("timeline"),
            keywords="timeline studio",
        ),
        PaletteEntry(
            title="Suggested Questions",
            description="Suggest follow-up questions for a topic",
            section="Studio",
            submenu=lambda: screen._start_studio_palette("suggest"),
            keywords="suggest questions studio",
        ),
        PaletteEntry(
            title="Narrated Audio",
            description="Narrate text as audio",
            section="Studio",
            submenu=lambda: screen._start_studio_palette("audio"),
            keywords="audio narrate studio",
        ),
        PaletteEntry(
            title="Narrated Video",
            description="Narrate a slideshow video",
            section="Studio",
            submenu=lambda: screen._start_studio_palette("video"),
            keywords="video narrate studio",
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
            submenu=lambda: screen._open_notebook_dialog(),
        ),
        PaletteEntry(
            title="New Notebook",
            description="Create a notebook",
            section="Notebook",
            action=lambda: screen._create_notebook_dialog(),
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