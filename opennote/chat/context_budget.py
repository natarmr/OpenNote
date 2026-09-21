"""Token-budgeted context fitting (upstream context_builder parity, chars-based).

Upstream reserves ~20% of the budget for insights and emits an explicit
truncation notice; items that cannot fit even headers + notice + 1 char are
omitted with status ``omitted_budget`` rather than notice-only text.

Local adaptation: no tiktoken (E12 decision) — budgets are in *chars*
(callers convert ~4 chars/token). Estimation uses len(); the notice is
rendered *inside* the <source> block so citation validation still sees
real text.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

from opennote.chat.prompt import escape_source_content
from opennote.retrieval.retriever import SearchResult

DEFAULT_BUDGET_CHARS = 12_000
HEADER_OVERHEAD = 80  # <source> tags + citation line approx


@dataclass
class FittedItem:
    result: SearchResult
    content: str  # possibly truncated slice
    status: str  # "full" | "truncated" | "omitted_budget"
    notice: str = ""


def _block_len(result: SearchResult, content: str) -> int:
    pages = (
        result.metadata.get("pages")
        or result.metadata.get("page")
        or result.metadata.get("page_start")
        or ""
    )
    return len(f'<source id="N" page="{pages}">\n[N] {result.citation}\n{content}\n</source>')


def fit_tagged_context(
    results: Sequence[SearchResult],
    budget_chars: int = DEFAULT_BUDGET_CHARS,
) -> List[FittedItem]:
    """Fit results into budget_chars; truncate with notice, omit when hopeless."""
    fitted: List[FittedItem] = []
    used = 0
    for idx, r in enumerate(results, start=1):
        full = r.content.strip()
        if used + _block_len(r, full) <= budget_chars:
            used += _block_len(r, full)
            fitted.append(FittedItem(result=r, content=full, status="full"))
            continue
        remaining = budget_chars - used
        # Minimum viable: headers + notice + 1 content char.
        notice_template = "[truncated: showing first {k} of {m} chars]"
        min_need = HEADER_OVERHEAD + len(notice_template.format(k=0, m=len(full))) + 1
        if remaining < min_need:
            fitted.append(FittedItem(result=r, content="", status="omitted_budget"))
            continue
        # Binary-search largest prefix that fits (logarithmic, upstream-style).
        lo, hi = 1, len(full)
        best = 1
        while lo <= hi:
            mid = (lo + hi) // 2
            notice = notice_template.format(k=mid, m=len(full))
            candidate = full[:mid] + "\n" + notice
            if used + _block_len(r, candidate) <= budget_chars:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        notice = notice_template.format(k=best, m=len(full))
        content = full[:best] + "\n" + notice
        used += _block_len(r, content)
        fitted.append(FittedItem(result=r, content=content, status="truncated", notice=notice))
    return fitted


def build_fitted_tagged_context(fitted: Sequence[FittedItem]) -> str:
    """Render fitted items; omitted_budget items are skipped (never notice-only)."""
    parts = []
    for idx, item in enumerate([f for f in fitted if f.status != "omitted_budget"], start=1):
        r = item.result
        pages = (
            r.metadata.get("pages")
            or r.metadata.get("page")
            or r.metadata.get("page_start")
            or ""
        )
        content = escape_source_content(item.content.strip())
        parts.append(f'<source id="{idx}" page="{pages}">\n[{idx}] {r.citation}\n{content}\n</source>')
    return "\n\n".join(parts)


def fitted_results(fitted: Sequence[FittedItem]) -> List[SearchResult]:
    """Return the SearchResults that survived budgeting (for citation maps)."""
    return [f.result for f in fitted if f.status != "omitted_budget"]
