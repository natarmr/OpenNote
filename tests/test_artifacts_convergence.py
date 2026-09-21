"""Track 3: artifacts frontmatter + Jinja studio + insight + export."""
from __future__ import annotations

from opennote.artifacts import (
    PROMPT_VERSIONS,
    export_artifact_json,
    generate_briefing,
    generate_faq,
    generate_study_guide,
    generate_suggested_questions,
    generate_timeline,
    load_artifact,
    make_insight,
    save_artifact,
    strip_frontmatter,
)


def test_frontmatter_roundtrip(tmp_path):
    art = save_artifact(
        "briefing", "Test", "body text",
        tmp_path, sources=[{"index": 1, "citation": "[f, p.1]"}],
    )
    raw = art.path.read_text(encoding="utf-8")
    assert raw.startswith("---")
    assert "prompt_version" in raw
    loaded = load_artifact(art.path)
    assert loaded.body.strip() == "body text"
    assert loaded.kind == "briefing"
    assert loaded.prompt_version == PROMPT_VERSIONS["briefing"]
    assert loaded.sources[0]["index"] == 1
    assert strip_frontmatter(raw).strip() == "body text"


def test_studio_jinja_renders_all():
    ctx = "Context about retrieval. [1] src."
    assert "Context about retrieval" in generate_study_guide("q?", ctx)
    assert "Context about retrieval" in generate_faq(ctx)
    assert "Context about retrieval" in generate_briefing("q?", ctx)
    assert "Context about retrieval" in generate_timeline("q?", ctx)
    assert "3–4" in generate_suggested_questions("q?", ctx) or "follow-up" in generate_suggested_questions("q?", ctx).lower()
    # Empty context must not crash (StrictUndefined has all vars supplied).
    assert isinstance(generate_faq(""), str)


def test_make_insight_and_export(tmp_path):
    art = make_insight("insight ctx", tmp_path, title="I1", source_label="doc.pdf",
                       sources=[{"index": 1, "citation": "[doc.pdf, p.2]"}])
    assert art.kind == "insight"
    assert art.prompt_version == PROMPT_VERSIONS["insight"]
    exported = export_artifact_json(art)
    assert exported["kind"] == "insight"
    assert exported["sources"][0]["citation"] == "[doc.pdf, p.2]"
    assert "insight ctx" in exported["body"]
    # Persisted file exports identically.
    assert export_artifact_json(load_artifact(art.path))["kind"] == "insight"
