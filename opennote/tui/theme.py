"""Theme palette for the TUI.

A faithful port of opencode's default theme (``opencode.json``) so the terminal
look matches the design we are replicating. The palette is exposed to Textual
as a registered Theme plus CSS variables (``$text-muted``, ``$background-element``,
``$border-active``, ...) so widgets can reference the same names opencode uses.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from textual.theme import Theme


def _tint(bg: str, fg: str, ratio: float) -> str:
    """Blend ``bg`` toward ``fg`` by ``ratio`` (opencode's ``tint()``)."""
    b = tuple(int(bg[i : i + 2], 16) for i in (1, 3, 5))
    f = tuple(int(fg[i : i + 2], 16) for i in (1, 3, 5))
    mixed = tuple(round(x + (y - x) * ratio) for x, y in zip(b, f))
    return "#%02x%02x%02x" % mixed


@dataclass(frozen=True)
class Palette:
    """All colors the UI uses, in opencode's naming."""

    name: str
    dark: bool
    primary: str
    secondary: str
    accent: str
    error: str
    warning: str
    success: str
    info: str
    text: str
    text_muted: str
    background: str
    background_panel: str
    background_element: str
    border: str
    border_active: str
    border_subtle: str

    @property
    def shadow(self) -> str:
        """Logo shadow color = background tinted toward text."""
        return _tint(self.background, self.text, 0.25)

    def variables(self) -> Dict[str, str]:
        return {
            "primary": self.primary,
            "secondary": self.secondary,
            "accent": self.accent,
            "error": self.error,
            "warning": self.warning,
            "success": self.success,
            "info": self.info,
            "text": self.text,
            "text-muted": self.text_muted,
            "background": self.background,
            "background-panel": self.background_panel,
            "background-element": self.background_element,
            "border": self.border,
            "border-active": self.border_active,
            "border-subtle": self.border_subtle,
            "shadow": self.shadow,
        }

    def to_textual(self) -> Theme:
        """Register as a Textual theme (dark/light variants keyed by name)."""
        return Theme(
            name=self.name,
            primary=self.primary,
            secondary=self.secondary,
            accent=self.accent,
            error=self.error,
            warning=self.warning,
            success=self.success,
            foreground=self.text,
            background=self.background,
            surface=self.background_element,
            panel=self.background_panel,
            dark=self.dark,
            variables=self.variables(),
        )


DARK = Palette(
    name="opencode",
    dark=True,
    primary="#fab283",
    secondary="#5c9cf5",
    accent="#9d7cd8",
    error="#e06c75",
    warning="#f5a742",
    success="#7fd88f",
    info="#56b6c2",
    text="#eeeeee",
    text_muted="#808080",
    background="#0a0a0a",
    background_panel="#141414",
    background_element="#1e1e1e",
    border="#484848",
    border_active="#606060",
    border_subtle="#3c3c3c",
)

LIGHT = Palette(
    name="opencode-light",
    dark=False,
    primary="#3b7dd8",
    secondary="#7b5bb6",
    accent="#d68c27",
    error="#d1383d",
    warning="#d68c27",
    success="#3d9a57",
    info="#318795",
    text="#1a1a1a",
    text_muted="#8a8a8a",
    background="#ffffff",
    background_panel="#fafafa",
    background_element="#f5f5f5",
    border="#b8b8b8",
    border_active="#a0a0a0",
    border_subtle="#d4d4d4",
)

#: The palette the app starts with; override via ``--light`` or config later.
DEFAULT = DARK