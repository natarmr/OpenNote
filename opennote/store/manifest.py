"""Persistent manifest tracking indexed file hashes for change detection."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict


class Manifest:
    """Maps source file paths to their indexed SHA256 hashes."""

    def __init__(self, manifest_file: Path):
        self.manifest_file = manifest_file
        self.data: Dict[str, str] = self._load()

    def _load(self) -> Dict[str, str]:
        if self.manifest_file.exists():
            try:
                with open(self.manifest_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data if isinstance(data, dict) else {}
            except (json.JSONDecodeError, OSError) as exc:
                import logging
                logging.getLogger("opennote.store.manifest").warning(
                    "Manifest '%s' corrupt/unreadable (%s); starting fresh.", self.manifest_file, exc
                )
                return {}
        return {}

    def save(self):
        self.manifest_file.parent.mkdir(parents=True, exist_ok=True)
        import os
        import tempfile
        fd, tmp = tempfile.mkstemp(dir=self.manifest_file.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.manifest_file)
        except Exception:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            raise

    def is_indexed(self, source: str, file_hash: str) -> bool:
        return self.data.get(source) == file_hash

    def mark_indexed(self, source: str, file_hash: str):
        self.data[source] = file_hash
        self.save()

    def clear(self):
        if self.manifest_file.exists():
            try:
                self.manifest_file.unlink()
            except Exception:
                pass
        self.data = {}