"""Plugin loader — discover + import Python plugin modules."""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from opennote.notebooks import default_home

logger = logging.getLogger("opennote.plugins")


@dataclass
class PluginContext:
    """Context passed to each plugin's register() call."""

    capabilities: Any = None
    notebook: Any = None
    logger: Any = None  # logging.Logger

    def __post_init__(self):
        if self.logger is None:
            self.logger = logger

    def httpx_client(self, **kwargs):
        """Factory for httpx.Client — plugins should use this (no new deps)."""
        import httpx

        return httpx.Client(**kwargs)


@dataclass
class PluginHooks:
    """Hooks returned by a plugin."""

    # tools: {name: {description, parameters, execute: callable}}
    tools: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    on_turn_complete: Optional[Callable] = None
    on_ingest_complete: Optional[Callable] = None
    tool_execute_before: Optional[Callable] = None
    tool_execute_after: Optional[Callable] = None
    # Raw plugin module reference (for debugging)
    _module: Any = None
    _name: str = ""


def _plugin_dirs(cwd: Path | None = None) -> List[Path]:
    from opennote.fsutil import walk_worktree_roots

    cwd = Path(cwd) if cwd is not None else Path.cwd()
    dirs: List[Path] = []
    for ancestor in walk_worktree_roots(cwd):
        dirs.append(ancestor / ".opennote" / "plugins")
    home = default_home()
    dirs.append(home / "plugins")
    dirs.append(Path.home() / ".config" / "opennote" / "plugins")
    # Dedupe on resolved path
    uniq: List[Path] = []
    seen_s: set[str] = set()
    for d in dirs:
        try:
            k = str(d.resolve())
        except (OSError, RuntimeError):
            k = str(d)
        if k not in seen_s:
            seen_s.add(k)
            uniq.append(d)
    return uniq


def _stable_hash(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:8]


def _import_file(path: Path, module_name: str):
    """Import a .py file as a module."""
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create spec for {path}")
    mod = importlib.util.module_from_spec(spec)
    # Ensure parent package semantics don't break
    sys.modules[module_name] = mod
    try:
        spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return mod


