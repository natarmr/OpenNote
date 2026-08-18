"""Banner rendering tests: logo glyphs and shadow marks."""
import pytest

from opennote.tui.banner import LOGO_LEFT, LOGO_RIGHT, MARKS, _append_glyph, render_logo
from opennote.tui.theme import DARK


def test_logo_rows_have_equal_width():
    assert len(LOGO_LEFT) == len(LOGO_RIGHT) == 4
    width = len(LOGO_LEFT[0])
    for row in LOGO_LEFT + LOGO_RIGHT:
        assert len(row) == width, f"ragged row: {row!r}"


def test_only_allowed_chars():
    allowed = set(" █▀▄") | set(MARKS)
    for row in LOGO_LEFT + LOGO_RIGHT:
        assert set(row) <= allowed, row


def test_marks_cover_block_shapes():
    # "_" becomes a space on the shadow bg, "^"/"~" become halves, "," a low half.
    for mark in MARKS:
        text = _append_glyph_inline(mark)
        assert len(text) == 1


def _append_glyph_inline(ch):
    from rich.text import Text

    out = Text()
    _append_glyph(out, ch, "#ffffff", True, "#123456")
    return out


def test_shadow_space_is_bg_style():
    out = _append_glyph_inline("_")
    assert out.plain == " "
    style = _parse_style(out)
    assert style.bgcolor is not None


def test_caret_is_upper_half_with_shadow_bg():
    out = _append_glyph_inline("^")
    assert out.plain == "▀"
    style = _parse_style(out)
    assert style.color is not None and style.bgcolor is not None


def test_tilde_and_comma_use_shadow_fg():
    for mark in ("~", ","):
        out = _append_glyph_inline(mark)
        style = _parse_style(out)
        assert style.color is not None
        assert style.bgcolor is None


def _parse_style(text):
    from rich.style import Style

    return Style.parse(str(text.spans[0].style))


def test_render_logo_four_lines():
    text = render_logo(DARK)
    width = len(LOGO_LEFT[0]) + len(LOGO_RIGHT[0])
    lines = text.plain.rstrip("\n").split("\n")
    assert len(lines) == 4
    assert all(len(line) == width for line in lines)


def test_render_logo_first_row_resolves_shadow_marks():
    text = render_logo(DARK)
    first = text.plain.rstrip("\n").split("\n")[0]
    expected = LOGO_LEFT[0] + LOGO_RIGHT[0]
    assert first == expected