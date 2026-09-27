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

#: Any spelling of a ``source`` tag opener/closer: mixed case, and
#: whitespace/NUL between the ``<``, the ``/``, the name and the ``>``. ``\\b``
#: keeps ``<sources>`` and ``<sourceful>`` out of scope so ordinary prose is not
#: mangled. Runs *after* the literal ``</source>`` replacement, which it never
#: re-matches because the inserted backslash breaks the pattern.
_VARIANT_OPEN = re.compile(r"<[\s\x00]*/?[\s\x00]*source\b", re.IGNORECASE)


def escape_source_content(text: str) -> str:
    """Return *text* with any ``<source``/``</source`` tag spelling defused.

    The literal ``</source>`` replacement is byte-for-byte the historical
    behaviour and runs first, so the expectations pinned in
    ``tests/security/test_injection_gate.py`` still hold. The pattern then
    catches every spelling the old exact-substring pair missed — ``</SOURCE>``,
    ``</source ``, ``</source\\n>``, ``< source>`` and the bare ``</source`` that
    a truncation cut can leave behind.
    """
    if not text:
        return text
    out = text.replace("</source>", "<\\/source>")
    return _VARIANT_OPEN.sub(lambda m: "<\\" + m.group(0)[1:], out)


def _escape_attr(value: object) -> str:
    """Escape a value destined for a double-quoted tag attribute."""
    return str(value).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


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
