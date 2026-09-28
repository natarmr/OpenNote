"""Source-block delimiting — the deterministic data/instruction separator.

Every retrieved chunk reaches the model inside a ``<source>`` block. That
delimiting is the *only* deterministic control separating document bytes from
instructions: ``security/scan.py`` self-documents as "telemetry, not a gate",
so if the delimiter can be closed early the rest of the chunk is read as
instructions for the remainder of the turn — while the model still holds
``read_page`` / ``web_search`` / ``memory_search``.

Two attacker-reachable slots exist, and both were unguarded:

* ``content`` — escaped, but by a case-sensitive exact-substring pair, so
  ``</SOURCE>``, ``</source `` and ``</source\\n>`` survived.
* ``citation`` — never escaped at all. ``retrieval/citations.py`` builds it from
  a document-authored DOCX/HTML heading, a Tavily ``title``/``url``, or a
  supermemory hit's metadata.

So this module escapes both, and all three renderers call it. The escape inserts
a backslash after ``<`` (``<\\/source>``), which is the spelling the original
implementation used and which existing tests pin exactly.
"""
from __future__ import annotations

import re
from collections.abc import Sequence

#: Compiled per tag on first use. ``<source>`` is the document block;
#: ``<worker-answer>`` frames one model stage's reply handed to the next
#: (see ``chat/ask.py``). Separate namespaces, so neither can be closed by the
#: other and a worker's reply cannot masquerade as a citable source.
_TAG_PATTERNS: dict = {}


def _tag_pattern(tag: str) -> re.Pattern:
    pat = _TAG_PATTERNS.get(tag)
    if pat is None:
        # Any spelling of the tag opener/closer: mixed case, and whitespace/NUL
        # between the ``<``, the ``/``, the name and the ``>``. ``\b`` keeps
        # ``<sources>`` out of scope. Runs *after* the literal replacement,
        # which it never re-matches because the inserted backslash breaks it.
        pat = re.compile(rf"<[\s\x00]*/?[\s\x00]*{re.escape(tag)}\b", re.IGNORECASE)
        _TAG_PATTERNS[tag] = pat
    return pat


def escape_delimited(text: str, tag: str) -> str:
    """Return *text* with any ``<tag>``/``</tag>`` spelling defused."""
    if not text:
        return text
    out = text.replace(f"</{tag}>", f"<\\/{tag}>")
    return _tag_pattern(tag).sub(lambda m: "<\\" + m.group(0)[1:], out)


def escape_source_content(text: str) -> str:
    """Return *text* with any ``<source``/``</source`` tag spelling defused.

    The literal ``</source>`` replacement is byte-for-byte the historical
    behaviour and runs first, so the expectations pinned in
    ``tests/security/test_injection_gate.py`` still hold. The pattern then
    catches every spelling the old exact-substring pair missed — ``</SOURCE>``,
    ``</source ``, ``</source\\n>``, ``< source>`` and the bare ``</source`` that
    a truncation cut can leave behind.
    """
    return escape_delimited(text, "source")


def _escape_attr(value: object) -> str:
    """Escape a value destined for a double-quoted tag attribute."""
    return str(value).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def render_delimited_block(tag: str, attrs: str, body: str) -> str:
    """Wrap *body* in ``<tag attrs>…</tag>``, defusing any tag inside it.

    The body is escaped before it is wrapped, so a block can never close itself
    or the block that follows it.
    """
    return f"<{tag} {attrs}>\n{escape_delimited(body, tag)}\n</{tag}>"


def render_worker_answers(answers: Sequence[str], tag: str = "worker-answer") -> str:
    """Frame each worker reply as data for the synthesiser.

    A worker is another model, not a source. Its reply is a *draft* that has to
    be reconciled against the sources, so it gets its own tag namespace: it
    cannot close a ``<source>`` block, and its ``[n]`` markers cannot be mistaken
    for citations into the retrieved chunks.

    Two escapes, not one. Defusing only the block's own tag would still let a
    worker emit ``</source>`` or ``<source id="1">`` and forge a source boundary
    or a citable-looking chunk inside the synthesiser's prompt.
    """
    parts = []
    for i, answer in enumerate(answers, start=1):
        body = escape_delimited(answer, "source")
        body = escape_delimited(body, tag)
        parts.append(f'<{tag} id="{i}">\n{body}\n</{tag}>')
    return "\n\n".join(parts)


def render_source_block(
    idx: int,
    citation: str,
    content: str,
    pages: object = "",
    *,
    max_chars: int | None = None,
    truncate_suffix: str = "\n[…truncated…]",
) -> str:
    """Render one ``<source>`` block for the model.

    Order matters: *content* is truncated first and escaped second, so exactly
    the bytes that reach the model are the bytes that were scanned. Escaping
    first would let a cut land inside an escape and re-expose a tag.
    """
    body = (content or "").strip()
    if max_chars is not None and len(body) > max_chars:
        body = body[:max_chars].rstrip() + truncate_suffix
    body = escape_source_content(body)
    return (
        f'<source id="{_escape_attr(idx)}" page="{_escape_attr(pages)}">\n'
        f"[{idx}] {escape_source_content(str(citation))}\n{body}\n</source>"
    )
