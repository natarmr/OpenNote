"""Theme palette tests: tint blending, variables, Textual Theme mapping."""
from textual.theme import Theme

from opennote.tui.theme import DARK, LIGHT, Palette, _tint


def test_tint_pure_background_is_unchanged():
    assert _tint("#0a0a0a", "#eeeeee", 0.0) == "#0a0a0a"


def test_tint_full_fg():
    assert _tint("#0a0a0a", "#eeeeee", 1.0) == "#eeeeee"


def test_tint_midpoint_blend():
    assert _tint("#000000", "#ffffff", 0.5) == "#808080"


def test_shadow_is_quarter_blend():
    p = Palette(
        name="t",
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
    assert p.shadow == _tint(p.background, p.text, 0.25)


def test_variables_cover_opencode_names():
    vars_ = DARK.variables()
    for key in (
        "primary",
        "secondary",
        "text-muted",
        "background-element",
        "background-panel",
        "border-active",
        "border-subtle",
    ):
        assert key in vars_ and vars_[key].startswith("#")


def test_to_textual_maps_fields():
    theme = DARK.to_textual()
    assert isinstance(theme, Theme)
    assert theme.name == "opencode"
    assert theme.dark is True
    assert theme.primary == DARK.primary
    assert theme.background == DARK.background
    assert theme.panel == DARK.background_panel
    assert theme.surface == DARK.background_element
    assert theme.variables["background-element"] == DARK.background_element


def test_light_palette_has_light_flag():
    assert LIGHT.dark is False
    assert LIGHT.name == "opencode-light"