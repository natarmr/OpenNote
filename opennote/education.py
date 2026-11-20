"""Education generators: active-recall quizzes and formative assessment.

These reuse the same grounded-generation building blocks as the studio:
framed ``<source>`` context (injection defense) and the two-tier citation
validator. Quiz output becomes SRS flashcards (``learning.add_card``);
assessment output is validated claim-by-claim and its history is persisted.

No provider call happens unless the caller supplies an LLM client — keeping
this module pure and testable.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List

from opennote.artifacts import frame_studio_context

logger = logging.getLogger("opennote.education")


def _extract_json(text: str):
    """Pull the first JSON array/object out of a model reply, tolerating fences."""
    text = (text or "").strip()
    # Strip common markdown fences including ```json ... ```
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # Pick the FIRST structural opener in document order (an object whose
    # first value is an array must still parse as the object, not the array).
    candidates = [(text.find("{"), "{", "}"), (text.find("["), "[", "]")]
    candidates = [(start, o, c) for start, o, c in candidates if start != -1]
    if not candidates:
        return None
    start, opener, closer = min(candidates, key=lambda t: t[0])
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except ValueError:
                    break
    return None


def _client_complete(client, system: str, question: str, max_tokens: int) -> str:
    return client.complete(
        system,
        [{"role": "user", "content": question}],
        max_tokens=max_tokens,
    )


# ---------------------------------------------------------------------------
# Quiz generation -> flashcards
# ---------------------------------------------------------------------------

def generate_quiz_cards(
    question: str,
    results: List[Any],
    notebook_dir: Path,
    client,
    count: int = 5,
    max_tokens: int = 1024,
) -> List[Dict[str, Any]]:
    """Generate grounded quiz questions and save them as SRS cards.

    Returns a list of card descriptors ``{id, question, answer, source_ids}``.
    Uses the framed context so a document cannot inject instructions.
    """
    from opennote.chat.render import render

    context = frame_studio_context(results)
    system = render("edu_quiz.jinja", context=context, question=question, count=max(1, min(count, 10)))
    raw = _client_complete(client, system, "Generate the quiz now.", max_tokens=max_tokens)
    data = _extract_json(raw)
    if not isinstance(data, list):
        logger.warning("Quiz generator did not return a JSON array")
        return []
    from opennote.learning import add_card

    out: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        q = str(item.get("question", "")).strip()
        a = str(item.get("answer", "")).strip()
        if not q or not a:
            continue
        sids = [str(s) for s in (item.get("source_ids") or [])]
        card = add_card(notebook_dir, question=q, answer=a, source_ids=sids)
        out.append({"id": card.id, "question": card.question, "answer": card.answer, "source_ids": sids})
    return out


# ---------------------------------------------------------------------------
# Formative writing assessment
# ---------------------------------------------------------------------------

def assess_writing(
    question: str,
    submission: str,
    results: List[Any],
    notebook_dir: Path,
    client,
    max_tokens: int = 1024,
) -> Dict[str, Any]:
    """Grade a learner's written answer against the sources.

    The model proposes claims + quote spans; the two-tier validator then keeps
    only verifiable claims. Feedback is returned with a grounded/unsupported
    split so the UI can show exactly what held up.
    """
    from opennote.chat.render import render
    from opennote.schemas import Claim, GroundedAnswer
    from opennote.validation.citation import filter_grounded_answer

    context = frame_studio_context(results)
    system = render(
        "edu_assess.jinja",
        context=context,
        question=question,
        submission=submission,
    )
    raw = _client_complete(client, system, "Grade the answer now.", max_tokens=max_tokens)
    data = _extract_json(raw)
    if not isinstance(data, dict):
        logger.warning("Assessment generator did not return a JSON object")
        data = {}

    chunk_map = {str(i + 1): r for i, r in enumerate(results)}
    claims: List[Claim] = []
    for c in data.get("claims") or []:
        if not isinstance(c, dict):
            continue
        claims.append(
            Claim(
                text=str(c.get("text", "")),
                source_ids=[str(s) for s in (c.get("source_ids") or [])],
                quote_span=str(c.get("quote_span", "")),
            )
        )
    answer = GroundedAnswer(claims=claims, summary=str(data.get("feedback", "")) or None)
    _, kept, dropped = filter_grounded_answer(answer, chunk_map)
    # Feedback is teaching prose, not a cited factual claim — preserve it as
    # authored rather than routing it through the claim-coverage summary gate.
    result = {
        "question": question,
        "submission": submission,
        "supported": [c.text for c in kept],
        "unsupported": [c.text for c in dropped],
        "feedback": str(data.get("feedback", "")).strip() or "No feedback could be produced.",
    }
    from opennote.learning import record_assessment

    record_assessment(notebook_dir, result)
    return result
