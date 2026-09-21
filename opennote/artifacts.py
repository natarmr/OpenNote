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

    kind: str  # "markdown", "study_guide", "faq", "briefing", "timeline", "summary", "insight", "questions"
    title: str
    body: str  # markdown body (without frontmatter)
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    prompt_version: str = ""
    sources: List[dict] = field(default_factory=list)
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


# ---------------------------------------------------------------------------
# Frontmatter (upstream Transformation/SourceInsight parity, back-compat)
# ---------------------------------------------------------------------------

PROMPT_VERSIONS = {
    "markdown": "mindmap-v1",
    "study_guide": "study-v1",
    "faq": "faq-v1",
    "briefing": "briefing-v1",
    "timeline": "timeline-v1",
    "summary": "summary-v1",
    "questions": "questions-v1",
    "insight": "insight-v1",
}


def _frontmatter(artifact: Artifact) -> str:
    """YAML frontmatter: kind/title/created/prompt_version/sources (API-ready)."""
    import json as _json

    lines = ["---"]
    lines.append(f'kind: "{artifact.kind}"')
    safe_title = artifact.title.replace('"', "'")
    lines.append(f'title: "{safe_title}"')
    lines.append(f'created: "{artifact.created}"')
    if artifact.prompt_version:
        lines.append(f'prompt_version: "{artifact.prompt_version}"')
    if artifact.sources:
        lines.append("sources: " + _json.dumps(artifact.sources))
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def strip_frontmatter(text: str) -> str:
    """Remove a leading --- ... --- block (back-compat read path)."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            close = text.find("\n", end + 4)
            if close != -1:
                return text[close + 1 :].lstrip("\n")
    return text


def load_artifact(path: Path) -> Artifact:
    """Read an artifact file, stripping frontmatter into fields when present."""
    import json as _json
    import re as _re

    raw = Path(path).read_text(encoding="utf-8")
    kind, title, created, pver, sources = "markdown", Path(path).stem, "", "", []
    body = raw
    if raw.startswith("---"):
        m = _re.match(r"^---\n(.*?)\n---\n?", raw, flags=_re.DOTALL)
        if m:
            fm = m.group(1)
            body = raw[m.end() :].lstrip("\n")
            for line in fm.splitlines():
                if ":" not in line:
                    continue
                k, _, v = line.partition(":")
                k, v = k.strip(), v.strip().strip('"')
                if k == "kind":
                    kind = v
                elif k == "title":
                    title = v
                elif k == "created":
                    created = v
                elif k == "prompt_version":
                    pver = v
                elif k == "sources":
                    try:
                        sources = _json.loads(v)
                    except Exception:
                        sources = []
    art = Artifact(kind=kind, title=title, body=body)
    if created:
        art.created = created
    art.prompt_version = pver
    art.sources = sources if isinstance(sources, list) else []
    art.path = Path(path)
    art.filename = Path(path).name
    return art


def export_artifact_json(artifact: Artifact) -> dict:
    """JSON export compatible with upstream insight-style APIs."""
    return {
        "kind": artifact.kind,
        "title": artifact.title,
        "created": artifact.created,
        "prompt_version": artifact.prompt_version or PROMPT_VERSIONS.get(artifact.kind, ""),
        "sources": list(artifact.sources or []),
        "body": artifact.body,
    }


def save_artifact(
    kind: str,
    title: str,
    body: str,
    notebook_dir: Path,
    prompt_version: str = "",
    sources: List[dict] | None = None,
) -> Artifact:
    """Save an artifact and return the :class:`Artifact` instance.

    Parameters
    ----------
    kind: one of "markdown", "study_guide", "faq", "briefing", "timeline", "summary", "insight", "questions"
    title: display title for the artifact
    body: markdown body content
    notebook_dir: path to the notebook folder (``./.opennote/notebooks/<name>`` or ``$OPENNOTE_HOME/notebooks``)
    prompt_version: template version stamp (defaults from PROMPT_VERSIONS)
    sources: [{index, citation}] list for traceability (upstream insight parity)
    """
    artifact = Artifact(
        kind=kind,
        title=title,
        body=body,
        prompt_version=prompt_version or PROMPT_VERSIONS.get(kind, ""),
        sources=list(sources or []),
    )
    ad = _artifacts_dir(notebook_dir)
    final_path = ad / artifact.filename
    _atomic_write(_frontmatter(artifact) + artifact.body, final_path)
    artifact.path = final_path
    return artifact


# ---------------------------------------------------------------------------
# Mind-map generator (markdown outline)
# ---------------------------------------------------------------------------

MAX_MINDMAP_DEPTH = 4


# ---------------------------------------------------------------------------
# Mind-map tree model: parse markdown bodies into a renderable tree
# (powers the in-terminal mindmap viewer: inline Rich Tree + popup
# Textual Tree + `opennote artifacts show --tree`). ASCII-only output
# per the TUI chrome contract.
# ---------------------------------------------------------------------------

MAX_MINDMAP_LABEL = 100


@dataclass
class MindNode:
    """A single mind-map node (label + ordered children)."""

    label: str
    children: List["MindNode"] = field(default_factory=list)


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_BULLET_RE = re.compile(r"^([ \t]*)[-*+]\s+(.*?)\s*$")
_NUMBERED_RE = re.compile(r"^([ \t]*)\d+[.)]\s+(.*?)\s*$")
_MD_FMT_RE = re.compile(r"(\*\*|__|\*|_|`|~~)")


def _clean_label(text: str) -> str:
    """Strip markdown inline formatting + trailing colons, truncate."""
    label = _MD_FMT_RE.sub("", text).strip().rstrip(":").strip()
    label = re.sub(r"\s+", " ", label)
    if len(label) > MAX_MINDMAP_LABEL:
        label = label[: MAX_MINDMAP_LABEL - 1].rstrip() + "…"
    return label


def parse_mindmap(body: str, title: str = "", max_depth: int = MAX_MINDMAP_DEPTH) -> MindNode:
    """Parse a markdown mind-map *body* into a :class:`MindNode` tree.

    Handles headings (``#``-``####``), bullets (``-``/``*``), numbered
    lists (``1.``/``1)``), and plain paragraphs (attached as nodes or
    continuations). Depth is capped at *max_depth*; deeper items fold
    into their parent. Never raises on weird input — worst case returns
    a single root node.
    """
    root_label = _clean_label(title) or "Mind map"
    try:
        text = strip_frontmatter(body or "")
    except Exception:
        text = body or ""
    root = MindNode(label=root_label)
    # (depth, node) stack; root sits at depth -1 so H1 lands at 0.
    stack: List[tuple] = [(-1, root)]
    seen_h1 = False

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        m = _HEADING_RE.match(line.lstrip())
        if m:
            depth = len(m.group(1)) - 1
            label = _clean_label(m.group(2))
            # First H1 becomes the root title instead of a child.
            if depth == 0 and not seen_h1 and not root.children:
                seen_h1 = True
                if label:
                    root.label = label
                continue
            seen_h1 = seen_h1 or depth == 0
        else:
            m2 = _BULLET_RE.match(line) or _NUMBERED_RE.match(line)
            if m2:
                indent = m2.group(1).replace("\t", "  ")
                depth = 1 + len(indent) // 2
                label = _clean_label(m2.group(2))
            else:
                # Plain paragraph: continuation of the last node when the
                # previous line was also plain text, else a new leaf.
                label = _clean_label(line.strip())
                prev_depth, prev_node = stack[-1]
                if prev_node is not root and getattr(prev_node, "_plain", False):
                    prev_node.label = _clean_label(prev_node.label + " " + label)
                    continue
                depth = min(prev_depth + 1, max_depth) if prev_node is not root else 1
        if not label:
            continue
        depth = max(0, min(depth, max_depth))
        while len(stack) > 1 and stack[-1][0] >= depth:
            stack.pop()
        node = MindNode(label=label)
        node._plain = m is None and (m2 is None)  # type: ignore[attr-defined]
        stack[-1][1].children.append(node)
        stack.append((depth, node))

    for _, node in stack:
        node.__dict__.pop("_plain", None)
    return root


def to_ascii_tree(node: MindNode) -> str:
    """Render a :class:`MindNode` tree as ASCII (``|--`` / ```--``)."""
    lines = [node.label]
    def _walk(children: List[MindNode], prefix: str) -> None:
        for i, child in enumerate(children):
            last = i == len(children) - 1
            branch, cont = ("`-- ", "    ") if last else ("|-- ", "|   ")
            lines.append(f"{prefix}{branch}{child.label}")
            _walk(child.children, prefix + cont)
    _walk(node.children, "")
    return "\n".join(lines)


def short_artifact_display(path: Path | str, notebook_dir: Path | str | None = None) -> str:
    """Short ``<notebook>/artifacts/<file>`` display for deep artifact paths."""
    p = Path(path)
    if notebook_dir is not None:
        return f"{Path(notebook_dir).name}/artifacts/{p.name}"
    parts = p.parts
    if "artifacts" in parts:
        i = parts.index("artifacts")
        nb = parts[i - 1] if i > 0 else ""
        return f"{nb}/artifacts/{p.name}" if nb else f"artifacts/{p.name}"
    return p.name


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


def _render_studio(name: str, fallback: str, context: dict, question: str = "") -> str:
    """Render opennote/prompt_templates/studio_*.jinja; fall back to legacy string."""
    norm = {k.lower(): v for k, v in context.items()}
    kwargs = {
        "context": norm.get("context", ""),
        "question": norm.get("question", question),
        "CONTEXT": norm.get("context", ""),
        "QUESTION": norm.get("question", question),
    }
    try:
        from opennote.chat.render import render as _render

        return _render(name, **kwargs)
    except Exception:
        return _render_template(fallback, context)


def generate_study_guide(question: str, context: str) -> str:
    return _render_studio("studio_study.jinja", STUDY_GUIDE_TEMPLATE, {"CONTEXT": context, "QUESTION": question}, question)


def generate_faq(context: str) -> str:
    return _render_studio("studio_faq.jinja", FAQ_TEMPLATE, {"CONTEXT": context})


def generate_briefing(question: str, context: str) -> str:
    return _render_studio("studio_briefing.jinja", BRIEFING_TEMPLATE, {"CONTEXT": context, "QUESTION": question}, question)


def generate_timeline(question: str, context: str) -> str:
    return _render_studio("studio_timeline.jinja", TIMELINE_TEMPLATE, {"CONTEXT": context, "QUESTION": question}, question)


def generate_source_summaries(context: str) -> str:
    return _render_studio("studio_summary.jinja", SOURCE_SUMMARY_TEMPLATE, {"CONTEXT": context})


def generate_suggested_questions(question: str, context: str) -> str:
    return _render_studio("studio_questions.jinja", SUGGESTED_QUESTIONS_TEMPLATE, {"CONTEXT": context, "QUESTION": question}, question)


def generate_insight(context: str, source_label: str = "") -> str:
    """Per-source insight body (upstream SourceInsight analog, markdown)."""
    header = f"## Insight: {source_label}\n\n" if source_label else ""
    return (
        header
        + _render_studio("studio_summary.jinja", SOURCE_SUMMARY_TEMPLATE, {"CONTEXT": context})
    )


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


def make_insight(
    context: str,
    notebook_dir: Path,
    title: str = "Insight",
    source_label: str = "",
    sources: List[dict] | None = None,
) -> Artifact:
    """Save a per-source insight (upstream SourceInsight analog)."""
    return save_artifact(
        kind="insight",
        title=title,
        body=generate_insight(context, source_label),
        notebook_dir=notebook_dir,
        sources=sources,
    )