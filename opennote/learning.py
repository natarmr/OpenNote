"""Local-first learning state for education features.

Everything here is notebook-scoped JSON under ``<notebook>/learning/``:

- ``learner.json``  — the learner model (mastered/confused concepts, goals)
- ``cards.json``    — active-recall flashcards with SM-2-lite scheduling
- ``assessments.json`` — past writing-assessment results

The agent reads/writes this state through tools (see ``agents/tools.py``).
It never leaves the machine and needs no API key, matching OpenNote's
privacy-first, BYOK design (no provider ever sees the learner profile unless
the model is explicitly shown it as tool output).
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

from opennote.fsutil import atomic_write_json, now_iso

_LEARNING_DIR = "learning"


# ---------------------------------------------------------------------------
# Paths / IO helpers
# ---------------------------------------------------------------------------

def _learning_dir(notebook_dir: Path) -> Path:
    d = Path(notebook_dir) / _LEARNING_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_json(path: Path, default: dict) -> dict:
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else dict(default)
    except (OSError, ValueError):
        return dict(default)


def _save_json(path: Path, obj: dict) -> None:
    atomic_write_json(path, obj)


# ---------------------------------------------------------------------------
# Learner model
# ---------------------------------------------------------------------------

@dataclass
class LearnerModel:
    goals: List[str] = field(default_factory=list)
    mastered: List[str] = field(default_factory=list)
    struggling: List[str] = field(default_factory=list)
    preferred_depth: str = "medium"  # shallow | medium | deep
    notes: str = ""
    updated: str = ""


def learner_path(notebook_dir: Path) -> Path:
    return _learning_dir(notebook_dir) / "learner.json"


def load_learner(notebook_dir: Path) -> LearnerModel:
    raw = _load_json(learner_path(notebook_dir), {})
    try:
        return LearnerModel(
            goals=list(raw.get("goals") or []),
            mastered=list(raw.get("mastered") or []),
            struggling=list(raw.get("struggling") or []),
            preferred_depth=str(raw.get("preferred_depth") or "medium"),
            notes=str(raw.get("notes") or ""),
            updated=str(raw.get("updated") or ""),
        )
    except Exception:
        return LearnerModel()


def save_learner(notebook_dir: Path, model: LearnerModel) -> LearnerModel:
    model.updated = now_iso()
    _save_json(learner_path(notebook_dir), asdict(model))
    return model


def update_learner(
    notebook_dir: Path,
    *,
    add_mastered: Optional[List[str]] = None,
    add_struggling: Optional[List[str]] = None,
    clear_struggling: Optional[List[str]] = None,
    add_goal: Optional[str] = None,
    preferred_depth: Optional[str] = None,
    note: Optional[str] = None,
) -> LearnerModel:
    """Apply a partial update to the learner model (append/dedupe semantics)."""
    m = load_learner(notebook_dir)
    for c in add_mastered or []:
        c = str(c).strip()
        if c and c not in m.mastered:
            m.mastered.append(c)
        if c in m.struggling:
            m.struggling.remove(c)
    for c in add_struggling or []:
        c = str(c).strip()
        if c and c not in m.struggling:
            m.struggling.append(c)
    for c in clear_struggling or []:
        if c in m.struggling:
            m.struggling.remove(c)
    if add_goal:
        g = str(add_goal).strip()
        if g and g not in m.goals:
            m.goals.append(g)
    if preferred_depth in ("shallow", "medium", "deep"):
        m.preferred_depth = preferred_depth
    if note:
        m.notes = (m.notes + "\n" + str(note).strip()).strip()
    return save_learner(notebook_dir, m)


def learner_context(notebook_dir: Path, max_chars: int = 1200) -> str:
    """Render the learner model as a compact block for a system prompt."""
    m = load_learner(notebook_dir)
    if not (m.goals or m.mastered or m.struggling or m.notes):
        return ""
    lines = ["<learner-model>"]
    if m.goals:
        lines.append("Goals: " + ", ".join(m.goals))
    if m.mastered:
        lines.append("Mastered: " + ", ".join(m.mastered))
    if m.struggling:
        lines.append("Struggling with: " + ", ".join(m.struggling))
    if m.preferred_depth != "medium":
        lines.append("Preferred depth: " + m.preferred_depth)
    if m.notes:
        lines.append("Notes: " + m.notes)
    lines.append("</learner-model>")
    out = "\n".join(lines)
    return out[:max_chars]


# ---------------------------------------------------------------------------
# Active-recall cards (SM-2-lite)
# ---------------------------------------------------------------------------

@dataclass
class Card:
    id: str
    question: str
    answer: str
    source_ids: List[str] = field(default_factory=list)
    ease: float = 2.5
    interval_days: int = 0
    repetitions: int = 0
    due_ts: float = 0.0
    created: str = ""
    last_review: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Card":
        return cls(
            id=str(d.get("id", "")),
            question=str(d.get("question", "")),
            answer=str(d.get("answer", "")),
            source_ids=[str(s) for s in (d.get("source_ids") or [])],
            ease=float(d.get("ease", 2.5)),
            interval_days=int(d.get("interval_days", 0)),
            repetitions=int(d.get("repetitions", 0)),
            due_ts=float(d.get("due_ts", 0.0)),
            created=str(d.get("created", "")),
            last_review=str(d.get("last_review", "")),
        )


def cards_path(notebook_dir: Path) -> Path:
    return _learning_dir(notebook_dir) / "cards.json"


def load_cards(notebook_dir: Path) -> List[Card]:
    raw = _load_json(cards_path(notebook_dir), {})
    items = raw.get("cards") or []
    out: List[Card] = []
    for d in items:
        if isinstance(d, dict):
            try:
                out.append(Card.from_dict(d))
            except Exception:
                continue
    return out


def save_cards(notebook_dir: Path, cards: List[Card]) -> None:
    _save_json(cards_path(notebook_dir), {"cards": [c.to_dict() for c in cards]})


def add_card(notebook_dir: Path, question: str, answer: str,
             source_ids: Optional[List[str]] = None,
             card_id: Optional[str] = None) -> Card:
    """Add a card (deduplicated by question text). Due immediately."""
    import hashlib

    question = str(question).strip()
    if not question:
        raise ValueError("card question must be non-empty")
    cards = load_cards(notebook_dir)
    for existing in cards:
        if existing.question.strip().lower() == question.lower():
            return existing  # idempotent
    cid = card_id or hashlib.sha1(question.encode("utf-8")).hexdigest()[:12]
    card = Card(
        id=cid,
        question=question,
        answer=str(answer).strip(),
        source_ids=[str(s) for s in (source_ids or [])],
        due_ts=time.time(),
        created=now_iso(),
    )
    cards.append(card)
    save_cards(notebook_dir, cards)
    return card


def remove_card(notebook_dir: Path, card_id: str) -> bool:
    cards = load_cards(notebook_dir)
    kept = [c for c in cards if c.id != card_id]
    if len(kept) == len(cards):
        return False
    save_cards(notebook_dir, kept)
    return True


def due_cards(notebook_dir: Path, limit: int = 5, now: Optional[float] = None) -> List[Card]:
    now = time.time() if now is None else now
    cards = [c for c in load_cards(notebook_dir) if c.due_ts <= now]
    cards.sort(key=lambda c: c.due_ts)
    return cards[:limit]


def review_card(notebook_dir: Path, card_id: str, quality: int) -> Optional[Card]:
    """Grade a review 0-5 (SM-2-lite) and reschedule the card.

    quality < 3 => a lapse (reset repetitions, keep card due soon).
    Returns the updated card, or None if the id was not found.
    Raises ValueError for an out-of-range quality.
    """
    if not isinstance(quality, int) or not 0 <= quality <= 5:
        raise ValueError("quality must be an integer 0-5")
    cards = load_cards(notebook_dir)
    target: Optional[Card] = None
    for c in cards:
        if c.id == card_id:
            target = c
            break
    if target is None:
        return None
    if quality < 3:
        target.repetitions = 0
        target.interval_days = 0
        target.ease = max(1.3, round(target.ease - 0.2, 2))
        target.due_ts = time.time() + 60  # re-show within a minute
    else:
        target.repetitions += 1
        if target.repetitions == 1:
            target.interval_days = 1
        elif target.repetitions == 2:
            target.interval_days = 6
        else:
            target.interval_days = max(1, round(target.interval_days * target.ease))
        target.ease = min(3.0, round(target.ease + 0.1 - (5 - quality) * 0.02, 2))
        target.due_ts = time.time() + target.interval_days * 86400
    target.last_review = now_iso()
    save_cards(notebook_dir, cards)
    return target


# ---------------------------------------------------------------------------
# Assessment history
# ---------------------------------------------------------------------------

def assessments_path(notebook_dir: Path) -> Path:
    return _learning_dir(notebook_dir) / "assessments.json"


def record_assessment(notebook_dir: Path, result: dict) -> None:
    raw = _load_json(assessments_path(notebook_dir), {})
    items = raw.get("assessments") or []
    items.append(result)
    _save_json(assessments_path(notebook_dir), {"assessments": items[-100:]})


def load_assessments(notebook_dir: Path) -> List[dict]:
    raw = _load_json(assessments_path(notebook_dir), {})
    items = raw.get("assessments") or []
    return [d for d in items if isinstance(d, dict)]
