"""Tests for the education features (learner model, SRS cards, quiz, assessment)."""
import time

import pytest

from opennote import learning
from opennote.agents.defs import AgentRegistry


# ---------------------------------------------------------------------------
# Feature 1 & 6: education agents are discoverable
# ---------------------------------------------------------------------------

def test_education_agents_discovered():
    reg = AgentRegistry.discover()
    for name in ("socratic", "tutor", "curriculum"):
        agent = reg.get(name)
        assert agent is not None
        assert agent.mode == "primary"
        assert "untrusted DATA" in agent.prompt


# ---------------------------------------------------------------------------
# Feature 2: learner model
# ---------------------------------------------------------------------------

def test_learner_model_roundtrip(tmp_path):
    m = learning.LearnerModel(goals=["learn RAG"], preferred_depth="deep")
    learning.save_learner(tmp_path, m)
    loaded = learning.load_learner(tmp_path)
    assert loaded.goals == ["learn RAG"]
    assert loaded.preferred_depth == "deep"
    assert loaded.updated


def test_update_learner_dedupes_and_clears(tmp_path):
    learning.update_learner(tmp_path, add_struggling=["attention"], add_goal="pass exam")
    assert "attention" in learning.load_learner(tmp_path).struggling
    learning.update_learner(tmp_path, add_mastered=["attention"])
    m = learning.load_learner(tmp_path)
    assert "attention" in m.mastered
    assert "attention" not in m.struggling
    assert "pass exam" in m.goals


def test_learner_context_includes_struggling(tmp_path):
    learning.update_learner(tmp_path, add_struggling=["BM25"])
    ctx = learning.learner_context(tmp_path)
    assert "<learner-model>" in ctx
    assert "BM25" in ctx


# ---------------------------------------------------------------------------
# Feature 3: active recall / SRS
# ---------------------------------------------------------------------------

def test_add_card_dedupe_and_due(tmp_path):
    c1 = learning.add_card(tmp_path, "What is BM25?", "A ranking function", ["1"])
    c2 = learning.add_card(tmp_path, "what is bm25?", "A ranking function")
    assert c1.id == c2.id
    assert len(learning.load_cards(tmp_path)) == 1
    assert any(c.id == c1.id for c in learning.due_cards(tmp_path))


def test_review_card_lapse_and_success(tmp_path):
    c = learning.add_card(tmp_path, "Q", "A")
    lapse = learning.review_card(tmp_path, c.id, 1)
    assert lapse.repetitions == 0
    assert lapse.due_ts <= time.time() + 61
    good = learning.review_card(tmp_path, c.id, 5)
    assert good.repetitions == 1
    assert good.interval_days == 1
    assert good.due_ts > time.time()


def test_review_card_unknown_and_invalid(tmp_path):
    assert learning.review_card(tmp_path, "nope", 4) is None
    with pytest.raises(ValueError):
        learning.review_card(tmp_path, "x", 9)


def test_remove_card(tmp_path):
    c = learning.add_card(tmp_path, "Q", "A")
    assert learning.remove_card(tmp_path, c.id) is True
    assert learning.remove_card(tmp_path, c.id) is False


# ---------------------------------------------------------------------------
# Feature 5: assessment (validator reuse) + quiz parsing
# ---------------------------------------------------------------------------

def test_assessment_validates_claims(tmp_path):
    from opennote.education import assess_writing
    from opennote.retrieval.citations import citation_for
    from opennote.retrieval.retriever import SearchResult

    chunk = SearchResult(
        content="The tensile strength reached 340 MPa after heat treatment.",
        metadata={"filename": "a.pdf", "chunk_id": "c1"},
        similarity=0.9,
        citation=citation_for({"filename": "a.pdf", "chunk_id": "c1"}),
    )

    class FakeClient:
        def complete(self, system, messages, max_tokens=1024):
            return (
                '```json\n{"claims": ['
                '{"text": "The tensile strength reached 340 MPa after heat treatment.", '
                '"source_ids": ["1"], "quote_span": "tensile strength reached 340 MPa"},'
                '{"text": "The strength was 999 MPa.", "source_ids": ["1"], "quote_span": ""}'
                '], "feedback": "Mostly correct; check the second number."}\n```'
            )

    result = assess_writing("What strength?", "340 MPa", [chunk], tmp_path, FakeClient())
    assert len(result["supported"]) == 1
    assert len(result["unsupported"]) == 1
    assert "Mostly correct" in result["feedback"]
    # History persisted.
    assert len(learning.load_assessments(tmp_path)) == 1


def test_quiz_saves_cards(tmp_path):
    from opennote.education import generate_quiz_cards
    from opennote.retrieval.citations import citation_for
    from opennote.retrieval.retriever import SearchResult

    chunk = SearchResult(
        content="BM25 ranks documents by term frequency and inverse document frequency.",
        metadata={"filename": "a.pdf", "chunk_id": "c1"},
        similarity=0.9,
        citation=citation_for({"filename": "a.pdf", "chunk_id": "c1"}),
    )

    class FakeClient:
        def complete(self, system, messages, max_tokens=1024):
            return '[{"question": "What does BM25 use?", "answer": "TF-IDF [1]", "source_ids": ["1"]}]'

    cards = generate_quiz_cards("BM25", [chunk], tmp_path, FakeClient(), count=1)
    assert len(cards) == 1
    assert len(learning.load_cards(tmp_path)) == 1


def test_quiz_bad_output_returns_empty(tmp_path):
    from opennote.education import generate_quiz_cards

    class FakeClient:
        def complete(self, system, messages, max_tokens=1024):
            return "not json"

    assert generate_quiz_cards("x", [], tmp_path, FakeClient()) == []


# ---------------------------------------------------------------------------
# Feature 4: learner block injected into agent system prompt
# ---------------------------------------------------------------------------

def test_learner_block_in_system_prompt(tmp_path):
    from opennote.agents.loop import agent_turn
    from opennote.chat.client import ChatResponse, ToolCall
    from opennote.notebooks import Notebook

    learning.update_learner(tmp_path, add_struggling=["entropy"])

    class FakeClient:
        provider_id = "fake"
        model = "fake"
        last_usage = None
        seen_system = None

        def chat(self, messages, tools=None, system=None, max_tokens=1024):
            FakeClient.seen_system = system
            return ChatResponse(content="sources don't contain this", tool_calls=[])

    class FakeRetriever:
        def search(self, *a, **k):
            return []

        def sources(self):
            return []

    nb = Notebook(name="nb", directory=tmp_path)
    agent_turn(nb, "hi", client=FakeClient(), retriever=FakeRetriever(), max_rounds=1)
    assert FakeClient.seen_system is not None
    assert "<learner-model>" in FakeClient.seen_system
