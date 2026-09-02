"""Notebook management.

A notebook is a self-contained folder on disk:
    <home>/notebooks/<name>/
        notebook.json          # name, embedding model, created, sources
        chroma/                # ChromaDB store + per-collection manifest

The embedding model identity lives in notebook.json so a notebook never
silently mixes embedding spaces.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from opennote.store.vectors import DEFAULT_EMBED_MODEL

COLLECTION_NAME = "documents"
NOTES_DIR_NAME = "notebooks"

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
    return Path.home() / ".opennote"


@dataclass
class Notebook:
    name: str
    directory: Path
    embed_model: str = DEFAULT_EMBED_MODEL
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    sources: List[str] = field(default_factory=list)

    @property
    def store_dir(self) -> Path:
        return self.directory / "chroma"

    @property
    def artifacts_dir(self) -> Path:
        return self.directory / "artifacts"

    @property
    def meta_file(self) -> Path:
        return self.directory / "notebook.json"

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "embed_model": self.embed_model,
            "created": self.created,
            "sources": self.sources,
        }

    @classmethod
    def from_dict(cls, data: Dict, directory: Path) -> "Notebook":
        return cls(
            name=data.get("name", directory.name),
            directory=directory,
            embed_model=data.get("embed_model", DEFAULT_EMBED_MODEL),
            created=data.get("created", ""),
            sources=list(data.get("sources", [])),
        )

    def save(self):
        from opennote.fsutil import atomic_write_json

        atomic_write_json(self.meta_file, self.to_dict())


class NotebookManager:
    def __init__(self, home: Path | None = None):
        self.home = Path(home) if home else default_home()
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
                    # One corrupt notebook must not break 'opennote list'.
                    import logging

                    logging.getLogger("opennote.notebooks").warning(
                        "Skipping unreadable notebook at '%s'.", entry
                    )
        return notebooks

    def get(self, name: str) -> Notebook:
        validate_notebook_name(name)
        directory = self.notebooks_dir / name
        if not (directory / "notebook.json").exists():
            raise KeyError(
                f"Notebook '{name}' does not exist. Create it with: opennote create {name}"
            )
        return self._load(directory)

    def create(self, name: str, embed_model: str = DEFAULT_EMBED_MODEL) -> Notebook:
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
        notebook = Notebook(name=name, directory=directory, embed_model=embed_model)
        notebook.save()
        return notebook

    def delete(self, name: str):
        validate_notebook_name(name)
        directory = self.notebooks_dir / name
        if not (directory / "notebook.json").exists():
            raise KeyError(f"Notebook '{name}' does not exist.")
        import shutil

        shutil.rmtree(directory)

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
        notebook.save()
        return notebook

    @staticmethod
    def _load(directory: Path) -> Notebook:
        with open(directory / "notebook.json", "r", encoding="utf-8") as f:
            return Notebook.from_dict(json.load(f), directory)