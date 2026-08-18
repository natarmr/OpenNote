"""OpenNote logo art, rendered in opencode's terminal style.

The banner is drawn in the same 4-row block-art language as opencode's logo:
each letter is a 4-column glyph whose rows use ``█▀▀█``-style top/middle/bottom
bars, with the shadow marks ``_ ^ ~ ,`` resolved by the renderer:

- ``_``  -> a space whose *background* is the shadow color
- ``^``  -> ``▀`` (top half block) whose *background* is the shadow color
- ``~``  -> ``▀`` whose *foreground* is the shadow color
- ``,``  -> ``▄`` whose *foreground* is the shadow color

The word is split into a left half (rendered muted, non-bold) and a right half
(rendered in the text color, bold), exactly like opencode's ``logo.left`` /
``logo.right``.
"""
from __future__ import annotations

from rich.text import Text

#: "Open" (left) and "Note" (right), glyph rows 1-3 plus a floating ``▄`` on row 0.
LOGO_LEFT = [
    "                   ",
    "█▀▀█ █▀▀█ █▀▀█ █▀▀▄",
    "█__█ █__█ █^^^ █__█",
    "▀▀▀▀ █▀▀▀ ▀▀▀▀ ▀~~▀",
]
LOGO_RIGHT = [
    "             ▄     ",
    "█▀▀█ █▀▀█ █▀▀█ █▀▀█",
    "█_^█ █__█ __█_ █^^^",
    "▀~▀▀ ▀▀▀▀ __▀_ ▀▀▀▀",
]

MARKS = "_^~,"


def _append_glyph(out: Text, ch: str, fg: str, bold: bool, shadow: str) -> None:
    """Append a single logo character, resolving shadow marks."""
    if ch == "_":
        out.append(" ", style=f"on {shadow}")
    elif ch == "^":
        out.append("▀", style=f"{fg} on {shadow}")
    elif ch == "~":
        out.append("▀", style=shadow)
    elif ch == ",":
        out.append("▄", style=shadow)
    elif ch == " ":
        out.append(" ")
    else:
        style = f"bold {fg}" if bold else fg
        out.append(ch, style=style)


def render_logo(palette) -> Text:
    """Render the OpenNote banner as a rich ``Text`` (4 lines)."""
    shadow = palette.shadow
    lines = Text()
    for lrow, rrow in zip(LOGO_LEFT, LOGO_RIGHT):
        line = Text()
        for ch in lrow:
            _append_glyph(line, ch, palette.text_muted, False, shadow)
        for ch in rrow:
            _append_glyph(line, ch, palette.text, True, shadow)
        lines.append(line)
        lines.append("\n")
    return lines