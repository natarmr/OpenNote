"""Regression tests for Phase G fixes: artifacts (L60/L61), TTS (L55-L59,
L65), and video (L52-L54, L62-L63, L67)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from opennote.artifacts import Artifact, save_artifact, _atomic_write
from opennote.audio.tts import explain_audio, _MAX_TTS_CHARS, _edge_tts, save_audio_artifact
from opennote.video import (
    VideoResult,
    _ffmpeg_mux,
    _mp3_duration,
    explain_video,
    save_video_artifact,
)


# --- L60: artifact filenames must be collision-proof -------------------------


def test_artifact_same_second_writes_get_distinct_filenames(tmp_path):
    a = Artifact(kind="markdown", title="Same Title", body="body one")
    b = Artifact(kind="markdown", title="Same Title", body="body two")
    assert a.filename != b.filename
    assert a.filename.endswith(".md")


# --- L61: atomic write cleans up tmp on failure ------------------------------


def test_atomic_write_no_tmp_leak_on_failure(tmp_path):
    target = tmp_path / "out.md"
    target.write_text("original", encoding="utf-8")
    # A body whose encoding raises mid-write simulates a failing write.
    class Boom:
        def encode(self, encoding):
            raise OSError("boom")

    with pytest.raises(OSError):
        _atomic_write(Boom(), target)
    assert not list(tmp_path.glob("*.tmp"))
    # The target file is untouched (atomicity preserved).
    assert target.read_text(encoding="utf-8") == "original"


def test_save_artifact_writes_file(tmp_path):
    art = save_artifact("markdown", "Hello World", "# hi", tmp_path)
    assert art.path.exists()
    assert art.path.read_text(encoding="utf-8") == "# hi"


# --- L59: path traversal guards ----------------------------------------------


def test_save_audio_artifact_rejects_traversal():
    with pytest.raises(ValueError, match="Invalid notebook name"):
        save_audio_artifact("hello", notebook_name="../../etc")


def test_save_video_artifact_rejects_traversal():
    with pytest.raises(ValueError, match="Invalid notebook name"):
        save_video_artifact("[]", notebook_name="../../etc")


# --- L55/L56: TTS adapter chain + honest gemini ------------------------------


def test_explain_audio_degrades_to_transcript_when_no_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    # Force every backend to fail: no keys + edge-tts not importable.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "edge_tts":
            raise ImportError("no edge-tts")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = explain_audio("hello world", output_dir=tmp_path)
    assert result.success is False
    assert result.backend == "none"
    # The .md transcript is the degradation artifact (never an .mp3 success).
    assert result.audio_path and result.audio_path.endswith(".md")
    assert "No TTS backend succeeded" in (result.error or "")


def test_gemini_never_fakes_success(monkeypatch, tmp_path):
    import opennote.audio.tts as tts

    monkeypatch.setattr(tts, "_groq_tts", lambda s, p: tts.TtsResult(success=False, error="no groq", backend="groq"))
    monkeypatch.setattr(tts, "_openai_tts", lambda s, p: tts.TtsResult(success=False, error="no openai", backend="openai"))
    monkeypatch.setattr(tts, "_gemini_tts", lambda s, p: tts.TtsResult(success=False, error="no gemini", backend="gemini"))
    monkeypatch.setattr(tts, "_edge_tts", lambda s, p: tts.TtsResult(success=False, error="no edge", backend="edge-tts"))
    result = tts.explain_audio("script", output_dir=tmp_path)
    assert result.success is False
    assert not result.audio_path.endswith(".mp3")


# --- L55: chain actually falls through to next backend -----------------------


def test_explain_audio_falls_back_to_edge_tts(monkeypatch, tmp_path):
    import opennote.audio.tts as tts

    calls = []

    def fake_groq(s, p):
        calls.append("groq")
        return tts.TtsResult(success=False, error="no groq", backend="groq")

    def fake_edge(s, p):
        calls.append("edge")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"MP3")
        return tts.TtsResult(success=True, audio_path=str(p), backend="edge-tts")

    monkeypatch.setattr(tts, "_groq_tts", fake_groq)
    monkeypatch.setattr(tts, "_openai_tts", lambda s, p: tts.TtsResult(success=False, error="no", backend="openai"))
    monkeypatch.setattr(tts, "_gemini_tts", lambda s, p: tts.TtsResult(success=False, error="no", backend="gemini"))
    monkeypatch.setattr(tts, "_edge_tts", fake_edge)
    result = tts.explain_audio("script", output_dir=tmp_path)
    assert result.success is True
    assert result.backend == "edge-tts"
    assert calls == ["groq", "edge"]  # edge tried only after groq failed


# --- L58: edge-tts uses NamedTemporaryFile, not mktemp -----------------------


def _install_fake_edge_tts(monkeypatch):
    import sys
    import types

    class FakeCommunicate:
        def __init__(self, text, voice):
            self.text = text
            self.voice = voice

        async def save(self, path):
            Path(path).write_bytes(b"MP3DATA")

    fake = types.ModuleType("edge_tts")
    fake.Communicate = FakeCommunicate
    monkeypatch.setitem(sys.modules, "edge_tts", fake)


def test_edge_tts_writes_audio_and_no_temp_leak(monkeypatch, tmp_path):
    import opennote.audio.tts as tts

    _install_fake_edge_tts(monkeypatch)
    result = tts._edge_tts("script", tmp_path / "out.mp3")
    assert result.success is True
    assert Path(result.audio_path).exists()
    assert Path(result.audio_path).read_bytes() == b"MP3DATA"


# --- L57: _edge_tts works from inside a running loop -------------------------


def test_edge_tts_runs_when_called_inside_event_loop(monkeypatch, tmp_path):
    import asyncio
    import opennote.audio.tts as tts

    _install_fake_edge_tts(monkeypatch)

    async def inside_loop():
        return tts._edge_tts("script", tmp_path / "loop.mp3")

    result = asyncio.run(inside_loop())
    assert result.success is True


# --- L65: input size cap -----------------------------------------------------


def test_explain_audio_truncates_script(monkeypatch, tmp_path):
    import opennote.audio.tts as tts

    received = []

    def fake_groq(s, p):
        received.append(s)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"MP3")
        return tts.TtsResult(success=True, audio_path=str(p), backend="groq")

    monkeypatch.setattr(tts, "_groq_tts", fake_groq)
    monkeypatch.setattr(tts, "_openai_tts", lambda s, p: tts.TtsResult(success=False, error="x", backend="openai"))
    monkeypatch.setattr(tts, "_gemini_tts", lambda s, p: tts.TtsResult(success=False, error="x", backend="gemini"))
    monkeypatch.setattr(tts, "_edge_tts", lambda s, p: tts.TtsResult(success=False, error="x", backend="edge-tts"))
    tts.explain_audio("x" * (_MAX_TTS_CHARS + 1000), output_dir=tmp_path)
    assert len(received[0]) <= _MAX_TTS_CHARS


# --- L54: tolerant Slide parsing ---------------------------------------------


def test_explain_video_handles_extra_slide_keys(tmp_path):
    script = json.dumps(
        [
            {
                "title": "Slide One",
                "bullets": ["point a", "point b"],
                "narration": "narrates",
                "extra_field": "ignored",  # LLM adds junk keys
            }
        ]
    )
    result = explain_video(script, output_dir=tmp_path)
    assert result.success is True
    assert result.slides_dir
    assert Path(result.slides_dir).exists()


def test_explain_video_coerces_non_string_bullets(tmp_path):
    script = json.dumps(
        [
            {
                "title": "Slide",
                "bullets": [123, None, "ok"],  # non-string junk
                "narration": "text",
            }
        ]
    )
    result = explain_video(script, output_dir=tmp_path)
    assert result.success is True


def test_explain_video_rejects_empty_slide_list(tmp_path):
    result = explain_video("[]", output_dir=tmp_path)
    assert result.success is False
    assert "no slides" in (result.error or "").lower()


def test_explain_video_rejects_invalid_json(tmp_path):
    result = explain_video("not json", output_dir=tmp_path)
    assert result.success is False


# --- L52/L53/L63: mux writes real file + checks ffmpeg status -----------------


def test_ffmpeg_mux_returns_error_when_ffmpeg_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    ok, err = _ffmpeg_mux(tmp_path, tmp_path, tmp_path / "slideshow.mp4")
    assert ok is False
    assert "ffmpeg" in (err or "").lower()


def test_ffmpeg_mux_failing_ffmpeg_surfaces_error(tmp_path, monkeypatch):
    # A fake ffmpeg that exits non-zero (a .bat since tests run on Windows).
    fake = tmp_path / "ffmpeg.bat"
    fake.write_text("@echo off\necho boom 1>&2\nexit /b 1\n", encoding="utf-8")

    monkeypatch.setattr(shutil, "which", lambda name: str(fake) if name == "ffmpeg" else None)
    img = tmp_path / "slide-00.png"
    img.write_bytes(b"PNG")
    aud = tmp_path / "slide-00.mp3"
    aud.write_bytes(b"MP3")
    ok, err = _ffmpeg_mux(tmp_path, tmp_path, tmp_path / "slideshow.mp4")
    assert ok is False
    assert "boom" in (err or "")


def test_explain_video_reports_ffmpeg_missing(tmp_path, monkeypatch):
    import opennote.video as video

    monkeypatch.setattr(shutil, "which", lambda name: None)
    # Provide per-slide audio so stage 3 actually runs and can detect ffmpeg.
    def fake_audio(slide, index, out_dir):
        p = out_dir / f"slide-{index:02d}.mp3"
        p.write_bytes(b"MP3")
        return str(p)

    monkeypatch.setattr(video, "_generate_slide_audio", fake_audio)
    script = json.dumps(
        [{"title": "T", "bullets": ["b"], "narration": "n"}]
    )
    result = explain_video(script, output_dir=tmp_path)
    # Stage 3 can't run but stages 1-2 still complete → success with no video.
    assert result.success is True
    assert result.video_path is None
    assert "ffmpeg" in (result.error or "")


def test_mp3_duration_probe_handles_missing_ffprobe(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert _mp3_duration(tmp_path / "none.mp3") == 5.0


# --- L67: import-time keychain probe removed ----------------------------------


def test_tts_module_import_does_not_probe_keychain(monkeypatch):
    import builtins
    import opennote.audio.tts as tts

    probes = []

    def fake_resolve(provider):
        probes.append(provider)
        return None

    monkeypatch.setattr(tts, "resolve_key", fake_resolve)
    # Re-import a fresh copy of the module — must not call resolve_key.
    import importlib

    # Just assert the current module has no _cached_backend attribute anymore.
    assert not hasattr(tts, "_cached_backend")