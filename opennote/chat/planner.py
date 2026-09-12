"""Query planner for multihop ask — plan → worker → synthesizer."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List

from opennote.chat.client import LLMClient


@dataclass
class PlannedQuery:
    term: str
    instructions: str


@dataclass
class Plan:
    reasoning: str
    searches: List[PlannedQuery]


_MAX_QUERIES = 3


def _extract_json(text: str) -> str:
    """Extract JSON object from possibly fenced text."""
    text = text.strip()
    # Strip ```json fences if present
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        return m.group(1)
    # Find first { ... last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def plan_queries(
    question: str,
    client: LLMClient,
    max_queries: int = _MAX_QUERIES,
    max_tokens: int = 512,
) -> Plan | None:
    """Ask the LLM to decompose ``question`` into sub-queries.

    Returns ``None`` on any failure (JSON parse error, empty searches, etc.)
    so the caller can fall back to single-shot retrieval.
    """
    from opennote.chat.render import render

    prompt = render("planner.jinja", question=question, max_queries=max_queries)
    try:
        raw = client.complete(
            "You are a helpful research planner. Output JSON only.",
            [{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
    except Exception:
        return None

    try:
        data = json.loads(_extract_json(raw))
    except Exception:
        return None

    reasoning = str(data.get("reasoning", "")).strip()
    searches_raw = data.get("searches")
    if not isinstance(searches_raw, list) or not searches_raw:
        return None

    searches: List[PlannedQuery] = []
    for item in searches_raw[:max_queries]:
        if not isinstance(item, dict):
            continue
        term = str(item.get("term", "")).strip()
        instructions = str(item.get("instructions", "")).strip()
        if term:
            searches.append(PlannedQuery(term=term, instructions=instructions or f"Answer about {term}"))

    if not searches:
        return None

    return Plan(reasoning=reasoning, searches=searches)
