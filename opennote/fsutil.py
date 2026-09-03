"""Shared filesystem utilities: atomic writes, timestamps, home resolution."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def atomic_write_json(path: Path, obj: Any, **dump_kwargs) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, **dump_kwargs)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


def walk_worktree_roots(start: Path | None = None) -> list[Path]:
    """Yield *start* and ancestors up to git worktree root (or FS root, max 30).

    Stops at the first ancestor containing a ``.git`` dir/file — mirrors opencode.
    Resolves symlinks safely; never raises on broken links.
    """
    try:
        cur = (Path(start) if start is not None else Path.cwd()).resolve()
    except (OSError, RuntimeError):
        cur = Path.cwd()
    roots: list[Path] = []
    seen: set[Path] = set()
    for _ in range(30):
        if cur in seen:
            break
        seen.add(cur)
        roots.append(cur)
        # Stop at git worktree boundary
        try:
            if (cur / ".git").exists():
                break
        except (OSError, RuntimeError):
            pass
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return roots
