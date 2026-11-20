"""Layered, secrets-free configuration — the ``opencode.json`` analogue.

OpenNote keeps API keys in the OS keychain and provider→model selections in
``auth.json``. Everything else that used to be an environment variable or a
hard-coded default now lives in ``opennote.json``:

* default provider / model
* ordered permission rules (see :mod:`opennote.security.permissions`)
* retrieval defaults (top_k, hybrid BM25, context budget)
* theme + keybinds (terminal UI)
* per-agent overrides
* tool-result caps

Files are merged in precedence order (later wins), mirroring opencode:

1. ``~/.opennote/opennote.json``                (global, all projects)
2. ``<root>/opennote.json``                     (project, farthest → closest)
3. ``<root>/.opennote/opennote.json``           (project-local, farthest → closest)

A corrupt file is backed up and skipped — never silently reset and overwritten
(the same discipline as ``auth.json`` / ``transcript.py``). No secrets are ever
stored here.
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from opennote.fsutil import atomic_write_json, walk_worktree_roots

logger = logging.getLogger("opennote.config")

CONFIG_NAME = "opennote.json"


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

@dataclass
class RetrievalConfig:
    top_k: Optional[int] = None  # None → adaptive (engg_choices.md:E2)
    bm25: bool = True
    bm25_alpha: float = 0.5
    context_budget: int = 12000


@dataclass
class ToolOutputConfig:
    max_lines: int = 2000
    max_bytes: int = 51200


@dataclass
class OpenNoteConfig:
    model: Optional[str] = None
    provider: Optional[str] = None
    default_agent: Optional[str] = None
    permissions: List[Dict[str, str]] = field(default_factory=list)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    theme: Optional[str] = None
    keybinds: Dict[str, Any] = field(default_factory=dict)
    tool_output: ToolOutputConfig = field(default_factory=ToolOutputConfig)
    agents: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    #: Global last-used provider/model, written on explicit switches only.
    last_used: Dict[str, str] = field(default_factory=dict)
    #: Declarative provider overrides (model, base_url, env, model list).
    providers: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    #: Highest-precedence file that actually existed (diagnostics / saves).
    source_path: Optional[Path] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize only explicitly-set values — a null here would override a
        value from a lower-precedence file when merged on load."""
        out: Dict[str, Any] = {}
        for key in ("model", "provider", "default_agent", "theme"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.permissions:
            out["permissions"] = list(self.permissions)
        defaults = RetrievalConfig()
        rdiff = {}
        for k in ("top_k", "bm25", "bm25_alpha", "context_budget"):
            if getattr(self.retrieval, k) != getattr(defaults, k):
                rdiff[k] = getattr(self.retrieval, k)
        if rdiff:
            out["retrieval"] = rdiff
        if self.keybinds:
            out["keybinds"] = dict(self.keybinds)
        tdef = ToolOutputConfig()
        tdiff = {}
        for k in ("max_lines", "max_bytes"):
            if getattr(self.tool_output, k) != getattr(tdef, k):
                tdiff[k] = getattr(self.tool_output, k)
        if tdiff:
            out["tool_output"] = tdiff
        if self.agents:
            out["agents"] = {k: dict(v) for k, v in self.agents.items()}
        if self.last_used:
            out["last_used"] = dict(self.last_used)
        if self.providers:
            out["providers"] = {k: dict(v) for k, v in self.providers.items()}
        return out

    @classmethod
    def from_dict(cls, data: Dict[str, Any], path: Optional[Path] = None) -> "OpenNoteConfig":
        data = resolve_env_placeholders(data if isinstance(data, dict) else {})
        r = data.get("retrieval") or {}
        t = data.get("tool_output") or {}
        cfg = cls(
            model=_str_or_none(data.get("model")),
            provider=_str_or_none(data.get("provider")),
            default_agent=_str_or_none(data.get("default_agent")),
            permissions=_rules_list(data.get("permissions")),
            retrieval=RetrievalConfig(
                top_k=_int_or_none(r.get("top_k")),
                bm25=bool(r.get("bm25", True)),
                bm25_alpha=_float(r.get("bm25_alpha"), 0.5),
                context_budget=_int(r.get("context_budget"), 12000),
            ),
            theme=_str_or_none(data.get("theme")),
            keybinds=data.get("keybinds") if isinstance(data.get("keybinds"), dict) else {},
            tool_output=ToolOutputConfig(
                max_lines=_int(t.get("max_lines"), 2000),
                max_bytes=_int(t.get("max_bytes"), 51200),
            ),
            agents=data.get("agents") if isinstance(data.get("agents"), dict) else {},
            last_used=data.get("last_used") if isinstance(data.get("last_used"), dict) else {},
            providers=_providers_dict(data.get("providers")),
            source_path=path,
        )
        return cfg


def _providers_dict(value: Any) -> Dict[str, Dict[str, Any]]:
    """Keep only well-shaped provider entries from config."""
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(value, dict):
        return out
    for pid, entry in value.items():
        if isinstance(entry, dict):
            out[str(pid).strip().lower()] = dict(entry)
    return out


_ENV_VAR_RE = __import__("re").compile(r"^\{env:([A-Za-z_][A-Za-z0-9_]*)\}$")


def resolve_env_placeholders(value: Any) -> Any:
    """Recursively replace ``{env:VAR}`` strings with the variable's value.

    Mirrors opencode's config substitution. A missing variable becomes an empty
    string so a key never silently falls back to a wrong credential.
    """
    if isinstance(value, str):
        m = _ENV_VAR_RE.match(value.strip())
        return os.environ.get(m.group(1), "") if m else value
    if isinstance(value, dict):
        return {k: resolve_env_placeholders(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_env_placeholders(v) for v in value]
    return value


def _str_or_none(value: Any) -> Optional[str]:
    return str(value) if isinstance(value, (str, int)) and str(value).strip() else None


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _int_or_none(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n >= 1 else None


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _rules_list(value: Any) -> List[Dict[str, str]]:
    """Keep only well-shaped ``{action, resource, effect}`` rule dicts."""
    out: List[Dict[str, str]] = []
    if not isinstance(value, list):
        return out
    for item in value:
        if not isinstance(item, dict):
            continue
        action = item.get("action")
        resource = item.get("resource", "*")
        effect = item.get("effect")
        if action is None or effect is None:
            continue
        if str(effect).lower() not in ("allow", "deny", "ask"):
            continue
        out.append(
            {
                "action": str(action),
                "resource": str(resource),
                "effect": str(effect).lower(),
            }
        )
    return out


# ---------------------------------------------------------------------------
# JSONC support (comments + trailing commas), without corrupting string values
# ---------------------------------------------------------------------------

def _strip_jsonc(text: str) -> str:
    """Remove ``//`` / ``/* */`` comments and trailing commas outside strings."""
    out: List[str] = []
    i = 0
    n = len(text)
    in_str = False
    escaped = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] not in "\r\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i = min(i + 2, n)
            continue
        out.append(c)
        i += 1
    # Strip trailing commas before } or ] (comments already gone).
    import re

    cleaned = "".join(out)
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    return cleaned


def _read_config_file(path: Path) -> Dict[str, Any]:
    import json

    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        logger.warning("Cannot read config '%s': %s", path, exc)
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            data = json.loads(_strip_jsonc(raw))
        except json.JSONDecodeError as exc:
            _backup_corrupt(path)
            logger.warning("Config '%s' is corrupt (backed up): %s", path, exc)
            return {}
    if not isinstance(data, dict):
        _backup_corrupt(path)
        logger.warning("Config '%s' must be a JSON object (backed up).", path)
        return {}
    return data


def _backup_corrupt(path: Path) -> None:
    try:
        stamp = int(time.time())
        backup = path.with_suffix(f".json.corrupt.{stamp}")
        n = 1
        while backup.exists():
            n += 1
            backup = path.with_suffix(f".json.corrupt.{stamp}.{n}")
        shutil.copy2(path, backup)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Discovery / merge
# ---------------------------------------------------------------------------

def config_paths(cwd: Optional[Path] = None) -> List[Path]:
    """Return candidate config files in ascending precedence order."""
    cwd = Path(cwd) if cwd is not None else Path.cwd()
    paths: List[Path] = []

    # OPENNOTE_HOME overrides the notebook store; config follows it unless an
    # explicit OPENNOTE_CONFIG_HOME is given (tests isolate via OPENNOTE_HOME).
    global_dir = Path(
        os.environ.get("OPENNOTE_CONFIG_HOME")
        or os.environ.get("OPENNOTE_HOME")
        or (Path.home() / ".opennote")
    )
    paths.append(global_dir / CONFIG_NAME)

    roots = list(reversed(walk_worktree_roots(cwd)))  # farthest → closest
    for root in roots:
        paths.append(root / CONFIG_NAME)
    for root in roots:
        paths.append(root / ".opennote" / CONFIG_NAME)
    return paths


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Merge *override* into *base*; nested dicts merge, other values replace."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(cwd: Optional[Path] = None) -> OpenNoteConfig:
    """Load and merge all config files. Never raises on a bad file."""
    merged: Dict[str, Any] = {}
    winner: Optional[Path] = None
    for path in config_paths(cwd):
        if not path.is_file():
            continue
        merged = _deep_merge(merged, _read_config_file(path))
        winner = path
    return OpenNoteConfig.from_dict(merged, winner)


_cached: Optional[OpenNoteConfig] = None
_cached_env: Optional[str] = None


def get_config() -> OpenNoteConfig:
    """Return the process-wide config snapshot (reload via :func:`clear_cached`).

    The snapshot is invalidated when ``OPENNOTE_HOME`` or ``OPENNOTE_CONFIG_HOME``
    changes, so tests and multi-project processes never read a stale global.
    """
    global _cached, _cached_env
    env_key = (
        os.environ.get("OPENNOTE_CONFIG_HOME", "")
        + "|"
        + os.environ.get("OPENNOTE_HOME", "")
    )
    if _cached is None or _cached_env != env_key:
        _cached = load_config()
        _cached_env = env_key
    return _cached


def clear_cached() -> None:
    global _cached, _cached_env
    _cached = None
    _cached_env = None


def project_config_path(cwd: Optional[Path] = None) -> Path:
    """Where ``save_config`` / approval saves write by default."""
    cwd = Path(cwd) if cwd is not None else Path.cwd()
    return cwd / ".opennote" / CONFIG_NAME


def global_config_path() -> Path:
    """User-global config path (follows OPENNOTE_HOME/CONFIG_HOME)."""
    return config_paths()[0]


def save_config(cfg: OpenNoteConfig, path: Optional[Path] = None) -> Path:
    """Atomically write *cfg* to *path* (default: project config)."""
    target = Path(path) if path else project_config_path()
    atomic_write_json(target, cfg.to_dict())
    if path is None:
        clear_cached()
    return target


def save_permission_rule(rule: Dict[str, str], cwd: Optional[Path] = None) -> Path:
    """Persist an 'allow always' approval as a project-scoped rule."""
    path = project_config_path(cwd)
    cfg = load_config(cwd)
    rules = cfg.permissions
    key = (rule.get("action"), rule.get("resource"), rule.get("effect"))
    if not any((r.get("action"), r.get("resource"), r.get("effect")) == key for r in rules):
        rules.append(dict(rule))
    cfg.permissions = rules
    atomic_write_json(path, cfg.to_dict())
    clear_cached()
    return path
