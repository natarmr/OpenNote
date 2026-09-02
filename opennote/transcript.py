"""Notebook transcript (replaces sessions).

Each notebook owns a single transcript stored at ``notebook/transcript.json``.
This module is the single owner of transcript persistence and the message
trim helper. Legacy ``sessions/`` dirs are migrated lazily.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List

from opennote.fsutil import atomic_write_json

TRANSCRIPT_FILE = "transcript.json"


def _transcript_path(notebook) -> Path:
    return notebook.directory / TRANSCRIPT_FILE


def _backfill_provenance(msgs: List[Dict]) -> List[Dict]:
    """Lazy backfill: user -> trusted, assistant/tool -> derived if missing."""
    for m in msgs:
        if "provenance" not in m:
            role = m.get("role")
            if role == "user":
                m["provenance"] = "trusted"
            else:
                m["provenance"] = "derived"
    return msgs


def load_transcript(notebook) -> List[Dict]:
    """Return messages list for *notebook* (empty if none). Migrates legacy sessions if needed."""
    tpath = _transcript_path(notebook)
    if not tpath.exists():
        migrated = _migrate_legacy_sessions(notebook)
        if migrated is not None:
            return _backfill_provenance(migrated)
        return []
    try:
        with open(tpath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        logging.getLogger("opennote.transcript").warning(
            "Transcript file '%s' corrupt; returning empty.", tpath
        )
        return []
    if isinstance(data, dict):
        msgs = data.get("messages", [])
        if isinstance(msgs, list):
            return _backfill_provenance(msgs)
    if isinstance(data, list):
        return _backfill_provenance(data)
    return []


def save_transcript(notebook, messages: List[Dict]) -> None:
    """Persist *messages* for *notebook* (trimmed, atomic)."""
    # Ensure provenance tagged
    messages = _backfill_provenance(list(messages))
    messages = trim_messages(messages)
    tpath = _transcript_path(notebook)
    tpath.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(tpath, {"messages": messages})
    # Bump notebook updated timestamp
    try:
        notebook.touch_updated()
    except Exception:
        pass


def append_messages(notebook, messages: List[Dict]) -> List[Dict]:
    """Append *messages* to notebook transcript and persist. Returns new list."""
    # Tag provenance: user -> trusted, others -> derived (if not already set)
    for m in messages:
        if "provenance" not in m:
            m["provenance"] = "trusted" if m.get("role") == "user" else "derived"
    existing = load_transcript(notebook)
    existing.extend(messages)
    save_transcript(notebook, existing)
    return load_transcript(notebook)


def history_for_prompt(messages: List[Dict]) -> List[Dict]:
    """Wrap derived messages as <source> data for next prompt (never as instructions)."""
    out: List[Dict] = []
    for m in messages:
        prov = m.get("provenance", "derived" if m.get("role") != "user" else "trusted")
        content = m.get("content", "")
        if isinstance(content, str) and prov == "derived":
            # Wrap derived content as citable data
            wrapped = f'<source provenance="derived">\n{content.replace("</source>", "<\\/source>")}\n</source>'
            nm = dict(m)
            nm["content"] = wrapped
            out.append(nm)
        else:
            out.append(m)
    return out


def clear_transcript(notebook) -> None:
    """Clear transcript."""
    save_transcript(notebook, [])


def _migrate_legacy_sessions(notebook) -> List[Dict] | None:
    """If legacy sessions/ exists and transcript.json missing, copy most recent session's messages."""
    sessions_dir = notebook.directory / "sessions"
    if not sessions_dir.is_dir():
        return None
    # Find most recent session file by updated field or mtime
    best_msgs: List[Dict] | None = None
    best_updated = ""
    for entry in sessions_dir.iterdir():
        if entry.name.endswith(".meta.json"):
            continue
        if entry.suffix != ".json":
            continue
        try:
            with open(entry, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        msgs = data.get("messages")
        if not isinstance(msgs, list):
            continue
        updated = data.get("updated", "")
        if best_msgs is None or updated > best_updated:
            best_updated = updated
            best_msgs = msgs
    if best_msgs is not None:
        # Persist as new transcript (leave sessions/ untouched)
        try:
            tpath = _transcript_path(notebook)
            atomic_write_json(tpath, {"messages": trim_messages(best_msgs)})
            try:
                notebook.touch_updated()
            except Exception:
                pass
        except Exception:
            pass
        return trim_messages(best_msgs)
    return None


def trim_messages(messages: List[Dict], max_chars: int = 120_000) -> List[Dict]:
    """Drop oldest messages so total content length stays <= *max_chars*.

    Trimming is turn-aware: only whole turns are dropped, and the surviving
    prefix never begins with a ``tool`` message or an ``assistant`` message
    carrying ``tool_calls``.
    The most recent message is always kept.
    """
    if not messages:
        return []

    def size(msg: Dict) -> int:
        try:
            return len(json.dumps(msg, default=str))
        except (TypeError, ValueError):
            content = msg.get("content", "")
            if isinstance(content, str):
                return len(content)
            return len(str(content))

    def valid_start(idx: int) -> bool:
        msg = messages[idx]
        role = msg.get("role")
        if role != "user":
            return False
        return True

    start = 0
    while start < len(messages) - 1 and not valid_start(start):
        start += 1

    def total_size(from_idx: int) -> int:
        return sum(size(m) for m in messages[from_idx:])

    while len(messages) - start > 1 and total_size(start) > max_chars:
        start += 1
        while start < len(messages) - 1 and not valid_start(start):
            start += 1

    result = list(messages[start:])
    if len(result) == 1 and not valid_start(start):
        return []
    return result
