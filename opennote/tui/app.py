"""OpenNote TUI application entry point.

``opennote`` with no subcommand launches this app (see ``opennote.cli``).
"""
from __future__ import annotations

from typing import Optional

from textual.app import App

from opennote.tui.screens.chat import ChatScreen
from opennote.tui.theme import DEFAULT, Palette

CSS = """
Screen {
    background: $background;
}

#transcript {
    width: 100%;
    height: 1fr;
    padding: 1 2;
    scrollbar-gutter: stable;
}

#prompt-bar {
    width: 100%;
    height: auto;
    padding: 0 2 0 2;
}

#prompt-box {
    border-left: round $primary;
    height: auto;
    padding: 1 2;
    background: $background-element;
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
}

#meta-sep {
    color: $text-muted;
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

#command-popup {
    display: none;
    height: auto;
    max-height: 10;
    border: round $border-subtle;
    background: $background-panel;
    padding: 0 1;
}

#dialog {
    width: 60;
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
    margin-bottom: 1;
}

#dialog-body {
    height: auto;
    max-height: 40;
}

#dialog-list {
    height: 1fr;
    min-height: 3;
}

#dialog-hint {
    color: $text-muted;
    margin-top: 1;
}

Screen {
    background: $background;
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
        notebook_name: str = "default",
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


def main(notebook: str = "default", light: bool = False) -> None:
    """Launch the TUI (console-script entry point for bare ``opennote``)."""
    from opennote.tui.theme import LIGHT

    palette = LIGHT if light else DEFAULT
    OpenNoteApp(notebook_name=notebook, palette=palette).run()


if __name__ == "__main__":
    main()