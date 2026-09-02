"""Artifacts engine for NotebookLM-style studio features.

Saves user- or model-generated content to ``notebook/artifacts/<slug>-<timestamp>.md``.

Conventions:
- Artifacts live under ``<notebook_dir>/artifacts/`` (created if absent).
- Filenames are slugified from the content topic + a short hash + ISO timestamp.
- Writes are atomic (tmp file + os.replace) per the project's durability contract.
- Each artifact stores: title, kind (markdown/study_guide/faq/briefing/timeline/summary),
  created timestamp, and the markdown body.
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from opennote.fsutil import atomic_write_bytes

# ---------------------------------------------------------------------------
# Artifact data model
# ---------------------------------------------------------------------------


@dataclass
class Artifact:
    """Represents a saved artifact."""

    kind: str  # "markdown", "study_guide", "faq", "briefing", "timeline", "summary"
    title: str
    body: str  # markdown body
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    # Derived: safe filename (no path separators, no reserved names)
    filename: str = field(init=False, default="")
    # Derived: full path on disk
    path: Path = field(init=False, default=Path(""))

    def __post_init__(self) -> None:
        # Slugify title: lowercase, replace non-alphanum with hyphens, collapse
        slug = re.sub(r"[^a-z0-9]+", "-", self.title.lower().strip()).strip("-")
        # Avoid reserved Windows names
        reserved = {
            "con", "prn", "aux", "nul",
            *[f"com{i}" for i in range(1, 10)],
            *[f"lpt{i}" for i in range(1, 10)],
        }
        if slug.lower() in reserved:
            slug = "artifact"
        # Timestamp + short content hash for uniqueness (L60: 1-second
        # granularity alone silently clobbers same-second writes).
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        body_hash = hashlib.sha1(self.body.encode("utf-8")).hexdigest()[:8]
        self.filename = f"artifact-{slug}-{ts}-{body_hash}.md"
        # Path relative to the notebook root; callers set the notebook dir
        self.path = Path(self.filename)


# ---------------------------------------------------------------------------
# Artifact storage
# ---------------------------------------------------------------------------

_ARTIFACTS_DIR_NAME = "artifacts"


def _artifacts_dir(notebook_dir: Path) -> Path:
    """Return the artifacts directory for *notebook_dir*."""
    d = notebook_dir / _ARTIFACTS_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _atomic_write(markdown_text: str, path: Path) -> None:
    """Write *markdown_text* to *path* atomically (tmp + os.replace)."""
    atomic_write_bytes(path, markdown_text.encode("utf-8"))


def save_artifact(
    kind: str,
    title: str,
    body: str,
    notebook_dir: Path,
) -> Artifact:
    """Save an artifact and return the :class:`Artifact` instance.

    Parameters
    ----------
    kind: one of "markdown", "study_guide", "faq", "briefing", "timeline", "summary"
    title: display title for the artifact
    body: markdown body content
    notebook_dir: path to the notebook folder (``~/.opennote/notebooks/<name>``)
    """
    artifact = Artifact(kind=kind, title=title, body=body)
    ad = _artifacts_dir(notebook_dir)
    final_path = ad / artifact.filename
    _atomic_write(artifact.body, final_path)
    artifact.path = final_path
    return artifact


# ---------------------------------------------------------------------------
# Mind-map generator (markdown outline)
# ---------------------------------------------------------------------------

MAX_MINDMAP_DEPTH = 4


def create_mindmap(topic: str, items: List[str], notebook_dir: Path) -> Artifact:
    """Create a mind‑map artifact as markdown.

    ``topic`` is the overall title. ``items`` are the primary nodes; they are
    distributed across heading levels up to ``MAX_MINDMAP_DEPTH``.

    Returns the :class:`Artifact` instance.
    """
    # Build hierarchical markdown: title as H1, items as H2‑H4
    lines: List[str] = [f"# {topic}"]
    for i, item in enumerate(items):
        d = min(MAX_MINDMAP_DEPTH, 2 + (i % 3))  # H2, H3, H4 cycling
        level = "#" * d
        lines.append(f"{level} {item}")
    body = "\n".join(lines)

    return save_artifact(
        kind="markdown",
        title=topic,
        body=body,
        notebook_dir=notebook_dir,
    )


# ---------------------------------------------------------------------------
# Studio generators (prompt templates over existing retrieval)
# ---------------------------------------------------------------------------

STUDY_GUIDE_TEMPLATE = """You are a study guide generator. Using the retrieved context below,
create a compact study guide for the user. Include:
- A one-sentence overview
- 3–5 key concepts with brief explanations
- 2–3 "Things to remember" bullet points
- A self-quiz with 2–3 short-answer questions (no answers — let the user attempt)

