"""Wave 2+3: corrupt-state quarantine, capability TTL, retriever/session caches."""
from __future__ import annotations

import json
import types

import pytest


def _nb(tmp_path):
    return types.SimpleNamespace(directory=tmp_path)


def test_corrupt_transcript_quarantined_not_wiped(tmp_path):
    from opennote.transcript import load_transcript

    (tmp_path / "transcript.json").write_text("{not json", encoding="utf-8")
    assert load_transcript(_nb(tmp_path)) == []
    backups = list(tmp_path.glob("transcript.corrupt.*.json"))
    assert len(backups) == 1
    assert "not json" in backups[0].read_text(encoding="utf-8")


def test_missing_transcript_no_backup(tmp_path):
    from opennote.transcript import load_transcript

    assert load_transcript(_nb(tmp_path)) == []
    assert list(tmp_path.glob("transcript.corrupt.*")) == []


def test_corrupt_usage_quarantined(tmp_path):
    from opennote.context_meter import load_spent

    (tmp_path / "usage.json").write_text("garbage{{{", encoding="utf-8")
    assert load_spent(tmp_path) == 0.0
    backups = list(tmp_path.glob("usage.corrupt.*.json"))
    assert len(backups) == 1


def test_record_spent_write_failure_returns_persisted(tmp_path, monkeypatch):
    import pathlib

    from opennote.context_meter import record_spent

    def boom(self, *a, **kw):
        raise OSError("disk down")

    monkeypatch.setattr(pathlib.Path, "write_text", boom)
    assert record_spent(1.5, tmp_path) == 0.0


def test_capabilities_ttl_caches_probe(monkeypatch):
    import opennote.capabilities as caps_mod

    calls = []

    def counting():
        calls.append(1)
        from opennote.capabilities import FakeCapability

        return FakeCapability(web_search=True)

    monkeypatch.setattr(caps_mod, "_probe", counting)
    caps_mod.clear_cached()
    try:
        assert caps_mod.get_capabilities().web_search is True
        assert caps_mod.get_capabilities().web_search is True
        assert len(calls) == 1
        # Age the cache past TTL: next call re-probes.
        caps_mod._cached_at -= (caps_mod._CACHE_TTL_SECONDS + 1)
        assert caps_mod.get_capabilities().web_search is True
        assert len(calls) == 2
    finally:
        caps_mod.clear_cached()


def test_capabilities_set_cached_still_honored(monkeypatch):
    import opennote.capabilities as caps_mod

    from opennote.capabilities import FakeCapability

    try:
        caps_mod.set_cached(FakeCapability(web_search="stub"))
        assert caps_mod.get_capabilities().web_search == "stub"
    finally:
        caps_mod.clear_cached()
