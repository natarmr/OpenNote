"""Single source of truth for provider/model selection.

Every startup and switch path goes through :func:`resolve_selection`, so the
question "which provider and model will be used?" has exactly one answer and
one documented precedence order:

1. explicit overrides (CLI ``--provider`` / ``--model``, agent frontmatter)
2. the notebook's saved session selection (opencode: a session keeps its model)
3. the global last-used selection (persisted in ``opennote.json``)
4. the configured default (``model: provider/model`` or ``provider:``)
5. the most recently validated usable provider (never alphabetical)

Switching model never rewrites the configured default — that mirrors opencode.
Use ``--make-default`` or ``set_default_selection`` for that explicit act.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

from opennote.auth.keychain import resolve_key
from opennote.auth.registry import get_provider, all_providers
from opennote.chat.client import ChatError

#: Source labels shown in ``opennote config --effective`` and the TUI sidebar.
SOURCE_FLAG = "flag"
SOURCE_SESSION = "session"
SOURCE_RECENT = "recent"
SOURCE_CONFIG = "config"
SOURCE_DISCOVERY = "discovery"


@dataclass
class Selection:
    provider: str
    model: Optional[str]
    source: str
    #: True when this selection came from an explicit override and must win.
    explicit: bool = False

    @property
    def label(self) -> str:
        return f"{self.provider}/{self.model}" if self.model else self.provider


def _configured_default() -> Selection:
    """Translate ``opennote.json`` ``model``/``provider`` into a selection."""
    try:
        from opennote.config import get_config

        cfg = get_config()
    except Exception:
        return Selection("", None, SOURCE_CONFIG)
    raw = cfg.model or ""
    provider = ""
    model = ""
    if raw:
        if "/" in raw:
            provider, model = raw.split("/", 1)
        else:
            model = raw
    if not provider:
        provider = cfg.provider or ""
    if "/" in provider:  # tolerate "provider/model" in the provider field
        provider, from_provider = provider.split("/", 1)
        model = model or from_provider
    return Selection(provider.strip().lower(), model.strip() or None, SOURCE_CONFIG)


def usable_providers() -> List[str]:
    """Providers with a resolvable key and a stored/default model, usable now."""
    from opennote.auth.config import AuthConfig

    config = AuthConfig()
    ids = [p.id for p in all_providers()]
    out = []
    for pid in ids:
        if not resolve_key(pid):  # module-level symbol (monkeypatchable)
            continue
        settings = config.get(pid)
        if settings and settings.model:
            out.append(pid)
    from opennote.auth.local import get_active as _local_active

    if _local_active():
        out.append("local")
    return out


def resolve_selection(
    *,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    notebook: Any = None,
) -> Selection:
    """Resolve the effective provider/model from all sources (see module docstring).

    ``provider``/``model`` are explicit overrides (flags or agent frontmatter).
    ``notebook`` contributes its saved session selection when present.
    """
    # 1. explicit flags
    if provider:
        pid = str(provider).strip().lower()
        if pid and pid != "local":
            get_provider(pid)  # validate id/alias; raises ValueError
        return Selection(pid, str(model).strip() if model else None, SOURCE_FLAG, explicit=True)

    # 2. notebook session selection (global last-used is NOT notebook-specific)
    if notebook is not None:
        np = getattr(notebook, "provider_id", None)
        nm = getattr(notebook, "model", None)
        if np:
            return Selection(str(np).strip().lower(), str(nm).strip() if nm else None, SOURCE_SESSION)

    # 3. global last-used selection
    try:
        from opennote.config import get_config

        recent = get_config().last_used or {}
        rp = str(recent.get("provider") or "").strip().lower()
        if rp:
            rm = recent.get("model")
            return Selection(rp, str(rm).strip() if rm else None, SOURCE_RECENT)
    except Exception:
        pass

    # 4. configured default
    configured = _configured_default()
    if configured.provider:
        usable = usable_providers()
        if configured.provider in usable or configured.provider == "local":
            return configured
        # Configured but not usable right now — keep the explicit choice visible;
        # get_client will raise a helpful error with setup instructions.

    # 5. most recently validated usable provider (fallback, never alphabetical)
    from opennote.auth.config import AuthConfig

    config = AuthConfig()
    candidates = sorted(
        usable_providers(),
        key=lambda pid: (config.get(pid).last_validated_at or "", pid),
        reverse=True,
    )
    if candidates:
        pid = candidates[0]
        settings = config.get(pid)
        return Selection(pid, settings.model if settings else None, SOURCE_DISCOVERY)
    # No usable provider via config/auth. Local GGUF still counts as a provider.
    from opennote.auth.local import get_active as _local_active

    if _local_active():
        return Selection("local", None, SOURCE_DISCOVERY)
    raise ChatError(
        "No provider is configured with a key and a model. "
        "Run 'opennote auth add <provider>' first."
    )


def set_last_used(provider: str, model: Optional[str]) -> None:
    """Persist the global last-used selection into ``opennote.json``.

    Only called on an *explicit* user switch (CLI flag, /model, /connect, model
    picker). A plain answer turn does not move the selection.
    """
    from opennote.config import get_config, global_config_path, save_config

    cfg = get_config()
    cfg.last_used = {"provider": provider, "model": model}
    save_config(cfg, path=global_config_path())
