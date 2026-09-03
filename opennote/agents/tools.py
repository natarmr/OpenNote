"""Tool definitions and dispatch for the agentic retrieval loop.

Each core tool returns a list of ``SearchResult`` objects — the same type used
by ``opennote.ask`` — so that the model's inline ``[n]`` markers are
automatically validated against the actual chunks.

Extended tools (skill, run_skill_script, task, plugin tools) are registered
dynamically via ToolContext / PluginLoader / SkillRegistry. ``execute_tool``
remains backward-compatible with the old ``(name, retriever, kwargs)``
signature while also accepting ``(name, ToolContext, kwargs)``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from opennote.retrieval.retriever import Retriever, SearchResult


# ---------------------------------------------------------------------------
# JSON‑schema definitions (OpenAI "functions" / Anthropic "input_schema")
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: Dict[str, dict] = {
    "search": {
        "description": "Retrieve top‑k chunks for a free‑text query, optionally restricted to a single source filename.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
                "top_k": {
                    "type": "integer",
                    "description": "Number of chunks to return (default: 5).",
                    "default": 5,
                },
                "source": {"type": "string", "description": "Restrict results to this filename."},
            },
            "required": ["query"],
        },
    },
    "list_sources": {
        "description": "List all source filenames currently indexed in the notebook.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    "web_search": {
        "description": "Retrieve top-k chunks for a free-text query using Tavily web search.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
                "top_k": {
                    "type": "integer",
                    "description": "Number of chunks to return (default: 5).",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    "read_page": {
        "description": "Fetch a web page URL and return its chunked text for detailed reading.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The http(s) URL to fetch."},
            },
            "required": ["url"],
        },
    },
    "submit_grounded_answer": {
        "description": "Submit a grounded answer with claims tied to sources. Use ONLY this to answer; each claim must have source_ids and an exact quote_span from a source.",
        "parameters": {
            "type": "object",
            "properties": {
                "claims": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "description": "Single grounded claim"},
                            "source_ids": {"type": "array", "items": {"type": "string"}, "description": "Source ids like '1','2'"},
                            "quote_span": {"type": "string", "description": "Exact substring from the source"},
                        },
                        "required": ["text", "source_ids", "quote_span"],
                    },
                },
                "summary": {"type": "string", "description": "Optional summary grounded in claims"},
            },
            "required": ["claims"],
        },
    },
}


# ---------------------------------------------------------------------------
# ToolContext — decouples tools from bare Retriever (enables skills/plugins/agents)
# ---------------------------------------------------------------------------

@dataclass
class ToolContext:
    """Context passed to tool dispatch — carries everything a tool may need."""

    retriever: Optional[Retriever] = None
    notebook: Optional[Any] = None
    capabilities: Optional[Any] = None
    artifacts_dir: Optional[Path] = None
    # Injected registries (set by loop.py)
    skill_registry: Optional[Any] = None
    plugin_loader: Optional[Any] = None
    agent_registry: Optional[Any] = None
    # For task tool recursion
    client: Optional[Any] = None
    history: Optional[List[Dict[str, Any]]] = None

    @classmethod
    def from_retriever(cls, retriever: Retriever) -> "ToolContext":
        return cls(retriever=retriever, notebook=getattr(retriever, "notebook", None))


def _as_context(obj: Any) -> ToolContext:
    if isinstance(obj, ToolContext):
        return obj
    # Treat as Retriever (including FakeRetriever in tests — duck-typed)
    if obj is None:
        return ToolContext()
    # Any object with .search or .sources is considered a retriever-like
    return ToolContext.from_retriever(obj)


def _resolve_retriever(ctx: ToolContext) -> Optional[Retriever]:
    return ctx.retriever


# ---------------------------------------------------------------------------
# Dispatch implementation — core tools
# ---------------------------------------------------------------------------

def _search(
    retriever: Retriever,
    query: str,
    top_k: Any = 5,
    source: Optional[str] = None,
) -> List[SearchResult]:
    """Retrieve top‑k chunks for *query* on *retriever*, optionally filtered by *source*."""
    try:
        top_k = int(top_k) if top_k is not None else 5
    except (TypeError, ValueError):
        raise ValueError(f"top_k must be an integer, got {top_k!r}.")
    if top_k < 1:
        raise ValueError(f"top_k must be >= 1, got {top_k}.")
    if top_k > 25:
        raise ValueError(f"top_k must be <= 25, got {top_k}.")
    if not query or not str(query).strip():
        raise ValueError("search requires a non-empty 'query' string.")
    if source is not None:
        available = retriever.sources()
        if available and source not in available:
            raise ValueError(
                f"Source '{source}' not found. Available sources: {', '.join(sorted(available))}"
            )
    return retriever.search(query, top_k=top_k, source=source)


def _list_sources(retriever: Retriever) -> List[str]:
    """Return the filenames of all sources currently indexed in *retriever*."""
    return retriever.sources()


def _web_search(
    retriever: Retriever,
    query: str,
    top_k: Any = 5,
) -> List[SearchResult]:
    """Retrieve top‑k chunks for *query* via Tavily web search."""
    try:
        top_k = int(top_k) if top_k is not None else 5
    except (TypeError, ValueError):
        raise ValueError(f"top_k must be an integer, got {top_k!r}.")
    if top_k < 1:
        raise ValueError(f"top_k must be >= 1, got {top_k}.")
    if top_k > 25:
        raise ValueError(f"top_k must be <= 25, got {top_k}.")
    if not query or not str(query).strip():
        raise ValueError("web_search requires a non-empty 'query' string.")
    from opennote.websearch import web_search as _ws
    return _ws(query, top_k=top_k)


def _read_page(retriever: Retriever, url: str) -> List[SearchResult]:
    """Fetch *url* and return chunked SearchResults."""
    if not url or not str(url).strip():
        raise ValueError("read_page requires a non-empty 'url' string.")
    url = str(url).strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("read_page requires an http(s) URL.")
    if any(c in url for c in [";", "|", "&", "`", "$", "\n", "\r"]):
        raise ValueError("URL contains invalid characters.")
    from opennote.websearch import read_page as _rp

    return _rp(url)


def _submit_grounded_answer(retriever: Retriever, claims=None, summary=None):
    return {"claims": claims or [], "summary": summary}


# ---------------------------------------------------------------------------
# Skill tools
# ---------------------------------------------------------------------------

def _build_skill_schema(registry: Any) -> Dict[str, Any]:
    """Build the ``skill`` tool schema with dynamic description."""
    base_desc = "Load an installed agent skill by name. Skills are reusable instruction sets (SKILL.md) that extend your capabilities."
    if registry is not None and not registry.is_empty():
        xml = registry.available_skills_xml()
        desc = f"{base_desc}\n\n{xml}\n\nCall skill with the name of the skill to use when its description matches the task."
    else:
        desc = base_desc + " No skills are currently installed."

    return {
        "description": desc,
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name (e.g. 'diagrams')."},
            },
            "required": ["name"],
        },
    }


def _skill_execute(ctx: ToolContext, name: str) -> str:
    if not name or not str(name).strip():
        raise ValueError("skill requires a non-empty 'name' string.")
    name = str(name).strip()
    registry = ctx.skill_registry
    if registry is None:
        # Lazy discover if not injected (fallback for direct execute_tool calls)
        try:
            from opennote.skills.registry import SkillRegistry
            registry = SkillRegistry.discover()
        except Exception:
            raise ValueError("No skills are available.")
    skill = registry.get(name) if registry else None
    if skill is None:
        available = ", ".join(registry.names()) if registry and not registry.is_empty() else "none"
        raise ValueError(f"Skill '{name}' not found. Available: {available}")

    # Build response: frontmatter summary + body + bundled file manifest
    parts: List[str] = []
    parts.append(f"# Skill: {skill.name}")
    parts.append(f"Description: {skill.description}")
    if skill.frontmatter.get("license"):
        parts.append(f"License: {skill.frontmatter['license']}")
    if skill.frontmatter.get("compatibility"):
        parts.append(f"Compatibility: {skill.frontmatter['compatibility']}")
    parts.append("")
    parts.append(skill.body)
    if skill.files:
        parts.append("\n---\nBundled files:")
        for f in skill.files[:50]:
            parts.append(f"  - {f}")
        if len(skill.files) > 50:
            parts.append(f"  ... (+{len(skill.files)-50} more)")
    parts.append(f"\nSkill directory: {skill.directory}")
    return "\n".join(parts)


def _build_run_skill_script_schema() -> Dict[str, Any]:
    return {
        "description": "Run a script bundled with an installed skill (e.g. diagrams' diagram_prompt.py). The script path must be inside the skill's directory. Requires OPENNOTE_ALLOW_SKILL_SCRIPTS=1.",
        "parameters": {
            "type": "object",
            "properties": {
                "skill": {"type": "string", "description": "Skill name that owns the script."},
                "script": {"type": "string", "description": "Relative path to the script inside the skill dir (e.g. 'scripts/diagram_prompt.py')."},
                "args": {"type": "array", "items": {"type": "string"}, "description": "Arguments to pass to the script."},
            },
            "required": ["skill", "script"],
        },
    }


def _run_skill_script_execute(ctx: ToolContext, skill: str, script: str, args: Optional[List[str]] = None) -> str:
    if not os.environ.get("OPENNOTE_ALLOW_SKILL_SCRIPTS", "").lower() in ("1", "true", "yes", "on"):
        raise ValueError("Skill script execution is disabled. Set OPENNOTE_ALLOW_SKILL_SCRIPTS=1 to enable.")
    if not skill or not str(skill).strip():
        raise ValueError("run_skill_script requires a non-empty 'skill' string.")
    if not script or not str(script).strip():
        raise ValueError("run_skill_script requires a non-empty 'script' string.")
    skill = str(skill).strip()
    script = str(script).strip()
    args = list(args or [])

    registry = ctx.skill_registry
    if registry is None:
        try:
            from opennote.skills.registry import SkillRegistry
            registry = SkillRegistry.discover()
        except Exception as exc:
            raise ValueError(f"Cannot load skill registry: {exc}") from exc
    sk = registry.get(skill) if registry else None
    if sk is None:
        raise ValueError(f"Skill '{skill}' not found.")

    # Resolve and confine script path
    skill_dir = sk.directory.resolve()
    target = (skill_dir / script).resolve()
    # Path confinement: must be inside skill_dir
    try:
        target.relative_to(skill_dir)
    except ValueError:
        raise ValueError(f"Script path '{script}' escapes the skill directory.")
    if not target.is_file():
        raise ValueError(f"Script '{script}' not found in skill '{skill}'.")

    # Validate args: no shell injection; args are passed as argv, not shell string
    for a in args:
        if not isinstance(a, str):
            raise ValueError(f"Argument {a!r} must be a string.")

    cmd = [sys.executable, str(target), *args]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(skill_dir),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        raise ValueError(f"Script '{script}' timed out after 60s.")
    except Exception as exc:
        raise ValueError(f"Failed to run script '{script}': {exc}") from exc

    output_parts: List[str] = []
    if result.stdout:
        # Cap output at 20k chars
        out = result.stdout
        if len(out) > 20000:
            out = out[:20000] + f"\n... (truncated, total {len(result.stdout)} chars)"
        output_parts.append(out)
    if result.stderr:
        err = result.stderr
        if len(err) > 5000:
            err = err[:5000] + " ... (stderr truncated)"
        output_parts.append(f"[stderr]\n{err}")
    if result.returncode != 0:
        output_parts.append(f"[exit code {result.returncode}]")
    if not output_parts:
        return "(script produced no output)"
    return "\n".join(output_parts)


# ---------------------------------------------------------------------------
# Task tool (subagents)
# ---------------------------------------------------------------------------

def _build_task_schema(agent_registry: Any) -> Dict[str, Any]:
    desc = "Delegate a sub-task to a specialized subagent. The subagent runs its own retrieval loop and returns its answer."
    if agent_registry is not None:
        subs = [a for a in agent_registry.list(mode="subagent") if not a.hidden]
        if subs:
            names = ", ".join(f"{a.name} ({a.description[:60]})" for a in subs)
            desc += f" Available subagents: {names}."
    return {
        "description": desc,
        "parameters": {
            "type": "object",
            "properties": {
                "subagent": {"type": "string", "description": "Subagent name (e.g. 'explore', 'general')."},
                "task": {"type": "string", "description": "Task/prompt for the subagent."},
            },
            "required": ["subagent", "task"],
        },
    }


def _task_execute(ctx: ToolContext, subagent: str, task: str) -> str:
    if not subagent or not str(subagent).strip():
        raise ValueError("task requires a non-empty 'subagent' string.")
    if not task or not str(task).strip():
        raise ValueError("task requires a non-empty 'task' string.")
    subagent = str(subagent).strip()
    task = str(task).strip()

    # Resolve agent def
    areg = ctx.agent_registry
    if areg is None:
        try:
            from opennote.agents.defs import AgentRegistry
            areg = AgentRegistry.discover()
        except Exception:
            areg = None
    if areg is None:
        raise ValueError("No agent registry available.")
    agent_def = areg.get(subagent)
    if agent_def is None:
        available = ", ".join(areg.names()) if areg else "none"
        raise ValueError(f"Subagent '{subagent}' not found. Available: {available}")
    if agent_def.mode not in ("subagent", "all"):
        raise ValueError(f"Agent '{subagent}' is not a subagent (mode={agent_def.mode}).")

    # Need notebook + retriever + client to spawn subagent
    notebook = ctx.notebook
    retriever = ctx.retriever
    if notebook is None or retriever is None:
        raise ValueError("task tool requires a notebook and retriever context.")

    # Build a focused system prompt addition from agent def
    # We spawn a nested agent_turn with a modified system prompt prefix
    from opennote.agents.loop import agent_turn as _agent_turn

    # Use the same client if available, else default
    client = ctx.client

    # History for subagent: we pass a minimal history (not the full parent history)
    # to keep subagent focused
    try:
        result = _agent_turn(
            notebook=notebook,
            question=task,
            retriever=retriever,
            client=client,
            max_rounds=3,  # subagents get fewer rounds
            history=[],
        )
        # Merge subagent retrieved chunks into parent context? The parent loop
        # will handle this by appending subagent result text. For grounding:
        # we include citation footer if subagent had sources, and we store
        # subagent's retrieved list on the context for parent to merge.
        # Simplest: return text that includes sources footer if any.
        answer = result.result.answer
        # Store retrieved for parent loop to merge (side-channel via ctx)
        if not hasattr(ctx, "_subagent_retrieved"):
            ctx._subagent_retrieved = []  # type: ignore[attr-defined]
        ctx._subagent_retrieved.extend(result.result.results)  # type: ignore[attr-defined]
        return answer
    except Exception as exc:
        raise ValueError(f"Subagent '{subagent}' failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Core dispatch map (static)
# ---------------------------------------------------------------------------

_TOOL_DISPATCH: Dict[str, Any] = {
    "search": _search,
    "list_sources": _list_sources,
    "web_search": _web_search,
    "read_page": _read_page,
    "submit_grounded_answer": _submit_grounded_answer,
}

# Dynamic dispatch for context-aware tools (skill, run_skill_script, task, plugin tools)
_CTX_DISPATCH: Dict[str, Any] = {
    "skill": _skill_execute,
    "run_skill_script": _run_skill_script_execute,
    "task": _task_execute,
}


def _get_dynamic_schemas(ctx: ToolContext) -> Dict[str, Dict[str, Any]]:
    """Build dynamic schemas for skill/plugin/task tools based on context."""
    schemas: Dict[str, Dict[str, Any]] = {}

    # Skill tool — always offer if skills exist (or even if none, show empty)
    try:
        reg = ctx.skill_registry
        if reg is None:
            from opennote.skills.registry import SkillRegistry
            reg = SkillRegistry.discover()
            ctx.skill_registry = reg
        # Only advertise skill tool if at least one skill exists, OR always?
        # Always advertise — description will say "No skills installed" when empty
        schemas["skill"] = _build_skill_schema(reg)
    except Exception:
        pass

    # run_skill_script — only when allowed
    if os.environ.get("OPENNOTE_ALLOW_SKILL_SCRIPTS", "").lower() in ("1", "true", "yes", "on"):
        schemas["run_skill_script"] = _build_run_skill_script_schema()

    # Task tool — when agent registry has subagents
    try:
        areg = ctx.agent_registry
        if areg is None:
            from opennote.agents.defs import AgentRegistry
            areg = AgentRegistry.discover()
            ctx.agent_registry = areg
        subs = [a for a in areg.list(mode="subagent") if not a.hidden]
        if subs:
            schemas["task"] = _build_task_schema(areg)
    except Exception:
        pass

    # Plugin tools
    try:
        loader = ctx.plugin_loader
        if loader is None:
            from opennote.plugins.loader import PluginLoader, PluginContext
            loader = PluginLoader(PluginContext(capabilities=ctx.capabilities, notebook=ctx.notebook))
            loader.load()
            ctx.plugin_loader = loader
        for tname, tschema in loader.get_tool_schemas().items():
            if tname not in schemas:
                schemas[tname] = tschema
    except Exception:
        pass

    return schemas


def _get_all_schemas(ctx: Optional[ToolContext] = None) -> Dict[str, Dict[str, Any]]:
    """Return merged schemas (core + dynamic) for a given context."""
    all_schemas = dict(TOOL_SCHEMAS)
    if ctx is not None:
        all_schemas.update(_get_dynamic_schemas(ctx))
    return all_schemas


def get_tool_schemas(ctx: Optional[ToolContext] = None) -> Dict[str, Dict[str, Any]]:
    """Public helper — return schemas available for *ctx* (or core only if ctx is None)."""
    return _get_all_schemas(ctx)


def execute_tool(
    tool_name: str, retriever: Any, kwargs: Optional[Dict[str, Any]]
) -> Any:
    """Execute *tool_name* on *retriever* (or ToolContext) with *kwargs*.

    Backward-compatible: ``retriever`` may be a ``Retriever``/``FakeRetriever``
    (old call sites) or a ``ToolContext`` (new call sites). Unknown extra
    kwargs are dropped. Raises ``ValueError`` on unknown tool / missing args.
    """
    # Normalize to ToolContext
    ctx = _as_context(retriever)
    actual_retriever = _resolve_retriever(ctx)

    # Build merged schema map for validation
    all_schemas = dict(TOOL_SCHEMAS)
    # Include dynamic schemas if ctx has registries or we can lazy-load them
    try:
        all_schemas.update(_get_dynamic_schemas(ctx))
    except Exception:
        pass

    schema = all_schemas.get(tool_name)
    if schema is None:
        raise ValueError(f"Unknown tool: {tool_name}")

    kwargs = dict(kwargs or {})
    allowed = set(schema.get("parameters", {}).get("properties", {}))
    extra = set(kwargs) - allowed
    if extra:
        for key in extra:
            kwargs.pop(key)

    required = schema.get("parameters", {}).get("required", [])
    for arg in required:
        if arg not in kwargs:
            raise ValueError(f"Missing required argument '{arg}' for tool {tool_name}")

    # Try context-aware dispatch first
    ctx_fn = _CTX_DISPATCH.get(tool_name)
    if ctx_fn is not None:
        return ctx_fn(ctx, **kwargs)

    # Plugin dispatch
    try:
        loader = ctx.plugin_loader
        if loader is None:
            from opennote.plugins.loader import PluginLoader, PluginContext
            loader = PluginLoader(PluginContext(capabilities=ctx.capabilities, notebook=ctx.notebook))
            loader.load()
            ctx.plugin_loader = loader
        pfn = loader.get_dispatch(tool_name)
        if pfn is not None:
            # Plugin tools receive (ToolContext, **kwargs)
            return pfn(ctx, **kwargs)
    except Exception:
        pass

    # Core dispatch — needs a retriever
    func = _TOOL_DISPATCH.get(tool_name)
    if func is None:
        raise ValueError(f"Tool {tool_name} not implemented")

    # Core tools that need retriever: ensure we have one
    if tool_name in ("search", "list_sources", "web_search", "read_page", "submit_grounded_answer"):
        if actual_retriever is None:
            # For tests that pass ToolContext with no retriever but call non-retrieval tools?
            # For these tools we need a retriever; raise a clear error
            raise ValueError(f"Tool {tool_name} requires a retriever/notebook context.")

    return func(actual_retriever, **kwargs)


# ---------------------------------------------------------------------------
# Rendering helpers – turn SearchResult objects into model‑friendly text
# ---------------------------------------------------------------------------

def render_tool_results(results: List[SearchResult], max_lines: int = 6, offset: int = 0) -> str:
    """Render *results* as a numbered block the model can reference with ``[n]`` markers.

    *offset* shifts the numbering (e.g. to keep indices globally unique across
    multiple ``search`` calls in one turn, matching the flat ``retrieved``
    list used for citation validation).
    """
    lines: List[str] = []
    for i, r in enumerate(results, start=1):
        idx = offset + i
        lines.append(f"[{idx}] {r.citation}")
        content_lines = r.content.strip().splitlines()
        display = content_lines[:max_lines]
        if len(content_lines) > max_lines:
            display.append(f"... (+{len(content_lines) - max_lines} more lines)")
        lines.extend(display)
        lines.append("")
    return "\n".join(lines)