Context:
{{CONTEXT}}

User question: {{QUESTION}}

Guide:"""

FAQ_TEMPLATE = """You are an FAQ generator. Using the retrieved context below,
generate 3–5 frequently asked questions with concise answers based *only* on the
provided sources. Do not use outside knowledge.

Context:
{{CONTEXT}}

FAQ:"""

BRIEFING_TEMPLATE = """You are a briefing document writer. Using the retrieved context
below, write a structured briefing (250–400 words) that covers:
- The core topic and its importance
- 3–4 key findings with source citations
- A short conclusion with potential next steps

Context:
{{CONTEXT}}

Briefing:"""

TIMELINE_TEMPLATE = """You are a timeline generator. Using the retrieved context
below, create a chronological timeline of events, ideas, or developments. Include:
- A date or sequence number for each entry
- A one-sentence description
- Source citations ([n]) where applicable

Context:
{{CONTEXT}}

Timeline:"""

SOURCE_SUMMARY_TEMPLATE = """You are a source summary generator. Using the retrieved
context below, write a 2–3 paragraph summary of each source (identified by its
citation [n]). Highlight the main contribution and any limiting factors.

Context:
{{CONTEXT}}

Source summaries:"""

SUGGESTED_QUESTIONS_TEMPLATE = """Given the user question and the retrieved
context below, generate 3–4 natural-sounding follow-up questions the user might
ask next. These should be grounded in the sources, not generic. Do not answer
them — just list the questions.

Context:
{{CONTEXT}}

User question: {{QUESTION}}

Suggested questions:"""


def _render_template(template: str, context: dict) -> str:
    """Simple template renderer: replace {{KEY}} with context[KEY]."""
    result = template
    for key, value in context.items():
        result = result.replace("{{" + key + "}}", value or "")
    return result


def generate_study_guide(question: str, context: str) -> str:
    return _render_template(STUDY_GUIDE_TEMPLATE, {"CONTEXT": context, "QUESTION": question})


def generate_faq(context: str) -> str:
    return _render_template(FAQ_TEMPLATE, {"CONTEXT": context})


def generate_briefing(question: str, context: str) -> str:
    return _render_template(BRIEFING_TEMPLATE, {"CONTEXT": context, "QUESTION": question})


def generate_timeline(question: str, context: str) -> str:
    return _render_template(TIMELINE_TEMPLATE, {"CONTEXT": context, "QUESTION": question})


def generate_source_summaries(context: str) -> str:
    return _render_template(SOURCE_SUMMARY_TEMPLATE, {"CONTEXT": context})


def generate_suggested_questions(question: str, context: str) -> str:
    return _render_template(SUGGESTED_QUESTIONS_TEMPLATE, {"CONTEXT": context, "QUESTION": question})


# ---------------------------------------------------------------------------
# Convenience: generate + save in one step
# ---------------------------------------------------------------------------

def make_study_guide(question: str, context: str, notebook_dir: Path, title: str = "Study Guide") -> Artifact:
    return save_artifact(kind="study_guide", title=title, body=generate_study_guide(question, context), notebook_dir=notebook_dir)


def make_faq(context: str, notebook_dir: Path, title: str = "FAQ") -> Artifact:
    return save_artifact(kind="faq", title=title, body=generate_faq(context), notebook_dir=notebook_dir)


def make_briefing(question: str, context: str, notebook_dir: Path, title: str = "Briefing") -> Artifact:
    return save_artifact(kind="briefing", title=title, body=generate_briefing(question, context), notebook_dir=notebook_dir)


def make_timeline(question: str, context: str, notebook_dir: Path, title: str = "Timeline") -> Artifact:
    return save_artifact(kind="timeline", title=title, body=generate_timeline(question, context), notebook_dir=notebook_dir)


def make_source_summaries(context: str, notebook_dir: Path, title: str = "Source Summaries") -> Artifact:
    return save_artifact(kind="summary", title=title, body=generate_source_summaries(context), notebook_dir=notebook_dir)


def make_suggested_questions(question: str, context: str, notebook_dir: Path, title: str = "Suggested Questions") -> Artifact:
    return save_artifact(kind="questions", title=title, body=generate_suggested_questions(question, context), notebook_dir=notebook_dir)