class PluginLoader:
    """Discovers and loads plugins; collects tool registrations and hooks."""

    def __init__(self, ctx: Optional[PluginContext] = None):
        self.ctx = ctx or PluginContext(logger=logger)
        self.hooks: List[PluginHooks] = []
        self.tools: Dict[str, Dict[str, Any]] = {}  # merged
        self._dispatch: Dict[str, Callable] = {}  # name -> execute fn

    def load(self, cwd: Path | None = None) -> "PluginLoader":
        """Discover and load all plugins; isolated failures are logged and skipped."""
        # 0. Built-ins first (lowest priority — user plugins override)
        self._load_builtin()
        # 1. File-based plugins
        for pdir in _plugin_dirs(cwd):
            if not pdir.is_dir():
                continue
            try:
                for entry in sorted(pdir.iterdir()):
                    if entry.is_file() and entry.suffix == ".py" and not entry.name.startswith("_"):
                        mod_name = f"opennote.plugins._file_{entry.stem}_{_stable_hash(str(entry))}"
                        try:
                            mod = _import_file(entry, mod_name)
                            self._register_module(mod, entry.stem)
                        except Exception as exc:
                            logger.warning("Skipping plugin %s: %s", entry, exc)
                            continue
                    elif entry.is_dir() and (entry / "__init__.py").exists():
                        init = entry / "__init__.py"
                        mod_name = f"opennote.plugins._pkg_{entry.name}_{_stable_hash(str(entry))}"
                        try:
                            mod = _import_file(init, mod_name)
                            self._register_module(mod, entry.name)
                        except Exception as exc:
                            logger.warning("Skipping plugin pkg %s: %s", entry, exc)
                            continue
            except OSError as exc:
                logger.debug("Plugin dir scan failed %s: %s", pdir, exc)

        # 2. Entry-point plugins (pip-installed)
        try:
            eps = importlib.metadata.entry_points()
            # Python 3.10+ compat: select(group=...)
            if hasattr(eps, "select"):
                candidates = eps.select(group="opennote.plugins")
            else:
                candidates = eps.get("opennote.plugins", [])  # type: ignore[call-arg]
            for ep in candidates:
                try:
                    mod = ep.load()
                    # ep.load() may return a module or a callable; normalize
                    if callable(mod) and not hasattr(mod, "register"):
                        # It's a register function directly
                        fake_mod = type(sys)("ep_" + ep.name)
                        fake_mod.register = mod  # type: ignore[attr-defined]
                        self._register_module(fake_mod, ep.name)
                    elif hasattr(mod, "register"):
                        self._register_module(mod, ep.name)
                    else:
                        # Try calling it
                        logger.debug("Entry point %s has no register attr, skipping", ep.name)
                except Exception as exc:
                    logger.warning("Skipping entry-point plugin %s: %s", ep.name, exc)
        except Exception as exc:
            logger.debug("Entry-point discovery failed: %s", exc)

        return self

    def _register_module(self, mod: Any, name: str) -> None:
        """Call the plugin's register() and merge hooks."""
        fn = None
        if hasattr(mod, "register") and callable(getattr(mod, "register")):
            fn = getattr(mod, "register")
        elif hasattr(mod, "Plugin") and callable(getattr(mod, "Plugin")):
            # Class-based: Plugin(ctx) -> hooks
            fn = getattr(mod, "Plugin")
        elif hasattr(mod, "hooks") and isinstance(getattr(mod, "hooks"), dict):
            # Static hooks dict
            raw = getattr(mod, "hooks")
            hooks = PluginHooks(_module=mod, _name=name)
            hooks.tools = raw.get("tools", {}) or {}
            hooks.on_turn_complete = raw.get("on_turn_complete")
            hooks.on_ingest_complete = raw.get("on_ingest_complete")
            hooks.tool_execute_before = raw.get("tool_execute_before")
            hooks.tool_execute_after = raw.get("tool_execute_after")
            self._merge_hooks(hooks)
            return
        else:
            logger.debug("Plugin %s has no register() or hooks, skipping", name)
            return

        try:
            result = fn(self.ctx)
            # Support async register? Not needed for Python sync plugins
            import inspect

            if inspect.isawaitable(result):
                logger.warning("Plugin %s register() returned awaitable — async plugins not supported, skipping", name)
                return
        except Exception as exc:
            logger.warning("Plugin %s register() failed: %s", name, exc)
            return

        if result is None:
            return
        # Normalize result
        hooks = PluginHooks(_module=mod, _name=name)
        if isinstance(result, dict):
            hooks.tools = result.get("tools", {}) or result.get("tool", {}) or {}
            hooks.on_turn_complete = result.get("on_turn_complete") or result.get("event")
            hooks.on_ingest_complete = result.get("on_ingest_complete")
            hooks.tool_execute_before = result.get("tool_execute_before") or result.get("tool.execute.before")
            hooks.tool_execute_after = result.get("tool_execute_after") or result.get("tool.execute.after")
        elif hasattr(result, "tools"):
            hooks.tools = getattr(result, "tools", {}) or {}
            hooks.on_turn_complete = getattr(result, "on_turn_complete", None)
            hooks.on_ingest_complete = getattr(result, "on_ingest_complete", None)
            hooks.tool_execute_before = getattr(result, "tool_execute_before", None)
            hooks.tool_execute_after = getattr(result, "tool_execute_after", None)
        else:
            logger.debug("Plugin %s returned unrecognized hooks type %s", name, type(result))
            return
        self._merge_hooks(hooks)

    def _merge_hooks(self, hooks: PluginHooks) -> None:
        # Merge tools (later plugins override earlier on name conflict — intentional: user project overrides global)
        for tname, tdef in hooks.tools.items():
            if not isinstance(tdef, dict):
                logger.warning("Plugin %s tool %s has non-dict def, skipping", hooks._name, tname)
                continue
            # Normalize: expect {description, parameters, execute}
            execute = tdef.get("execute") or tdef.get("handler") or tdef.get("fn")
            if not callable(execute):
                logger.warning("Plugin %s tool %s has no callable execute, skipping", hooks._name, tname)
                continue
            # Keep the schema part
            self.tools[tname] = {k: v for k, v in tdef.items() if k != "execute"}
            # Ensure minimal schema shape
            if "description" not in self.tools[tname]:
                self.tools[tname]["description"] = f"Plugin tool {tname} from {hooks._name}"
            if "parameters" not in self.tools[tname]:
                self.tools[tname]["parameters"] = {"type": "object", "properties": {}, "required": []}
            self._dispatch[tname] = execute
        self.hooks.append(hooks)

    def _load_builtin(self) -> None:
        """Load built-in plugins that are gated by environment."""
        # Supermemory — ships in-repo, gated on SUPERMEMORY_API_KEY
        try:
            from opennote.plugins.builtin.supermemory import register as sm_register

            # Only register if key is present (capability probe also gates tool visibility)
            import os

            if os.environ.get("SUPERMEMORY_API_KEY"):
                fake_mod = type(sys)("builtin_supermemory")
                fake_mod.register = sm_register  # type: ignore[attr-defined]
                self._register_module(fake_mod, "supermemory")
        except Exception as exc:
            logger.debug("Builtin supermemory load skipped: %s", exc)

    def get_tool_schemas(self) -> Dict[str, Dict[str, Any]]:
        return dict(self.tools)

    def get_dispatch(self, name: str) -> Optional[Callable]:
        return self._dispatch.get(name)

    def fire_on_turn_complete(self, result: Any) -> None:
        for h in self.hooks:
            if h.on_turn_complete:
                try:
                    h.on_turn_complete(result)
                except Exception as exc:
                    logger.warning("Plugin %s on_turn_complete failed: %s", h._name, exc)
