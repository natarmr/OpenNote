"""Persistent local model registry (GGUF files).

Stores model metadata under the notebook home directory so that ``opennote``
can keep track of which model files are available, which one is active, and
avoid rescanning on every start.

On-disk format (<OPENNOTE_HOME>/local.json):

.. code-block:: json

    {
      "models": {
        "qwen2.5-7b": {
          "path": "C:/models/qwen2.5-7b-q4.gguf",
          "n_ctx": 4096,
          "threads": null
        },
        "llama3.2-3b": {
          "path": "D:/models/llama3.2-3b-q3.gguf",
          "n_ctx": 32768,
          "threads": 4
        }
      },
      "active": "qwen2.5-7b"
    }

````

The file is written atomically (``tmp`` + ``os.replace``) the same way
``auth.json`` is.  Reading the file is fast — only the active entry plus
the full list is needed for the menu / /model command.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from opennote.auth.config import ProviderSettings, _now

LOCAL_CONFIG_NAME = "local.json"

# --------------------------------------------------------------------------- #
# Path & I/O helpers
# --------------------------------------------------------------------------- #


def _local_dir(notebook_home: Optional[Path] = None) -> Path:
    if notebook_home is not None:
        return Path(notebook_home)
    env = os.environ.get("OPENNOTE_HOME")
    return Path(env) if env else Path.home() / ".opennote"


def _local(notebook_home: Optional[Path] = None) -> Path:
    """Return the base directory for local model config."""
    return _local_dir(notebook_home)


def _local_path(notebook_home: Optional[Path] = None) -> Path:
    return _local(notebook_home) / LOCAL_CONFIG_NAME


def _read_local(notebook_home: Optional[Path] = None) -> Dict:
    p = _local_path(notebook_home)
    if not p.exists():
        return {"models": {}, "active": None}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        logging.getLogger("opennote.auth.local").warning(
            "Local model config '%s' is corrupt; starting fresh.", p
        )
        return {"models": {}, "active": None}
    if not isinstance(data, dict):
        return {"models": {}, "active": None}
    # normalise keys
    models = {str(k): v for k, v in data.get("models", {}).items()}
    active = data.get("active")
    if active and active not in models:
        active = None  # stale active – clear it
    return {"models": models, "active": active}


def _write_local(data: Dict, notebook_home: Optional[Path] = None) -> None:
    from opennote.fsutil import atomic_write_json

    p = _local_path(notebook_home)
    atomic_write_json(p, data, sort_keys=True)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def add_model(
    notebook_home: Optional[Path],
    name: str,
    path: str,
    n_ctx: int = 4096,
    threads: Optional[int] = None,
) -> None:
    """Register a new local GGUF model.

    * ``name`` must match ``^[A-Za-z0-9._-]+$`` (same convention as notebook
      names).
    * ``path`` is resolved to an absolute path; the file must exist and be
      non‑empty.  A warning is emitted if the suffix is not ``.gguf``.
    * If ``active`` is not already set, this model becomes the active one.
    """
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise ValueError(f"Invalid model name '{name}'")
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Model file not found: {path}")
    if os.path.getsize(path) == 0:
        raise ValueError(f"Model file is empty: {path}")

    data = _read_local(notebook_home)
    models = data["models"]
    # warn on non-.gguf suffix (do not reject – the user may have a custom name)
    if not path.lower().endswith(".gguf"):
        logging.getLogger("opennote.auth.local").warning(
            "File '%s' does not have a '.gguf' extension; behaviour may vary.", path
        )
    models[name] = {"path": path, "n_ctx": n_ctx, "threads": threads}
    data["models"] = models
    if data["active"] is None:
        data["active"] = name
    _write_local(data, notebook_home)


def remove_model(notebook_home: Optional[Path], name: str) -> None:
    """Unregister a model.  Does not delete the underlying file."""
    data = _read_local(notebook_home)
    models = data["models"]
    if name in models:
        del models[name]
    data["models"] = models
    # if we removed the active model, clear active
    if data.get("active") == name:
        data["active"] = None
    _write_local(data, notebook_home)


def list_models(notebook_home: Optional[Path] = None) -> List[Dict]:
    """Return a list of ``{"name": ..., "path": ..., "n_ctx": ..., "threads": ...}``."""
    data = _read_local(notebook_home)
    result = []
    for name, meta in data["models"].items():
        result.append({"name": name, **meta})
    return result


def set_active(notebook_home: Optional[Path], name: str) -> None:
    """Make *name* the active model (writes to config)."""
    data = _read_local(notebook_home)
    if name in data["models"]:
        data["active"] = name
        _write_local(data, notebook_home)


def get_active(notebook_home: Optional[Path] = None) -> Optional[Dict]:
    """Return the active model dict (or ``None`` if nothing is active)."""
    data = _read_local(notebook_home)
    name = data.get("active")
    if name and name in data["models"]:
        return {"name": name, **data["models"][name]}
    return None


# --------------------------------------------------------------------------- #
# CLI helper (used by ``opennote local add|list|use|remove``)
# --------------------------------------------------------------------------- #


def _default_n_ctx() -> int:
    """Suggest a reasonable default based on file size (very rough)."""
    # not used by the public API; kept for future CLI niceties
    return 4096


# --------------------------------------------------------------------------- #
# Name‑validation regex – same convention as notebook names
# --------------------------------------------------------------------------- #
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def validate_name(name: str) -> bool:
    return bool(_NAME_RE.fullmatch(name))