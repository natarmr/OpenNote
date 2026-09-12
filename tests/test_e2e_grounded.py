"""E2E: ingest → retrieve → ask (+ audio/video) regression.

Uses stub_embedder so no model download, and FakeClient so no API keys.
One test ingests the real kimi.pdf to prove the pipeline handles it;
synthetic txt fixtures give deterministic retrieval for ask-quality checks.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from opennote.audio.tts import explain_audio
from opennote.chat.ask import ask
from opennote.ingest.pipeline import ingest
from opennote.retrieval.citations import citation_for
from opennote.retrieval.retriever import SearchResult
from opennote.video import explain_video


# ── helpers ──────────────────────────────────────────────────────────────

class FakeRetriever:
    def __init__(self, results):
        self._results = results
        self.queries: list[str] = []

    def search(self, query, **kwargs):
        self.queries.append(query)
        return list(self._results)


class FakeClient:
    def __init__(self, replies: list[str] | str, provider_id="groq", model="fake"):
        if isinstance(replies, str):
            replies = [replies]
        self._replies = list(replies)
        self.provider_id = provider_id
        self.model = model
        self.calls: list[tuple[str, list, int]] = []
        self._idx = 0

    def complete(self, system, messages, max_tokens=1024):
        self.calls.append((system, messages, max_tokens))
        # cycle last reply if exhausted (lets planner fallback share client)
        if self._idx >= len(self._replies):
            return self._replies[-1]
        r = self._replies[self._idx]
        self._idx += 1
        return r


def _result(filename, content, pages="1"):
    meta = {"filename": filename, "chunk_id": filename, "pages": pages}
    return SearchResult(content=content, metadata=meta, similarity=0.9, citation=citation_for(meta))


FIXTURE_TEXT = (
    "OpenNote River Report — the Blue River flows through Cedar Valley. "
    "The Cedar Dam was completed in 1987. The valley's main crop is barley. "
    "A 2021 survey counted 42 heron nests along the north bank."
)


# ── ingest ───────────────────────────────────────────────────────────────

def test_e2e_ingest_kimi_pdf_real_file(stub_embedder, notebook_manager, tmp_path):
    """Real file smoke: kimi.pdf (first 2 pages sliced) must chunk and index."""
    src = Path("kimi.pdf")
    assert src.exists(), "kimi.pdf missing at repo root"
    # Slice to 2 pages so the test stays under 10s (full 47 pages → 150s)
    try:
        from pypdf import PdfReader, PdfWriter

        r = PdfReader(str(src))
        w = PdfWriter()
        for i in range(min(2, len(r.pages))):
            w.add_page(r.pages[i])
        sliced = tmp_path / "kimi_slice.pdf"
        with open(sliced, "wb") as f:
            w.write(f)
        src = sliced
    except Exception:
        pass  # fall back to full file if slicing fails

    nb = notebook_manager.create("kimi-e2e")
    count = ingest(nb, src, parser="fallback")
    assert count > 0, "kimi slice produced no chunks"
    assert len(nb.sources) == 1
    from opennote.store.vectors import VectorStoreManager

    mgr = VectorStoreManager("documents", nb.store_dir)
    assert mgr.manifest.data, "manifest empty after ingest"


def test_e2e_ingest_synthetic_txt_is_retrievable(stub_embedder, notebook_manager, tmp_path):
    """Deterministic txt ingest → Retriever returns the content."""
    from opennote.retrieval.retriever import Retriever

    f = tmp_path / "river.txt"
    f.write_text(FIXTURE_TEXT, encoding="utf-8")
    nb = notebook_manager.create("river")
    count = ingest(nb, f)
    assert count > 0
    # Even with stub (random) embeddings, search returns *some* chunk.
    retr = Retriever(nb, top_k=3)
    results = retr.search("Cedar Dam")
    assert len(results) > 0
    # At least one chunk should be from river.txt (the only source)
    assert any("river.txt" in str(r.citation) for r in results)


# ── ask ──────────────────────────────────────────────────────────────────

def test_e2e_ask_single_fact_grounded(stub_embedder, notebook_manager, tmp_path):
    """Single-fact ask wires context → system prompt → footer."""
    f = tmp_path / "river.txt"
    f.write_text(FIXTURE_TEXT, encoding="utf-8")
    nb = notebook_manager.create("river-ask")
    ingest(nb, f)

    results = [_result("river.txt", "The Cedar Dam was completed in 1987.", pages="1")]
    client = FakeClient("The Cedar Dam was completed in 1987 [1].")
    out = ask(nb, "When was the Cedar Dam completed?", client=client, retriever=FakeRetriever(results))
    assert "1987" in out.answer
    assert "[1]" in out.answer
    assert "Sources:" in out.answer
    assert "river.txt" in out.answer
    # prompt contained tagged source + ONLY guardrail
    system = client.calls[0][0]
    assert '<source id="1"' in system
    assert "ONLY" in system
    # notebook context injected (Phase 2)
    assert "river-ask" in system


def test_e2e_ask_multihop_merges_two_facts(stub_embedder, notebook_manager, tmp_path):
    """Multihop: planner → 2 workers → synthesizer, footer has both sources."""
    f = tmp_path / "river.txt"
    f.write_text(FIXTURE_TEXT, encoding="utf-8")
    nb = notebook_manager.create("river-mh")
    ingest(nb, f)

    class TermRetriever:
        def search(self, q, **kw):
            if "Dam" in q:
                return [_result("dam.pdf", "Cedar Dam completed in 1987.", pages="1")]
            if "heron" in q.lower() or "nests" in q.lower():
                return [_result("heron.pdf", "42 heron nests on north bank in 2021.", pages="2")]
            return [_result("other.pdf", FIXTURE_TEXT, pages="1")]

    replies = [
        '{"reasoning":"need dam date and heron count","searches":[{"term":"Cedar Dam","instructions":"when was Cedar Dam completed"},{"term":"heron nests","instructions":"how many heron nests were counted"}]}',
        "The Cedar Dam was completed in 1987 [1].",
        "The 2021 survey counted 42 heron nests [1].",
        "The Cedar Dam was completed in 1987 [1] and 42 heron nests were counted in 2021 [2].",
    ]
    client = FakeClient(replies)
    out = ask(nb, "When was the Cedar Dam completed and how many heron nests were counted?", client=client, retriever=TermRetriever(), multihop=True)
    assert "1987" in out.answer
    assert "42" in out.answer
    assert "[1]" in out.answer and "[2]" in out.answer
    assert "Sources:" in out.answer
    # validator must not have forced abstention
    assert "sources don't contain this" not in out.answer.lower()


def test_e2e_ask_abstains_without_citation(stub_embedder, notebook_manager, tmp_path):
    """Uncited / off-topic answer is gated to abstention."""
    f = tmp_path / "river.txt"
    f.write_text(FIXTURE_TEXT, encoding="utf-8")
    nb = notebook_manager.create("river-abstain")
    ingest(nb, f)

    results = [_result("river.txt", FIXTURE_TEXT)]
    client = FakeClient("The capital of Mars is Olympus.")  # no citation, no overlap
    out = ask(nb, "What is the capital of Mars?", client=client, retriever=FakeRetriever(results))
    assert out.answer.strip().lower() == "sources don't contain this"
    assert out.sources == []


def test_e2e_ask_multihop_falls_back_on_bad_planner(stub_embedder, notebook_manager, tmp_path):
    """Bad planner JSON → fallback to single-shot (no crash, valid footer)."""
    f = tmp_path / "river.txt"
    f.write_text(FIXTURE_TEXT, encoding="utf-8")
    nb = notebook_manager.create("river-fb")
    ingest(nb, f)

    results = [_result("river.txt", "Barley is the main crop.", pages="1")]

    class SingleRetr:
        def search(self, q, **kw):
            return list(results)

    client = FakeClient(["not json at all", "Barley is the main crop [1]."])
    out = ask(nb, "What is the main crop?", client=client, retriever=SingleRetr(), multihop=True)
    assert "barley" in out.answer.lower()
    assert "[1]" in out.answer


# ── audio / video ────────────────────────────────────────────────────────

def test_e2e_audio_degrades_to_transcript(tmp_path, monkeypatch):
    """No backend → .md transcript, success=False, no crash."""
    # Force all backends to fail
    import opennote.audio.tts as tts

    monkeypatch.setattr(tts, "_groq_tts", lambda s, p: tts.TtsResult(success=False, error="no groq", backend="groq"))
    monkeypatch.setattr(tts, "_openai_tts", lambda s, p: tts.TtsResult(success=False, error="no openai", backend="openai"))
    monkeypatch.setattr(tts, "_gemini_tts", lambda s, p: tts.TtsResult(success=False, error="no gemini", backend="gemini"))
    monkeypatch.setattr(tts, "_edge_tts", lambda s, p: tts.TtsResult(success=False, error="no edge", backend="edge-tts"))
    res = explain_audio("Hello Cedar Valley.", output_dir=tmp_path)
    assert res.success is False
    assert res.backend == "none"
    assert res.audio_path and res.audio_path.endswith(".md")
    assert Path(res.audio_path).exists()
    assert "No TTS backend succeeded" in (res.error or "")


def test_e2e_video_slides_always_render(tmp_path):
    """Stage 1 (png + md) always succeeds even without TTS/ffmpeg."""
    script = json.dumps(
        [
            {"title": "Cedar Dam", "bullets": ["Completed in 1987", "In Cedar Valley"], "narration": "The Cedar Dam was completed in 1987."},
            {"title": "Heron Nests", "bullets": ["42 nests", "North bank, 2021"], "narration": "42 heron nests were counted."},
        ]
    )
    res = explain_video(script, output_dir=tmp_path)
    assert res.success is True
    assert res.slides_dir and Path(res.slides_dir).exists()
    pngs = list(Path(res.slides_dir).glob("*.png"))
    assert len(pngs) == 2, f"expected 2 slides, got {pngs}"
    # Without TTS keys and without ffmpeg, video may be None — but slides must exist
    # (honest degradation; see test_explain_video_reports_ffmpeg_missing)
    assert res.script_path is None or Path(res.script_path).exists() if res.script_path else True


def test_e2e_video_rejects_bad_script(tmp_path):
    res = explain_video("not json", output_dir=tmp_path)
    assert res.success is False
    assert "Invalid JSON" in (res.error or "")


def test_e2e_audio_truncation_is_honest(tmp_path, monkeypatch):
    """Oversized script is truncated before TTS (no API 413)."""
    import opennote.audio.tts as tts

    seen: list[str] = []

    def fake_groq(s, p):
        seen.append(s)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"MP3")
        return tts.TtsResult(success=True, audio_path=str(p), backend="groq")

    monkeypatch.setattr(tts, "_groq_tts", fake_groq)
    monkeypatch.setattr(tts, "_openai_tts", lambda s, p: tts.TtsResult(success=False, error="x", backend="openai"))
    monkeypatch.setattr(tts, "_gemini_tts", lambda s, p: tts.TtsResult(success=False, error="x", backend="gemini"))
    monkeypatch.setattr(tts, "_edge_tts", lambda s, p: tts.TtsResult(success=False, error="x", backend="edge-tts"))

    from opennote.audio.tts import _MAX_TTS_CHARS

    res = tts.explain_audio("x" * (_MAX_TTS_CHARS + 500), output_dir=tmp_path)
    assert res.success is True
    assert len(seen[0]) <= _MAX_TTS_CHARS


def test_e2e_hybrid_finds_exact_term(stub_embedder, notebook_manager, tmp_path):
    """Hybrid BM25 finds a rare exact token that pure vectors (random stub) may miss."""
    from opennote.retrieval.retriever import Retriever

    rare = "StableLatentMoE-xyzzy-rare"
    f = tmp_path / "tech.txt"
    f.write_text(f"Standard filler. {rare} appears only here. More filler.", encoding="utf-8")
    (tmp_path / "other.txt").write_text("Completely unrelated filler about rivers and dams.", encoding="utf-8")
    nb = notebook_manager.create("hybrid-rare")
    ingest(nb, tmp_path)

    retr_hybrid = Retriever(nb, use_bm25=True, bm25_alpha=0.0)  # pure BM25
    hits = retr_hybrid.search(rare, top_k=5)
    assert any(rare in h.content for h in hits), "hybrid/BM25 should rank exact token first"

    # Adaptive default should be on (no explicit use_bm25 needed)
    retr_default = Retriever(nb)
    assert retr_default.use_bm25 is True
    assert retr_default.top_k in (5, 8, 12)  # adaptive thresholds


def test_e2e_adaptive_top_k_thresholds(stub_embedder, notebook_manager, tmp_path):
    """Adaptive top_k: <50→5, <300→8, else 12."""
    from opennote.retrieval.retriever import Retriever

    # Small corpus → 5
    nb_small = notebook_manager.create("adaptive-small")
    f = tmp_path / "small.txt"
    f.write_text("tiny doc", encoding="utf-8")
    ingest(nb_small, f)
    r_small = Retriever(nb_small)
    assert r_small.top_k == 5

    # Explicit override still honored
    r_explicit = Retriever(nb_small, top_k=3, use_bm25=False)
    assert r_explicit.top_k == 3
    assert r_explicit.use_bm25 is False
