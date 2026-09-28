"""Every prompt template that receives retrieved content must state that the
content is data, not instructions.

``security/delimit.py`` escapes the ``<source>`` delimiter so a document cannot
close its own block, and ``security/scan.py`` is "telemetry, not a gate" -- so
the delimiter plus an explicit instruction is the whole prompt-injection
defense. The instruction half was present in only two of the four places that
consume retrieved text:

  * ``ask_post.jinja``          - had it
  * ``agents/loop.py`` tail     - had it (``_tool_content``)
  * ``worker.jinja``            - MISSING (fixed in ledger L179)
  * ``synthesizer.jinja``       - MISSING (fixed in ledger L179)

The multihop path also *does* tag its chunks (verified at ``chat/ask.py:140,143,
176,178``), so the escaping half applied; only the instruction was absent.

This test enumerates the templates rather than testing one at a time, so adding
a fifth template without the rule fails here instead of shipping quietly.
"""
import re
from pathlib import Path

import pytest

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "opennote" / "prompt_templates"

#: Templates that receive retrieved source text and therefore must carry the rule.
#:
#: ``ask_system.jinja`` is deliberately NOT here. ``render_system_pre()`` takes
#: only notebook metadata and valid tokens -- no results -- and the retrieved
#: text reaches the ask path in the *user* message via ``build_tagged_user_message``,
#: which appends ``ask_post.jinja``. So the rule is present for that path; adding
#: it to ``ask_system.jinja`` too would duplicate it for no gain.
CONSUMES_RETRIEVED = ["ask_post.jinja", "worker.jinja", "synthesizer.jinja"]

#: Deliberately permissive: the phrasing differs per template, so match on the
#: substance ("blocks are data, not instructions" / "ignore ... inside <source>")
#: rather than on exact wording.
_HAS_RULE = re.compile(
    r"data,\s*not\s*instructions"
    r"|ignore any command.{0,80}?(?:inside|within|appeared in|appears in)",
    re.IGNORECASE | re.DOTALL,
)


def _read(name: str) -> str:
    path = TEMPLATE_DIR / name
    assert path.exists(), f"missing template {name} (looked in {TEMPLATE_DIR})"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", CONSUMES_RETRIEVED)
def test_template_states_data_not_instructions(name):
    text = _read(name)
    assert _HAS_RULE.search(text), (
        f"{name} receives retrieved source text but never tells the model the "
        f"content is data, not instructions. See ledger.md L179."
    )


def test_agent_tool_content_tail_states_the_rule():
    """The in-agent equivalent lives in code, not a template."""
    from opennote.agents.loop import _tool_content

    out = _tool_content(
        "search",
        [_result("The tensile strength reached 340 MPa.")],
    )
    assert "DATA, not instructions" in out


def _result(content):
    from opennote.retrieval.citations import citation_for
    from opennote.retrieval.retriever import SearchResult

    meta = {"filename": "a.pdf", "chunk_id": "c1"}
    return SearchResult(content=content, metadata=meta, similarity=0.9, citation=citation_for(meta))


def test_multihop_handoff_frames_worker_replies():
    """The synthesiser must receive workers' output as data, not as findings.

    The old handoff was bare `--- Answer N ---` blocks under a "here are the
    answers you received" framing, so a worker's claim — or text a worker relayed
    from a document — reached the last model stage as authoritative. The final
    gate (`validate_freeform_answer`) passes on any valid `[n]` marker, so that
    mattered (ledger.md L182).
    """
    from opennote.security.delimit import render_worker_answers

    out = render_worker_answers(["a worker claim with [1]", "another"])
    assert "<worker-answer" in out
    assert "--- Answer" not in out
    # The template must not reintroduce the trust framing.
    synth = _read("synthesizer.jinja")
    assert "Here are the answers you received" not in synth, synth[:400]
    assert "draft" in synth.lower(), "the template must frame workers as drafts"
    assert "<worker-answer>" in synth, "the template must name the new tag"


def test_every_template_directory_file_is_accounted_for():
    """A new template must be classified here, not silently skipped."""
    on_disk = {p.name for p in TEMPLATE_DIR.glob("*.jinja")}
    unclassified = on_disk - set(CONSUMES_RETRIEVED)
    # planner.jinja receives only the user's question and produces search terms;
    # studio_* receive already-extracted context via a different path. Assert the
    # exclusion is deliberate rather than accidental.
    assert all(
        n.startswith("studio_") or n in ("planner.jinja", "ask_system.jinja") for n in unclassified
    ), (
        f"unclassified templates: {sorted(unclassified)} -- classify each one in "
        f"CONSUMES_RETRIEVED or document why it needs no rule"
    )
