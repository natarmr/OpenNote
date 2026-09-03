"""Skill discovery — scan shared dirs for SKILL.md files."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Set

from opennote.notebooks import default_home


def _home() -> Path:
    # Reuse OPENNOTE_HOME if set, else ~/.opennote; skills global lives under ~/.opennote/skills/
    # But also scan ~/.agents/skills and ~/.claude/skills regardless of OPENNOTE_HOME
    return default_home()


def _worktree_roots(start: Path) -> List[Path]:
    """Return cwd and ancestors up to git worktree root (or filesystem root).

    Mirrors opencode behavior: walk up from cwd until git dir found.
    For discovery we just walk up to filesystem root — cheap and covers project nesting.
    """
    roots: List[Path] = []
    cur = start.resolve()
    seen: Set[Path] = set()
    while True:
        if cur in seen:
            break
        seen.add(cur)
        roots.append(cur)
        # Stop at filesystem root
        parent = cur.parent
        if parent == cur:
            break
        # If this dir is a git worktree root, include it and stop walking further?
        # Opencode stops at git worktree; we include the walk anyway but keep it bounded
        # (don't walk past home to avoid scanning all of filesystem on deep nests — but
        # cwd is usually near repo root, so full walk is fine).
        cur = parent
        # Safety: cap walk depth
        if len(roots) > 30:
            break
    return roots


def skill_search_roots(cwd: Path | None = None) -> List[Path]:
    """Return ordered list of directories that may contain `<name>/SKILL.md` skills.

    Order = project roots (cwd walk) first, then global homes.
    Duplicates are deduped; caller should first-match wins.
    """
    cwd = Path(cwd) if cwd is not None else Path.cwd()
    roots: List[Path] = []

    # Project-level candidates — for each ancestor, check these subdirs
    project_skill_subdirs = [
        "skills",               # ./skills/<name>/SKILL.md  (neutral)
        ".agents/skills",       # ./.agents/skills/<name>/SKILL.md
        ".claude/skills",       # ./.claude/skills/<name>/SKILL.md
        ".opennote/skills",     # ./.opennote/skills/<name>/SKILL.md (opennote-native)
        ".opencode/skills",     # ./.opencode/skills/<name>/SKILL.md (co-located with opencode)
    ]

    for ancestor in _worktree_roots(cwd):
        for sub in project_skill_subdirs:
            roots.append(ancestor / sub)

    # Global candidates
    home = Path.home()
    opennote_home = _home()
    global_candidates = [
        opennote_home / "skills",       # ~/.opennote/skills/<name>/SKILL.md
        home / ".agents" / "skills",    # ~/.agents/skills/<name>/SKILL.md
        home / ".claude" / "skills",    # ~/.claude/skills/<name>/SKILL.md
        # Also scan XDG-style if present
        home / ".config" / "opennote" / "skills",
    ]
    # If OPENNOTE_HOME is outside home (e.g., temp dir in tests), already covered
    for p in global_candidates:
        if p not in roots:
            roots.append(p)

    # Dedupe while preserving order
    seen: Set[Path] = set()
    uniq: List[Path] = []
    for p in roots:
        rp = p.resolve() if p.exists() else p
        # Use string key to avoid resolve() creating non-existent paths errors
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
