"""Secrets-free auth configuration.

Stored at ``<home>/auth.json`` (``OPENNOTE_HOME`` overrides the default home).
Holds only per-provider settings — never API keys (those live in the OS
keychain). Writes are atomic (tmp file + rename).
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

AUTH_CONFIG_NAME = "auth.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ProviderSettings:
    model: Optional[str] = None
    base_url_override: Optional[str] = None
    added_at: Optional[str] = None
    last_validated_at: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "model": self.model,
            "base_url_override": self.base_url_override,
            "added_at": self.added_at,
            "last_validated_at": self.last_validated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ProviderSettings":
        return cls(
            model=data.get("model"),
            base_url_override=data.get("base_url_override"),
            added_at=data.get("added_at"),
            last_validated_at=data.get("last_validated_at"),
        )


class AuthConfig:
    """Non-secret per-provider settings, keyed by provider id."""

    def __init__(self, path: Optional[Path] = None):
        env = os.environ.get("OPENNOTE_HOME")
        self.path = Path(path) if path else Path(env or Path.home() / ".opennote") / AUTH_CONFIG_NAME
        self._providers: Dict[str, ProviderSettings] = {}
        self.load()

    def load(self) -> None:
        self._providers = {}
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            # Never silently destroy a corrupt config: back it up so the next
            # save() can't overwrite the only surviving copy of the data.
            if self.path.exists():
                backup = self.path.with_suffix(".json.corrupt")
                try:
                    import shutil

                    shutil.copy2(self.path, backup)
                    import logging

                    logging.getLogger("opennote.auth.config").warning(
                        "Auth config '%s' is corrupt; backed up to '%s'.", self.path, backup
                    )
                except OSError:
                    pass
            return
        if not isinstance(raw, dict):
            return
        for pid, data in raw.items():
            if isinstance(data, dict):
                self._providers[pid] = ProviderSettings.from_dict(data)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(
                    {pid: s.to_dict() for pid, s in sorted(self._providers.items())},
                    f,
                    indent=2,
                )
            os.replace(tmp_name, self.path)
        except Exception:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise

    def providers(self) -> Dict[str, ProviderSettings]:
        return dict(self._providers)

    def get(self, provider_id: str) -> Optional[ProviderSettings]:
        return self._providers.get(provider_id)

    def mark_added(self, provider_id: str) -> ProviderSettings:
        settings = self._providers.setdefault(provider_id, ProviderSettings())
        settings.added_at = _now()
        self.save()
        return settings

    def mark_validated(self, provider_id: str) -> ProviderSettings:
        settings = self._providers.setdefault(provider_id, ProviderSettings())
        settings.last_validated_at = _now()
        self.save()
        return settings

    def set_model(self, provider_id: str, model: str) -> ProviderSettings:
        settings = self._providers.setdefault(provider_id, ProviderSettings())
        settings.model = model
        self.save()
        return settings

    def set_base_url(self, provider_id: str, base_url: str) -> ProviderSettings:
        settings = self._providers.setdefault(provider_id, ProviderSettings())
        settings.base_url_override = base_url
        self.save()
        return settings

    def remove(self, provider_id: str) -> bool:
        existed = provider_id in self._providers
        self._providers.pop(provider_id, None)
        self.save()
        return existed