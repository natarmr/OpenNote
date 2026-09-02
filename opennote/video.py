"""Video explanation (narrated slideshow).

Provides ``explain_video`` tool that the agent loop can call.

The model supplies a script + slide breakdown (JSON: each slide has title, bullets,
and narration text). The tool then:

1. Renders each slide as a Pillow image (text on colored background).
2. Generates per-slide TTS narration via the already-installed TTS backends.
3. Muxes audio + images into an MP4 using ``ffmpeg`` (system binary, already on PATH).

Degradable at every stage:
  - Stage 1: script + slides markdown (always succeeds).
  - Stage 2: + TTS narration → saves .mp3 per slide.
  - Stage 3: + ffmpeg mux → .mp4 in ``notebook/artifacts/``.
  - Stage 4: nothing available → script + slide images (.png) saved as artifact.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from opennote.audio.tts import explain_audio, TtsResult, _MAX_TTS_CHARS

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass
class Slide:
    """One slide in the narrated slideshow."""
    title: str
    bullets: List[str]  # short bullet points
    narration: str  # full TTS text for this slide

@dataclass
class VideoResult:
    """Result of ``explain_video``."""

    success: bool
    # Paths to saved assets
    script_path: Optional[str] = None  # .md with the full script
    slides_dir: Optional[str] = None  # dir with .png slide images
    video_path: Optional[str] = None  # .mp4 if mux succeeded
    # Per-slide TTS audio paths (may be None if TTS not available)
    audio_paths: Optional[List[str]] = None
    # Error info when success=False
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Slide rendering (Pillow)
# ---------------------------------------------------------------------------

SLIDE_WIDTH = 1280
SLIDE_HEIGHT = 720
SLIDE_PADDING = 40
SLIDE_BACKGROUND = "#2c3e50"  # dark blue-gray
SLIDE_TEXT = "#ecf0f1"  # off-white
SLIDE_TITLE_FONT_SIZE = 44
SLIDE_BULLET_FONT_SIZE = 32
SLIDE_MARGIN_TOP = 60

# Try to load a truetype font; fall back to the default PIL font.
try:
    TITLE_FONT = ImageFont.truetype("arial.ttf", SLIDE_TITLE_FONT_SIZE)
    BULLET_FONT = ImageFont.truetype("arial.ttf", SLIDE_BULLET_FONT_SIZE)
except OSError:
    TITLE_FONT = ImageFont.load_default()
    BULLET_FONT = ImageFont.load_default()


def _make_slide_image(slide: Slide, index: int) -> Image.Image:
    """Render a single slide as a Pillow ``Image``."""
    img = Image.new("RGB", (SLIDE_WIDTH, SLIDE_HEIGHT), SLIDE_BACKGROUND)
    draw = ImageDraw.Draw(img)

    # Title
    title_x = SLIDE_PADDING
    title_y = SLIDE_PADDING
    draw.text((title_x, title_y), slide.title, font=TITLE_FONT, fill=SLIDE_TEXT)

    # Bullets
    bullet_y = SLIDE_MARGIN_TOP + SLIDE_TITLE_FONT_SIZE + 20
    for bullet in slide.bullets:
        # Wrap text if too wide
        lines = _wrap_text(draw, bullet, SLIDE_WIDTH - 2 * SLIDE_PADDING, BULLET_FONT)
        for line in lines:
            draw.text((SLIDE_PADDING, bullet_y), line, font=BULLET_FONT, fill=SLIDE_TEXT)
            bullet_y += BULLET_FONT.size + 8

    # Slide number
    num_text = f"Slide {index + 1}"
    draw.text((SLIDE_WIDTH - SLIDE_PADDING, SLIDE_HEIGHT - SLIDE_PADDING),
              num_text, font=BULLET_FONT, fill=SLIDE_TEXT, anchor="rd")

    return img


def _wrap_text(draw: ImageDraw.Draw, text: str, max_width: int,
               font: ImageFont.FreeTypeFont) -> List[str]:
    """Split *text* into lines that fit within *max_width*."""
    words = text.split()
    lines = []
    current = []
    for word in words:
        test_line = " ".join(current + [word])
        w, _ = draw.textbbox((0, 0), test_line, font=font)[2:]
        if w <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


# ---------------------------------------------------------------------------
# TTS per-slide (reuses the TTS adapter chain from Phase D)
# ---------------------------------------------------------------------------

def _generate_slide_audio(slide: Slide, index: int, output_dir: Path) -> Optional[str]:
    """Generate TTS for one slide and return the .mp3 path, or None.

    Uses the same backend probe as ``explain_audio``; if no backend resolves,
    returns None (the caller handles the degradation).
    """
    from opennote.audio.tts import TtsResult, _MAX_TTS_CHARS

    # L65: cap the narration text fed to TTS providers.
    narration = slide.narration[:_MAX_TTS_CHARS]

    # Ask the adapter chain for the audio; it degrades to a transcript (None
    # audio_path) when no backend is available.
    result: TtsResult = explain_audio(narration, output_dir=output_dir)
    if result.success and result.audio_path and Path(result.audio_path).exists():
        # Normalize the path to a per-slide file name for stable ordering (L62).
        target = output_dir / f"slide-{index:02d}.mp3"
        saved = Path(result.audio_path)
        if saved.suffix.lower() not in (".mp3", ".wav"):
            return None
        if saved != target:
            try:
                if saved.suffix.lower() == ".wav":
                    target = target.with_suffix(".wav")
                import shutil

                shutil.copyfile(str(saved), str(target))
            except Exception:
                return str(saved)
        return str(target)
    return None


# ---------------------------------------------------------------------------
# FFmpeg mux
# ---------------------------------------------------------------------------

def _mp3_duration(path: Path) -> float:
    """Best-effort MP3 duration in seconds via ffprobe, defaulting to 5.0."""
    probe = shutil.which("ffprobe")
    if probe and path.exists():
        try:
            out = subprocess.run(
                [
                    probe, "-v", "quiet", "-show_entries", "format=duration",
                    "-of", "csv=p=0", str(path),
                ],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            )
            if out.returncode == 0 and out.stdout.strip():
                return max(0.5, float(out.stdout.strip()))
        except Exception:
            pass
    return 5.0


def _ffmpeg_mux(image_dir: Path, audio_dir: Path, output_mp4: Path) -> Tuple[bool, Optional[str]]:
    """Mux images from *image_dir* + audio from *audio_dir* into *output_mp4*.

    Returns ``(success, error)``. The MP4 is built from per-slide clips whose
    duration matches each narration track (L52/L53/L62).
    """
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        return False, "ffmpeg is not installed or not on PATH."

    # Build list of image files sorted by slide index (natural sort, fixes lexicographic 100+ bug)
    import re as _re
    def _idx(p: Path) -> int:
        m = _re.search(r"slide-(\d+)", p.name)
        return int(m.group(1)) if m else -1
    image_map = { _idx(p): p for p in image_dir.glob("slide-*.png") if _idx(p) >= 0 }
    audio_map = { _idx(p): p for p in audio_dir.glob("slide-*.mp3") if _idx(p) >= 0 }
    if not image_map:
        return False, "No slide images found."
    if not audio_map:
        return False, "No narration audio found."
    # Pair by slide index, not positionally — fixes E1 where a failed TTS left a gap
    paired_indices = sorted(set(image_map) & set(audio_map))
    if not paired_indices:
        return False, "No matching slide image/audio pairs found."
    clips_dir = output_mp4.parent / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    clip_paths: List[Path] = []
    for i, idx in enumerate(paired_indices):
        img = image_map[idx]
        aud = audio_map[idx]
        duration = _mp3_duration(aud)
        out_clip = clips_dir / f"clip-{i:02d}.mp4"
        cmd = [
            ffmpeg_path,
            "-loop", "1",
            "-i", str(img),
            "-i", str(aud),
            "-c:v", "libx264",
            "-c:a", "aac",
            "-t", f"{duration:.2f}",
            "-pix_fmt", "yuv420p",
            "-shortest",
            "-y",
            str(out_clip),
        ]
        try:
            run = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        except Exception as exc:  # noqa: BLE001
            return False, f"ffmpeg clip encode failed for slide {i + 1}: {exc}"
        if run.returncode != 0:
            return False, (
                f"ffmpeg clip encode failed for slide {i + 1} "
                f"(rc={run.returncode}): {run.stderr[-500:]}"
            )
        clip_paths.append(out_clip)

    if not clip_paths:
        return False, "No clips were encoded."

    # Concat the clips into the final slideshow.mp4 (L52).
    # Escape single quotes per ffmpeg concat demuxer spec (E2).
    def _escape(p: Path) -> str:
        return p.as_posix().replace("'", r"'\''")
    concat_list = clips_dir / "concat.txt"
    concat_list.write_text(
        "\n".join(f"file '{_escape(c)}'" for c in clip_paths) + "\n",
        encoding="utf-8",
    )
    out_clip = clips_dir / f"clip-{len(paired_indices):02d}.mp4"  # temp path for concat result
    cmd = [
        ffmpeg_path,
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        "-y",
        str(out_clip),
    ]
    try:
        run = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    except Exception as exc:  # noqa: BLE001
        return False, f"ffmpeg concat failed: {exc}"
    if run.returncode != 0 or not out_clip.exists():
        # Concat with stream copy can fail on codec mismatch; fall back to
        # re-encoding the concatenated stream.
        cmd = [
            ffmpeg_path,
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list),
            "-c:v", "libx264",
            "-c:a", "aac",
            "-pix_fmt", "yuv420p",
            "-y",
            str(out_clip),
        ]
        try:
            run = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        except Exception as exc:  # noqa: BLE001
            return False, f"ffmpeg concat re-encode failed: {exc}"
        if run.returncode != 0 or not out_clip.exists():
            return False, (
                f"ffmpeg concat failed (rc={run.returncode}): {run.stderr[-500:]}"
            )

    import shutil as _shutil

    _shutil.move(str(out_clip), str(output_mp4))
    return output_mp4.exists(), None


# ---------------------------------------------------------------------------
# Main entry point: explain_video tool
# ---------------------------------------------------------------------------

def explain_video(script_json: str, output_dir: Optional[Path] = None) -> VideoResult:
    """Generate a narrated slideshow video from JSON.

    ``script_json`` is a JSON string representing a list of ``Slide`` objects
    (title, bullets, narration). The tool degrades gracefully at each stage.

    Parameters
    ----------
    script_json : str
        JSON string ``[{"title": "...", "bullets": [...], "narration": "..."},
        ...]`` describing the slideshow.
    output_dir : Path or None
        Directory to save assets in. If None, uses ``notebook/artifacts/``.

    Returns
    -------
    VideoResult
        ``success=True`` with paths to saved assets (video, audio, slides).
        ``success=False`` with the highest‑available stage completed (script,
        slides, or audio only).
    """
    # Parse the script JSON
    try:
        slides_data = json.loads(script_json)
    except Exception:
        return VideoResult(
            success=False,
            error="Invalid JSON for script. Expected a list of slide objects.",
        )

    if not isinstance(slides_data, list):
        return VideoResult(
            success=False,
            error="Script must be a list of slide objects with 'title', 'bullets', "
            "and 'narration' fields.",
        )

    # Tolerant slide construction (L54): LLM output often carries extra keys
    # or non-string fields. Filter to known fields + coerce to str.
    _SLIDE_FIELDS = ("title", "bullets", "narration")
    slides: List[Slide] = []
    for s in slides_data:
        if not isinstance(s, dict):
            return VideoResult(success=False, error="Script contains a non-object slide.")
        clean = {k: s.get(k) for k in _SLIDE_FIELDS}
        if not clean["title"] or clean["narration"] is None:
            return VideoResult(
                success=False,
                error="Each slide needs a 'title', 'bullets', and 'narration'.",
            )
        bullets = clean.get("bullets")
        if isinstance(bullets, str):
            bullets = [bullets]
        if not isinstance(bullets, list):
            bullets = []
        slides.append(
            Slide(
                title=str(clean["title"]),
                bullets=[str(b) for b in bullets],
                narration=str(clean["narration"]),
            )
        )

    # Empty slide list is a degenerate success — treat as an error (L67).
    if not slides:
        return VideoResult(
            success=False,
            error="Script contained no slides.",
        )

    n_slides = len(slides)

    # Set output directory
    if output_dir is None:
        # Will be set by the agent loop; default to cwd/artifacts
        output_dir = Path.cwd() / "artifacts"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    result = VideoResult(success=False)

    # --- Stage 1: Render slide images + save script as markdown ---
    script_lines = [f"# Narrated Slideshow\n"]
    for i, slide in enumerate(slides):
        script_lines.append(f"## Slide {i + 1}: {slide.title}\n")
        script_lines.append(f"**Bullets:** {', '.join(slide.bullets)}\n")
        script_lines.append(f"**Narration:**\n{slide.narration}\n\n")
    script_md_path = output_dir / "slideshow-script.md"
    script_md_path.write_text("\n".join(script_lines), encoding="utf-8")
    result.script_path = str(script_md_path)

    # Render slide images
    slides_dir = output_dir / "slides"
    slides_dir.mkdir(parents=True, exist_ok=True)
    image_paths: List[str] = []
    for i, slide in enumerate(slides):
        img = _make_slide_image(slide, i)
        img_path = slides_dir / f"slide-{i:02d}.png"
        img.save(img_path, "PNG")
        image_paths.append(str(img_path))
    if image_paths:
        result.slides_dir = str(slides_dir)

    # --- Stage 2: Generate per-slide TTS narration ---
    audio_dir = output_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_paths: List[Optional[str]] = []
    for i, slide in enumerate(slides):
        audio_path = _generate_slide_audio(slide, i, audio_dir)
        audio_paths.append(audio_path)
    # Filter out None values; keep only successfully generated
    audio_paths = [p for p in audio_paths if p is not None]
    if audio_paths:
        result.audio_paths = audio_paths

    # --- Stage 3: Mux with ffmpeg ---
    if image_paths and audio_paths:
        if shutil.which("ffmpeg"):
            mux_ok, mux_err = _ffmpeg_mux(
                slides_dir, audio_dir, output_dir / "slideshow.mp4"
            )
            if mux_ok:
                result.video_path = str(output_dir / "slideshow.mp4")
            else:
                # L53/L63: surface the real failure instead of claiming success.
                result.error = mux_err or "video mux failed"
        else:
            # L63: ffmpeg missing must not silently degrade to success=True.
            result.error = "ffmpeg is not installed or not on PATH; video skipped."

    # Set final result — success iff we saved at least the script + slides.
    result.audio_paths = audio_paths  # type: ignore
    result.success = bool(result.script_path and result.slides_dir)

    return result


# ---------------------------------------------------------------------------
# Convenience wrapper (studio mode / slash command)
# ---------------------------------------------------------------------------

def save_video_artifact(
    script_json: str,
    artifacts_dir: Optional[Path] = None,
    notebook_name: str = "default",
) -> str:
    """Generate video explanation and save to notebook artifacts.

    Returns the path to the saved video, or the transcript/Slides path if video
    generation failed.
    """
    from opennote.notebooks import validate_notebook_name

    validate_notebook_name(notebook_name)
    # Use the caller‑provided artifacts_dir so that workspace‑mode notebooks
    # write under <root>/artifacts instead of ~/.opennote/notebooks/<name>/artifacts.
    if artifacts_dir is None:
        from opennote.notebooks import NotebookManager

        try:
            artifacts_dir = NotebookManager().get(notebook_name).artifacts_dir
        except Exception:
            artifacts_dir = Path.cwd() / "artifacts"
    output_dir = artifacts_dir
    result = explain_video(script_json, output_dir=output_dir)

    if result.success and result.video_path:
        return result.video_path
    elif result.script_path:
        # Return the script path as fallback
        return result.script_path
    else:
        # Return error info
        return f"artifacts/{notebook_name}/video-error.md"