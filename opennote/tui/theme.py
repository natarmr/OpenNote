"""Theme palette for the TUI.

A faithful port of opencode's default theme (``opencode.json``) so the terminal
look matches the design we are replicating. The palette is exposed to Textual
as a registered Theme plus the CSS variables the stylesheet actually reads
(``$text-muted``, ``$background-element``, ``$primary``, ...), so widgets can
reference the same names opencode uses.
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
    text: str
    text_muted: str
    background: str
    background_panel: str
    background_element: str
    border: str
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
            "text": self.text,
            "text-muted": self.text_muted,
            "background": self.background,
            "background-panel": self.background_panel,
            "background-element": self.background_element,
            "border": self.border,
            "border-subtle": self.border_subtle,
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
    text="#eeeeee",
    text_muted="#808080",
    background="#0a0a0a",
    background_panel="#141414",
    background_element="#1e1e1e",
    border="#484848",
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
    text="#1a1a1a",
    text_muted="#8a8a8a",
    background="#ffffff",
    background_panel="#fafafa",
    background_element="#f5f5f5",
    border="#b8b8b8",
    border_subtle="#d4d4d4",
)

TOKYO_NIGHT = Palette(
    name="tokyonight",
    dark=True,
    primary="#ff9e64",
    secondary="#7aa2f7",
    accent="#bb9af7",
    error="#f7768e",
    warning="#e0af68",
    success="#9ece6a",
    text="#c0caf5",
    text_muted="#7a84a8",
    background="#1a1b26",
    background_panel="#24283b",
    background_element="#292e42",
    border="#565a78",
    border_subtle="#3d4266",
)

CATPPUCCIN = Palette(
    name="catppuccin",
    dark=True,
    primary="#f9e2af",
    secondary="#89b4fa",
    accent="#cba6f7",
    error="#f38ba8",
    warning="#fab387",
    success="#a6e3a1",
    text="#cdd6f4",
    text_muted="#9399b2",
    background="#11111b",
    background_panel="#181825",
    background_element="#1e1e2e",
    border="#585b70",
    border_subtle="#313244",
)

GRUVBOX = Palette(
    name="gruvbox",
    dark=True,
    primary="#fabd2f",
    secondary="#83a598",
    accent="#d3869b",
    error="#fb4934",
    warning="#fe8019",
    success="#b8bb26",
    text="#ebdbb2",
    text_muted="#928374",
    background="#282828",
    background_panel="#32302f",
    background_element="#3c3836",
    border="#665c54",
    border_subtle="#504945",
)

#: Named palettes selectable via ``opennote.json`` ``theme:`` / ``/theme``.
PALETTES: Dict[str, Palette] = {
    p.name: p for p in (DARK, LIGHT, TOKYO_NIGHT, CATPPUCCIN, GRUVBOX)
}


def by_name(name: str, dark: bool = True) -> Palette:
    """Resolve a theme name from config, falling back to a sensible default."""
    key = (name or "").strip().lower()
    if key in PALETTES:
        return PALETTES[key]
    if key in ("dark", "opencode"):
        return DARK
    if key in ("light", "opencode-light"):
        return LIGHT
    return LIGHT if not dark else DEFAULT


#: The palette the app starts with; override via ``--light`` or config later.
DEFAULT = DARK