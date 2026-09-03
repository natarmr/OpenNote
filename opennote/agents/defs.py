"""Agent definitions — markdown files with YAML frontmatter (opencode-style).

Locations:
  - .opennote/agents/*.md  (project, walk up from cwd)
  - ~/.opennote/agents/*.md (global, respects OPENNOTE_HOME)
  - ~/.config/opennote/agents/*.md (XDG global)

Format (frontmatter):
  ---
  description: What this agent does
  mode: primary | subagent | all
  model: anthropic/claude-sonnet-4-... (optional, overrides provider default)
  temperature: 0.2
  permission:
    edit: deny
    skill: allow
  ---

  Prompt body (markdown) — system prompt for this agent.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from opennote.notebooks import default_home

logger = logging.getLogger("opennote.agents.defs")

_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_VALID_MODES = {"primary", "subagent", "all"}


@dataclass
class AgentDef:
    name: str
    description: str
    mode: str  # primary | subagent | all
    prompt: str  # body after frontmatter
    model: Optional[str] = None
    temperature: Optional[float] = None
    permission: Dict = field(default_factory=dict)
    source_path: Optional[Path] = None
    hidden: bool = False


def _parse_agent_file(path: Path) -> AgentDef:
    text = path.read_text(encoding="utf-8").lstrip("\ufeff")
    if not text.lstrip().startswith("---"):
        raise ValueError(f"{path}: missing frontmatter '---'")
    text = text.lstrip()
    import re as _re
    m_open = _re.match(r"---[ \t]*\r?\n", text)
    if not m_open:
        raise ValueError(f"{path}: missing opening '---'")
    rest = text[m_open.end():]
    m_close = _re.search(r"\r?\n---[ \t]*\r?\n", rest)
    if not m_close:
        raise ValueError(f"{path}: missing closing '---'")
    fm_text = rest[: m_close.start()]
    body = rest[m_close.end():].lstrip("\r\n")
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(fm, dict):
        raise ValueError(f"{path}: frontmatter must be a mapping")

    name = path.stem.lower()
    if not _NAME_RE.match(name):
        raise ValueError(f"{path}: agent filename {name!r} must match ^[a-z0-9]+(-[a-z0-9]+)*$")

    desc = fm.get("description", "")
    if not isinstance(desc, str) or not desc.strip():
        # Fallback: use filename as description if missing
        desc = name

    mode = str(fm.get("mode", "all")).strip().lower()
    if mode not in _VALID_MODES:
        raise ValueError(f"{path}: mode must be one of {sorted(_VALID_MODES)}, got {mode!r}")

    model = fm.get("model")
    if model is not None and not isinstance(model, str):
        raise ValueError(f"{path}: model must be a string")

    temp = fm.get("temperature")
    if temp is not None:
        try:
            temp = float(temp)
        except (TypeError, ValueError):
            raise ValueError(f"{path}: temperature must be a number")
        if not (0 <= temp <= 2):
            raise ValueError(f"{path}: temperature must be 0-2")

    perm = fm.get("permission", {}) or {}
    if not isinstance(perm, dict):
        raise ValueError(f"{path}: permission must be a mapping")

    hidden = bool(fm.get("hidden", False))

    return AgentDef(
        name=name,
        description=str(desc).strip(),
        mode=mode,
        prompt=body.strip(),
        model=str(model).strip() if model else None,
        temperature=temp,
        permission=dict(perm),
        source_path=path,
        hidden=hidden,
    )


def _agent_search_roots(cwd: Path | None = None) -> List[Path]:
    from opennote.fsutil import walk_worktree_roots

    cwd = Path(cwd) if cwd is not None else Path.cwd()
    clean: List[Path] = []
    for ancestor in walk_worktree_roots(cwd):
        clean.append(ancestor / ".opennote" / "agents")
    home = default_home()
    for p in [home / "agents", Path.home() / ".config" / "opennote" / "agents"]:
        if p not in clean:
            clean.append(p)
    # Dedupe on resolved path
    uniq: List[Path] = []
    seen_s: set[str] = set()
    for p in clean:
        try:
            k = str(p.resolve())
        except (OSError, RuntimeError):
            k = str(p)
        if k not in seen_s:
            seen_s.add(k)
            uniq.append(p)
    return uniq


class AgentRegistry:
    """Discover and index agent definitions."""

    def __init__(self, agents: Optional[List[AgentDef]] = None):
        self._agents: List[AgentDef] = list(agents or [])
        self._by_name: Dict[str, AgentDef] = {a.name: a for a in self._agents}

    @classmethod
    def discover(cls, cwd: Path | None = None) -> "AgentRegistry":
        reg = cls()
        # Built-ins first (lowest priority — file-based overrides them)
        for builtin in _builtin_agents():
            reg._agents.append(builtin)
            reg._by_name[builtin.name] = builtin

        for root in _agent_search_roots(cwd):
            if not root.is_dir():
                continue
            try:
                for entry in sorted(root.iterdir()):
                    if not entry.is_file() or entry.suffix != ".md":
                        continue
                    try:
                        agent = _parse_agent_file(entry)
                    except Exception as exc:
                        logger.debug("Skipping agent %s: %s", entry, exc)
                        continue
                    # File-based overrides built-in on name collision
                    if agent.name in reg._by_name:
                        # Replace
                        idx = next((i for i, a in enumerate(reg._agents) if a.name == agent.name), None)
                        if idx is not None:
                            reg._agents[idx] = agent
                    else:
                        reg._agents.append(agent)
                    reg._by_name[agent.name] = agent
            except OSError:
                continue
        return reg

    def list(self, mode: Optional[str] = None) -> List[AgentDef]:
        if mode is None:
            return list(self._agents)
        return [a for a in self._agents if a.mode in (mode, "all")]

    def get(self, name: str) -> Optional[AgentDef]:
        return self._by_name.get(name.lower())

    def names(self) -> List[str]:
        return [a.name for a in self._agents]


def _builtin_agents() -> List[AgentDef]:
    return [
        AgentDef(
            name="ask",
            description="Grounded Q&A over your notebook sources — default primary agent.",
            mode="primary",
            prompt="",
        ),
        AgentDef(
            name="search",
            description="LLM-free retrieval — return top chunks with citations, no generation.",
            mode="primary",
            prompt="",
        ),
        AgentDef(
            name="explore",
            description="Fast, read-only codebase exploration — find files and search code without edits.",
            mode="subagent",
            prompt="You are an exploration subagent. Use search and list_sources to find relevant notebook content. Be concise and cite sources.",
        ),
        AgentDef(
            name="general",
            description="General-purpose subagent for multi-step research and synthesis.",
            mode="subagent",
            prompt="You are a general subagent. Help with research and synthesis over the notebook sources.",
        ),
    ]
