"""In-terminal mind-map viewer: parser, ASCII tree, short paths."""
from __future__ import annotations

from pathlib import Path

from opennote.artifacts import (
    MindNode,
    load_artifact,
    parse_mindmap,
    save_artifact,
    short_artifact_display,
    to_ascii_tree,
)

KIMI_BODY = """Based on the provided text snippets, here is the architectural information.

### Key Architectural Components Mentioned

1.  **Gated MLA (Multi-head Latent Attention)**
    *   Explicitly listed in section **2.1.2 Gated MLA**.
    *   MLA reduces KV cache memory.

2.  **MTP Layer (Multi-Token Prediction)**
    *   Snippet [8] mentions pre-training.

### Summary of Likely Architecture

*   **Attention:** **Gated MLA**.
*   **Prediction Heads:** **MTP** layers.
"""


def test_parse_llm_style_body():
    root = parse_mindmap(KIMI_BODY, title="kimi arch")
    assert isinstance(root, MindNode)
    labels = [c.label for c in root.children]
    assert any("Gated MLA" in label for label in labels)
    assert any("MTP Layer" in label for label in labels)
    # Numbered children nest under their section.
    gated = next(c for c in root.children if "Gated MLA" in c.label)
    assert any("2.1.2" in g.label for g in gated.children)


def test_parse_h1_becomes_root_title():
    root = parse_mindmap("# Topic\n## A\n## B\n", title="fallback")
    assert root.label == "Topic"
    assert [c.label for c in root.children] == ["A", "B"]


def test_parse_bullets_and_depth_cap():
    body = "- a\n  - b\n    - c\n      - d\n        - e\n"
    root = parse_mindmap(body, title="t", max_depth=2)
    assert root.children[0].label == "a"

    def _depth(n, d=0):
        return max([d] + [_depth(c, d + 1) for c in n.children])

    assert _depth(root) <= 3  # root + max_depth levels


def test_parse_never_crashes():
    assert parse_mindmap("", title="").label
    assert parse_mindmap("   \n\n", title="x").label == "x"
    assert parse_mindmap("# only", title="").label == "only"


def test_ascii_tree_uses_ascii_guides():
    root = parse_mindmap("# T\n## A\n- b\n", title="T")
    tree = to_ascii_tree(root)
    assert tree.splitlines()[0] == "T"
    assert "|-- " in tree or "`-- " in tree
    assert "├" not in tree and "└" not in tree


def test_short_display():
    p = Path("/x/.opennote/notebooks/kkkk/artifacts/artifact-a.md")
    assert short_artifact_display(p, Path("/x/.opennote/notebooks/kkkk")) == "kkkk/artifacts/artifact-a.md"
    assert short_artifact_display(p) == "kkkk/artifacts/artifact-a.md"


def test_saved_mindmap_roundtrip_and_view(tmp_path):
    art = save_artifact("mindmap", "Demo", "# Demo\n## Node1\n- leaf", tmp_path)
    loaded = load_artifact(art.path)
    assert loaded.kind == "mindmap"
    root = parse_mindmap(loaded.body, title=loaded.title)
    assert root.label == "Demo"
    assert "Node1" in to_ascii_tree(root)


def test_transcript_add_mindmap_smoke():
    from opennote.tui.widgets.transcript import Transcript

    t = Transcript()
    root = parse_mindmap("# T\n## A\n", title="T")
    t.add_mindmap("T", root)  # must not raise without a mounted app


def test_mindmap_dialog_builds():
    from opennote.tui.dialogs import MindmapDialog

    root = parse_mindmap("# T\n## A\n- b\n", title="T")
    dlg = MindmapDialog("T", root, "kkkk/artifacts/x.md")
    assert dlg._title == "T"
    assert dlg._short_path.startswith("kkkk/")


async def test_mindmap_dialog_mounts_with_nodes(tmp_path):
    from textual.widgets import Tree

    from opennote.notebooks import NotebookManager
    from opennote.tui.app import OpenNoteApp
    from opennote.tui.dialogs import MindmapDialog
    from opennote.tui.theme import DARK

    manager = NotebookManager(home=tmp_path)
    try:
        manager.get("t")
    except KeyError:
        manager.create("t")
    app = OpenNoteApp(notebook_name="t", palette=DARK, manager=manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        root = parse_mindmap("# T\n## A\n- b\n## C\n", title="T")
        app.push_screen(MindmapDialog("T", root, "t/artifacts/x.md"))
        await pilot.pause()
        tree = app.screen.query_one("#mindmap-tree", Tree)
        assert tree.root.children, "dialog tree should contain mind-map nodes"
        await pilot.press("escape")
        await pilot.pause()
