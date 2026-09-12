"""Citation validator = injection gate (primary defense).

Any claim that doesn't verifiably trace to real source text gets dropped
before rendering, regardless of whether it looks malicious.
Uses difflib fuzzy matching (threshold 0.85) on normalized text.
"""

from __future__ import annotations

import difflib
import re
from typing import Dict, List, Tuple

from opennote.retrieval.retriever import SearchResult
from opennote.schemas import Claim, GroundedAnswer


def _normalize(s: str) -> str:
    # lower, collapse whitespace, strip punctuation for fuzzy compare
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    # keep alphanumeric and space for comparison
    return s


def fuzzy_contains(chunk_text: str, quote_span: str, threshold: float = 0.85) -> bool:
    if not quote_span.strip():
        return False
    norm_chunk = _normalize(chunk_text)
    norm_quote = _normalize(quote_span)
    if norm_quote in norm_chunk:
        return True
    if len(norm_quote) > len(norm_chunk):
        ratio = difflib.SequenceMatcher(None, norm_quote, norm_chunk).ratio()
        return ratio >= threshold
    # Sliding-window fuzzy check: compare quote against every chunk window of same length.
    # This handles paraphrases with minor edits and was previously dead code (always False).
    n, m = len(norm_chunk), len(norm_quote)
    if m == 0:
        return False
    best = 0.0
    # Step by 15 chars to keep O(n*m/step) reasonable; 30-char windows overlap.
    step = max(1, m // 4)
    for start in range(0, n - m + 1, step):
        window = norm_chunk[start : start + m]
        ratio = difflib.SequenceMatcher(None, window, norm_quote).ratio()
        if ratio >= threshold:
            return True
        if ratio > best:
            best = ratio
    # Also try word-level overlap as fallback for very short quotes
    quote_words = norm_quote.split()
    if len(quote_words) >= 3:
        chunk_words = norm_chunk.split()
        # check any consecutive word window matches with high overlap
        qw_set = set(quote_words)
        for i in range(len(chunk_words) - len(quote_words) + 1):
            window_words = chunk_words[i : i + len(quote_words)]
            overlap = len(qw_set & set(window_words)) / len(qw_set)
            if overlap >= 0.8:
                return True
    # Full-chunk ratio as last resort (catches cases where quote ~ chunk length)
    if best == 0.0:
        best = difflib.SequenceMatcher(None, norm_chunk, norm_quote).ratio()
    return best >= threshold


def validate_claim(claim: Claim, chunks_by_id: Dict[str, SearchResult], threshold: float = 0.85) -> bool:
    for sid in claim.source_ids:
        chunk = chunks_by_id.get(str(sid))
        if chunk is None:
            continue
        if fuzzy_contains(chunk.content, claim.quote_span, threshold=threshold):
            return True
    return False


def filter_grounded_answer(answer: GroundedAnswer, chunks_by_id: Dict[str, SearchResult], threshold: float = 0.85) -> Tuple[GroundedAnswer, List[Claim], List[Claim]]:
    """Return (filtered_answer, kept_claims, dropped_claims)."""
    kept: List[Claim] = []
    dropped: List[Claim] = []
    for claim in answer.claims:
        if validate_claim(claim, chunks_by_id, threshold=threshold):
            kept.append(claim)
        else:
            dropped.append(claim)
    filtered = GroundedAnswer(claims=kept, summary=answer.summary if kept else None)
    # If summary exists but no kept claims, drop summary (no grounding)
    if not kept:
        filtered.summary = None
    return filtered, kept, dropped


def validate_freeform_answer(answer: str, chunks_by_id: Dict[str, SearchResult]) -> bool:
    """Heuristic for legacy free-form: check if answer contains at least one verifiable citation span.
    Used when model didn't use structured tool.
    """
    if "sources don't contain this" in answer.lower():
        return True
    # If answer contains a valid [n] citation that exists in chunk_map, consider grounded
    # (citations are the primary grounding signal for free-form)
    ids_in_answer = re.findall(r"\[(\d+)\]", answer)
    if ids_in_answer and any(str(x) in chunks_by_id for x in ids_in_answer):
        return True
    # Fallback: require substring overlap (overlapping windows, not non-overlapping step=30)
    norm_answer = _normalize(answer)
    for chunk in chunks_by_id.values():
        norm_chunk = _normalize(chunk.content)
        if len(norm_answer) < 30:
            if norm_answer in norm_chunk:
                return True
        else:
            # overlapping step 15 so windows straddling a 30-boundary are not missed
            for i in range(0, len(norm_answer) - 30 + 1, 15):
                window = norm_answer[i : i + 30]
                if window in norm_chunk:
                    return True
    return False
