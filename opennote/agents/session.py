"""Persistent session management for the agentic chat loop.

Sessions live inside the notebook folder so that moving/renaming a notebook
automatically migrates its conversation history. The on-disk format is kept
deliberately neutral so resuming works regardless of which provider the user
selected when the session was created.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from opennote.chat.client import ChatError

SESSIONS_DIR_NAME = "sessions"
SESSION_EXT = ".json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sessions_dir(notebook) -> Path:
    return notebook.directory / SESSIONS_DIR_NAME


def _session_path(notebook, session_id: str) -> Path:
    return _sessions_dir(notebook) / f"{session_id}{SESSION_EXT}"


def load_session(notebook, session_id: str) -> Optional[Dict]:
    path = _session_path(notebook, session_id)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        logging.getLogger("opennote.agents.session").warning(
            "Session file '%s' is corrupt or unreadable; ignoring it.", path
        )
        return None
    if not isinstance(data, dict):
        logging.getLogger("opennote.agents.session").warning(
            "Session file '%s' does not contain a JSON object; ignoring it.", path
        )
        return None
    data.setdefault("id", session_id)
    data.setdefault("messages", [])
    return data


def save_session(notebook, session: Dict) -> None:
    session["updated"] = _now()
    _sessions_dir(notebook).mkdir(parents=True, exist_ok=True)
    path = _session_path(notebook, session["id"])
    _atomic_write_json(path, session)


def _atomic_write_json(path: Path, data: Dict) -> None:
    """Write *data* to *path* atomically (tmp file + rename)."""
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_name, path)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def list_sessions(notebook) -> List[Dict]:
    """Return session dicts sorted by most recently updated first."""
    d = _sessions_dir(notebook)
    if not d.is_dir():
        return []
    sessions: List[Dict] = []
    for entry in d.iterdir():
        if entry.suffix != SESSION_EXT:
            continue
        sid = entry.name[: -len(SESSION_EXT)]
        data = load_session(notebook, sid)
        if data is not None:
            sessions.append(data)
    sessions.sort(key=lambda s: s.get("updated", ""), reverse=True)
    return sessions


def new_session(notebook, provider_id: str, model: str) -> Dict:
    session = {
        "id": str(uuid.uuid4()),
        "created": _now(),
        "updated": _now(),
        "provider_id": provider_id,
        "model": model,
        "messages": [],
    }
    save_session(notebook, session)
    return session


def append_messages(notebook, session_id: str, messages: List[Dict]) -> Dict:
    """Append neutral *messages* to a session, trim to budget, and save."""
    session = load_session(notebook, session_id)
    if session is None:
        raise ChatError(f"Session '{session_id}' does not exist in this notebook.")
    session["messages"].extend(messages)
    session["messages"] = trim_messages(session["messages"])
    save_session(notebook, session)
    return session


def trim_messages(messages: List[Dict], max_chars: int = 120_000) -> List[Dict]:
    """Drop oldest messages so total content length stays <= *max_chars*.

    Trimming is turn-aware: only whole turns are dropped, and the surviving
    prefix never begins with a ``tool`` message or an ``assistant`` message
    carrying ``tool_calls`` (both would be invalid for OpenAI/Anthropic).
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
        """A message at *idx* may be the first survivor."""
        msg = messages[idx]
        role = msg.get("role")
        # Anthropic requires the first message to be 'user'; a 'tool' or an
        # assistant-with-tool_calls start would orphan (or be orphaned by) the
        # tool responses.
        if role != "user":
            return False
        return True

    # Advance past leading invalid messages (defensive: corrupt persisted state).
    start = 0
    while start < len(messages) - 1 and not valid_start(start):
        start += 1

    def total_size(from_idx: int) -> int:
        return sum(size(m) for m in messages[from_idx:])

    while len(messages) - start > 1 and total_size(start) > max_chars:
        # Drop the current leading message, then skip any now-orphaned tools.
        start += 1
        while start < len(messages) - 1 and not valid_start(start):
            start += 1

    return list(messages[start:])