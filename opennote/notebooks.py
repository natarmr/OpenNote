"""Notebook management.

A notebook is a self-contained folder on disk:
    <home>/notebooks/<name>/
        notebook.json          # name, embedding model, created, sources, project, transcript
        transcript.json        # conversation history (notebook == session)
        chroma/                # ChromaDB store + per-collection manifest

The embedding model identity lives in notebook.json so a notebook never
silently mixes embedding spaces. Each notebook is scoped to the directory
where it was created (project), with per-directory listing.

Each notebook holds at most MAX_SOURCES sources.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from opennote.fsutil import walk_worktree_roots
from opennote.store.vectors import DEFAULT_EMBED_MODEL

COLLECTION_NAME = "documents"
NOTES_DIR_NAME = "notebooks"
MAX_SOURCES = 5

#: Notebook names must be safe on every filesystem: no separators, no "..",
#: no empty strings, no Windows device names.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def validate_notebook_name(name: str) -> None:
    """Raise ``ValueError`` unless *name* is a safe notebook name."""
    if not isinstance(name, str) or not name:
        raise ValueError("Notebook name cannot be empty.")
    if not _NAME_RE.match(name):
        raise ValueError(
            f"Invalid notebook name '{name!r}'. Use only letters, digits, "
            "'.', '_', '-' and do not use path separators or '..'."
        )
    if name.upper() in _RESERVED_NAMES:
        raise ValueError(f"Notebook name '{name}' is reserved on Windows.")


def default_home() -> Path:
    env = os.environ.get("OPENNOTE_HOME")
    if env:
        return Path(env)
    # Default to project-local when no env set; keep backward compat for
    # callers that pass explicit ``home``.
    return Path.cwd() / ".opennote"


def _norm_project(p: str) -> str:
    """Normalize project path for comparison (case-insensitive on Windows)."""
    if not p:
        return ""
    # On Windows normcase lowercases; on POSIX it's a no-op.
    return os.path.normcase(os.path.normpath(p))


def current_project() -> str:
    """Return the current working directory as project key."""
    try:
        return str(Path.cwd().resolve())
    except Exception:
        return str(Path.cwd())


def resolve_home(cwd: Path | None = None) -> Path:
    """Return the project-local home for notebook data.

    Priority:
    1. OPENNOTE_HOME env var (explicit override; tests use this).
    2. Walk ancestors from *cwd* (or Path.cwd()) up to .git boundary;
       if an ancestor contains a ``.opennote`` directory, use that.
    3. Fallback to ``cwd / ".opennote"`` (new project root).
    """
    # 1. Explicit env override (used by tests)
    env = os.environ.get("OPENNOTE_HOME")
    if env:
        return Path(env)

    # 2. Walk ancestors looking for a .opennote marker dir
    start = cwd or Path.cwd()
    for root in walk_worktree_roots(start):
        marker = root / ".opennote"
        if marker.is_dir():
            return marker

    # 3. Fallback: project-local under cwd
    return Path.cwd() / ".opennote"


@dataclass
class Notebook:
    name: str
    directory: Path
    embed_model: str = DEFAULT_EMBED_MODEL
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    sources: List[str] = field(default_factory=list)
    project: str = ""
    updated: str = ""
    provider_id: str = ""
    model: str = ""

    @property
    def store_dir(self) -> Path:
        return self.directory / "chroma"

    @property
    def artifacts_dir(self) -> Path:
        return self.directory / "artifacts"

    @property
    def meta_file(self) -> Path:
        return self.directory / "notebook.json"

    @property
    def transcript_file(self) -> Path:
        return self.directory / "transcript.json"

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "embed_model": self.embed_model,
            "created": self.created,
            "sources": self.sources,
            "project": self.project,
            "updated": self.updated,
            "provider_id": self.provider_id,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, data: Dict, directory: Path) -> "Notebook":
        return cls(
            name=data.get("name", directory.name),
            directory=directory,
            embed_model=data.get("embed_model", DEFAULT_EMBED_MODEL),
            created=data.get("created", ""),
            sources=list(data.get("sources", [])),
            project=data.get("project", ""),
            updated=data.get("updated", ""),
            provider_id=data.get("provider_id", ""),
            model=data.get("model", ""),
        )

    def save(self):
        from opennote.fsutil import atomic_write_json

        # Keep updated timestamp fresh
        if not self.updated:
            self.updated = datetime.now(timezone.utc).isoformat()
        else:
            # Bump updated on every save (callers may have already set it)
            pass
        atomic_write_json(self.meta_file, self.to_dict())

    def touch_updated(self):
        self.updated = datetime.now(timezone.utc).isoformat()
        self.save()


class NotebookManager:
    def __init__(self, home: Path | None = None):
        if home is not None:
            self.home = Path(home)
        else:
            self.home = default_home()
        self.notebooks_dir = self.home / NOTES_DIR_NAME
        self.notebooks_dir.mkdir(parents=True, exist_ok=True)

    def list(self) -> List[Notebook]:
        notebooks: List[Notebook] = []
        if not self.notebooks_dir.is_dir():
            return notebooks
        for entry in sorted(self.notebooks_dir.iterdir()):
            if entry.is_dir() and (entry / "notebook.json").exists():
                try:
                    notebooks.append(self._load(entry))
                except (json.JSONDecodeError, OSError):
                    import logging

                    logging.getLogger("opennote.notebooks").warning(
                        "Skipping unreadable notebook at '%s'.", entry
                    )
        return notebooks

    def list_for_project(self, project: str | None = None) -> List[Notebook]:
        """List notebooks for *project* (cwd if None)."""
        if project is None:
            project = current_project()
        # With per-project isolation, list() already returns the resolved project's notebooks.
        return self.list()

    def next_notebook_name(self, project: str | None = None) -> str:
        """Return next free 'notebook-N' name for *project*."""
        if project is None:
            project = current_project()
        # Only check within the resolved project's directory (local isolation).
        existing = {nb.name for nb in self.list_for_project(project)}
        n = 1
        while True:
            cand = f"notebook-{n}"
            if cand not in existing:
                return cand
            n += 1

    def get(self, name: str) -> Notebook:
        validate_notebook_name(name)
        directory = self.notebooks_dir / name
        if not (directory / "notebook.json").exists():
            raise KeyError(
                f"Notebook '{name}' does not exist. Create it with: opennote create {name}"
            )
        return self._load(directory)

    def create(
        self,
        name: str,
        embed_model: str = DEFAULT_EMBED_MODEL,
        project: str | None = None,
    ) -> Notebook:
        validate_notebook_name(name)
        directory = self.notebooks_dir / name
        if directory.exists():
            raise FileExistsError(f"Notebook '{name}' already exists.")
        if any(
            d.is_dir() and d.name.lower() == name.lower()
            for d in self.notebooks_dir.iterdir()
        ):
            raise FileExistsError(
                f"Notebook '{name}' already exists (case-insensitive match)."
            )
        if project is None:
            project = current_project()
        now = datetime.now(timezone.utc).isoformat()
        notebook = Notebook(
            name=name,
            directory=directory,
            embed_model=embed_model,
            project=project,
            created=now,
            updated=now,
        )
        # Use atomic write directly to avoid double touch
        from opennote.fsutil import atomic_write_json

        directory.mkdir(parents=True, exist_ok=True)
        atomic_write_json(notebook.meta_file, notebook.to_dict())
        return notebook

    def create_next(self, project: str | None = None, embed_model: str = DEFAULT_EMBED_MODEL) -> Notebook:
        """Create next auto-numbered notebook for *project*."""
        name = self.next_notebook_name(project)
        return self.create(name, embed_model=embed_model, project=project)

    def delete(self, name: str):
        validate_notebook_name(name)
        directory = self.notebooks_dir / name
        if not (directory / "notebook.json").exists():
            raise KeyError(f"Notebook '{name}' does not exist.")
        import gc
        import shutil
        import time

        # Release any open Chroma handles for this notebook (Windows WinError 32)
        try:
            from opennote.store.vectors import close_all_for_path

            close_all_for_path(directory / "chroma")
        except Exception:
            pass
        gc.collect()

        # Retry loop for Windows file-lock (chroma.sqlite3)
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                shutil.rmtree(directory)
                return
            except PermissionError as e:
                last_exc = e
                if attempt < 2:
                    time.sleep(0.3 * (attempt + 1))
                    gc.collect()
                    continue
                # Final attempt with onerror to handle read-only files
                try:
                    import os

                    def _onerror(func, path, exc_info):
                        try:
                            os.chmod(path, 0o777)
                            func(path)
                        except Exception:
                            pass

                    shutil.rmtree(directory, onerror=_onerror)
                    return
                except PermissionError as e2:
                    chroma_file = directory / "chroma" / "chroma.sqlite3"
                    raise PermissionError(
                        f"Cannot delete notebook '{name}': file is in use (chroma.sqlite3 locked, WinError 32). "
                        f"Close the TUI or any 'opennote search/ask' process holding '{chroma_file}' and try again. "
                        f"If the TUI is still open, switch to another notebook first, then delete."
                    ) from e2
        if last_exc is not None:
            raise last_exc

    def rename(self, old: str, new: str) -> Notebook:
        validate_notebook_name(old)
        validate_notebook_name(new)
        old_dir = self.notebooks_dir / old
        new_dir = self.notebooks_dir / new
        if not (old_dir / "notebook.json").exists():
            raise KeyError(f"Notebook '{old}' does not exist.")
        if new_dir.exists() or any(
            d.is_dir() and d.name.lower() == new.lower()
            for d in self.notebooks_dir.iterdir()
            if d != old_dir
        ):
            raise FileExistsError(f"Notebook '{new}' already exists.")
        old_dir.rename(new_dir)
        notebook = self._load(new_dir)
        notebook.name = new
        notebook.touch_updated()
        return notebook

    @staticmethod
    def _load(directory: Path) -> Notebook:
        with open(directory / "notebook.json", "r", encoding="utf-8") as f:
            return Notebook.from_dict(json.load(f), directory)
