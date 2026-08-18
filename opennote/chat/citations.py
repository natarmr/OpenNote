"""Post-process model output: validate [n] markers against retrieved sources.

Guarantees every cited source is a chunk that was actually retrieved — out-of-
range or invented markers are silently dropped rather than surfaced.
"""
from __future__ import annotations

import re
from typing import List, Sequence, Tuple

from opennote.retrieval.citations import Citation
from opennote.retrieval.retriever import SearchResult

# Bracketed markers: [2], 【3†L1-L3】, [^4], and bare parenthesised (2).
# A parenthesised marker must be a *bare* number closed immediately — "(2
# percentage points)" is prose and must not match (L22).
MARKER = re.compile(
    r"(?:\[\^(\d+)\]|\[(\d+)\]|【(\d+)(?=†)|\((\d+)\))",
)


def _marker_number(match: "re.Match[str]") -> int:
    return int(next(g for g in match.groups() if g is not None))


def used_sources(
    answer: str, results: Sequence[SearchResult]
) -> Tuple[str, List[Citation]]:
    """Return (sources footer, used citations) for the [n] markers in ``answer``."""
    by_index = {i: result.citation for i, result in enumerate(results, start=1)}
    entries: List[Tuple[int, Citation]] = []  # (index, citation) in citation order
    seen_citations: set = set()
    for match in MARKER.finditer(answer):
        index = _marker_number(match)
        citation = by_index.get(index)
        if citation is None:
            continue
        key = str(citation)
        if key in seen_citations:
            continue
        seen_citations.add(key)
        entries.append((index, citation))
    if not entries:
        return "", []
    lines = ["Sources:"]
    lines.extend(f"  [{i}] {c}" for i, c in entries)
    used = [c for _, c in entries]
    return "\n".join(lines), used