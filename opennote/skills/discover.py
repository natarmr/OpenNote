"""Skill discovery — scan shared dirs for SKILL.md files."""

from __future__ import annotations

from pathlib import Path
from typing import List, Set

from opennote.fsutil import walk_worktree_roots
from opennote.notebooks import default_home


def skill_search_roots(cwd: Path | None = None) -> List[Path]:
    """Return ordered list of directories that may contain `<name>/SKILL.md` skills."""
    cwd = Path(cwd) if cwd is not None else Path.cwd()
    roots: List[Path] = []

    project_skill_subdirs = [
        "skills",
        ".agents/skills",
        ".claude/skills",
        ".opennote/skills",
        ".opencode/skills",
    ]

    for ancestor in walk_worktree_roots(cwd):
        for sub in project_skill_subdirs:
            roots.append(ancestor / sub)

    home = Path.home()
    opennote_home = default_home()
    global_candidates = [
        opennote_home / "skills",
        home / ".agents" / "skills",
        home / ".claude" / "skills",
        home / ".config" / "opennote" / "skills",
    ]
    for p in global_candidates:
        if p not in roots:
            roots.append(p)

    # Dedupe on resolved path (handles symlinks), preserve order
    seen: Set[str] = set()
    uniq: List[Path] = []
    for p in roots:
        try:
            key = str(p.resolve())
        except (OSError, RuntimeError):
            key = str(p)
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def discover_skill_dirs(cwd: Path | None = None) -> List[Path]:
    """Return list of skill directories (each contains SKILL.md), first-match-wins order."""
    dirs: List[Path] = []
    seen_names: Set[str] = set()
    for root in skill_search_roots(cwd):
        if not root.is_dir():
            continue
        try:
            for entry in root.iterdir():
                if not entry.is_dir():
                    continue
                skill_md = entry / "SKILL.md"
                if not skill_md.is_file():
                    continue
                name = entry.name
                if name in seen_names:
                    continue
                seen_names.add(name)
                dirs.append(entry)
        except OSError:
            continue
    return dirs
