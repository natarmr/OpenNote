"""OpenNote TUI application entry point.

``opennote`` with no subcommand launches this app (see ``opennote.cli``).
"""
from __future__ import annotations

from typing import Optional

from textual.app import App

from opennote.tui.screens.chat import ChatScreen
from opennote.tui.theme import DEFAULT, Palette

CSS = """
ChatScreen {
    align: center middle;
}

#transcript {
    display: block;
    width: 100%;
    height: 1fr;
    padding: 1 2;
    color: $text;
    scrollbar-gutter: stable;
}

#prompt-bar {
    width: 100%;
    height: auto;
    padding: 0 2 0 2;
    align-horizontal: center;
}

Screen {
    background: $background;
}

.muted {
    color: $text-muted;
}

.hint {
    color: $text-muted;
    text-style: italic;
}

Footer {
    background: $background-panel;
    color: $text-muted;
    height: 1;
}

#prompt-box {
    border-left: round $primary;
    height: auto;
    padding: 1 2;
    background: $background-element;
    width: 100%;
    max-width: 100%;
}

#prompt-input {
    height: auto;
    min-height: 3;
    max-height: 12;
    border: none;
    background: $background-element;
    padding: 0 0 0 1;
}

#meta-row {
    height: 1;
    align-horizontal: left;
    width: 100%;
    max-width: 100%;
}

#meta-sep {
    color: $text-muted;
}

#meta-row Label {
    margin-right: 1;
}

#meta-model {
    color: $text;
}

#meta-provider {
    color: $text-muted;
}

#status-row {
    height: 1;
    align-horizontal: left;
    width: 100%;
    max-width: 100%;
}

#status-spinner {
    width: 2;
    display: none;
}

#status-text {
    color: $text-muted;
}

#status-hint {
    color: $text-muted;
    width: 1fr;
    text-align: right;
}

#main {
    height: 1fr;
}

#left {
    width: 1fr;
    height: 1fr;
}

#sidebar {
    width: 32;
    min-width: 32;
    height: 1fr;
    background: $background-panel;
    padding: 1 2;
}

#sidebar-body {
    height: 1fr;
}

#side-session-title {
    text-style: bold;
}

#side-session-sub {
    color: $text-muted;
}

#side-context-head {
    text-style: bold;
    margin-top: 1;
}

#side-services-head {
    text-style: bold;
    margin-top: 1;
}

#sidebar-footer {
    height: auto;
    dock: bottom;
}

#command-popup {
    display: none;
    height: auto;
    max-height: 10;
    min-width: 60;
    border: round $border-subtle;
    background: $background-panel;
    padding: 0 1;
}

#dialog {
    width: 80;
    max-width: 90%;
    height: auto;
    max-height: 80%;
    border: round $primary;
    background: $background-panel;
    padding: 1 2;
    align-horizontal: center;
    align-vertical: middle;
    margin: 1 2;
}

#dialog-title {
    text-style: bold;
    color: $primary;
    margin-bottom: 1;
}

#dialog-subtitle {
    height: auto;
    margin-bottom: 1;
}

#dialog-detail {
    height: auto;
    margin-bottom: 1;
}

#dialog-body {
    height: auto;
    max-height: 40;
}

.dialog.picker {
    width: 78;
    max-width: 95%;
}

.dialog.picker #dialog-search {
    height: 3;
    border: round $border-subtle;
    background: $background;
    margin-bottom: 1;
}

.dialog.picker #dialog-list {
    height: 1fr;
    min-height: 8;
    max-height: 18;
    border: round $border-subtle;
    background: $background;
}

.dialog.picker #item-list {
    background: $background;
}

.dialog.picker ListItem {
    padding: 0 1;
}

.dialog.picker ListItem.--highlight {
    background: $background-element;
    color: $primary;
}

.dialog.input-dialog {
    width: 68;
    max-width: 92%;
}

.dialog.input-dialog #dialog-input {
    height: 3;
    border: round $primary;
    background: $background;
    margin-top: 1;
}

#dialog-list {
    height: 1fr;
    min-height: 3;
}

#dialog-hint {
    color: $text-muted;
    margin-top: 1;
}

#palette-options {
    max-height: 16;
    border: none;
    margin-top: 1;
}

/* --- Transcript polish ------------------------------------------------- */
.role-user {
    color: $primary;
    text-style: bold;
}

.role-assistant {
    color: $secondary;
    text-style: bold;
}

.answer-header {
    color: $text-muted;
}

.chip-error {
    color: $error;
    text-style: bold;
}

.chip-info {
    color: $secondary;
}

.chip-success {
    color: $success;
}

.code-block {
    background: $background-panel;
    color: $text;
    padding: 0 1;
}

#permission-hint {
    color: $warning;
    margin-top: 1;
}
"""


class OpenNoteApp(App):
    """The opencode-style OpenNote terminal UI."""

    TITLE = "OpenNote"
    SUB_TITLE = "grounded, cited Q&A"
    CSS = CSS
    ENABLE_COMMAND_PALETTE = False

    def __init__(
        self,
        notebook_name: Optional[str] = None,
        palette: Optional[Palette] = None,
        manager=None,
        provider_id: Optional[str] = None,
        client=None,
        retriever=None,
        ingest_fn=None,
        **kwargs,
    ) -> None:
        self.notebook_name = notebook_name
        self.palette = palette or DEFAULT
        super().__init__(**kwargs)
        self.manager = manager
        self.provider_id = provider_id
        self.client = client
        self.retriever = retriever
        self.ingest_fn = ingest_fn

    def get_theme_variable_defaults(self) -> dict[str, str]:
        """Expose the palette as CSS variables (``$background-element``, ...).

        This runs before the theme is registered, so the variables are always
        defined regardless of which theme is active.
        """
        return self.palette.variables()

    def on_mount(self) -> None:
        self.register_theme(self.palette.to_textual())
        self.theme = self.palette.name
        self.push_screen(
            ChatScreen(
                notebook_name=self.notebook_name,
                palette=self.palette,
                manager=self.manager,
                provider_id=self.provider_id,
                client=self.client,
                retriever=self.retriever,
                ingest_fn=self.ingest_fn,
            )
        )


def main(notebook: Optional[str] = None, light: bool = False) -> None:
    """Launch the TUI (console-script entry point for bare ``opennote``)."""
    from opennote.config import get_config
    from opennote.tui.theme import LIGHT, by_name

    configured = get_config().theme
    if light:
        palette = LIGHT
    elif configured:
        palette = by_name(configured)
    else:
        palette = DEFAULT
    OpenNoteApp(notebook_name=notebook, palette=palette).run()


if __name__ == "__main__":
    main()
