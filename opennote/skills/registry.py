"""Skill registry — load + index discovered skills."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from opennote.skills.discover import discover_skill_dirs
from opennote.skills.parse import parse_skill_file, validate_frontmatter

logger = logging.getLogger("opennote.skills")


@dataclass
class Skill:
    """A loaded skill."""

    name: str
    description: str
    directory: Path
    body: str  # markdown after frontmatter
    frontmatter: Dict = field(default_factory=dict)
    # Optional: list of bundled files (references, scripts) relative to dir
    files: List[str] = field(default_factory=list)


class SkillRegistry:
    """Discover and load skills from shared dirs."""

    def __init__(self, skills: Optional[List[Skill]] = None):
        self._skills: List[Skill] = list(skills or [])
        self._by_name: Dict[str, Skill] = {s.name: s for s in self._skills}

    @classmethod
    def discover(cls, cwd: Path | None = None) -> "SkillRegistry":
        """Scan shared dirs and load valid skills; invalid ones are skipped with a warning."""
        reg = cls()
        for skill_dir in discover_skill_dirs(cwd):
            skill_md = skill_dir / "SKILL.md"
            try:
                fm, body = parse_skill_file(skill_md)
                validate_frontmatter(fm, skill_dir.name, skill_md)
                name = fm["name"]
                desc = fm["description"]
                # Collect bundled files (top 200, for manifest)
                files: List[str] = []
                try:
                    for p in skill_dir.rglob("*"):
                        if p.is_file() and p.name != "SKILL.md":
                            try:
                                files.append(str(p.relative_to(skill_dir)))
                            except ValueError:
                                files.append(p.name)
                            if len(files) >= 200:
                                break
                except OSError:
                    pass
                skill = Skill(
                    name=name,
                    description=desc.strip(),
                    directory=skill_dir,
                    body=body,
                    frontmatter=fm,
                    files=sorted(files),
                )
                reg._skills.append(skill)
                reg._by_name[name] = skill
            except Exception as exc:
                logger.debug("Skipping skill at %s: %s", skill_dir, exc)
                continue
        return reg

    def list(self) -> List[Skill]:
        return list(self._skills)

    def get(self, name: str) -> Optional[Skill]:
        return self._by_name.get(name)

    def names(self) -> List[str]:
        return [s.name for s in self._skills]

    def is_empty(self) -> bool:
        return not self._skills

    def available_skills_xml(self) -> str:
        """Render <available_skills> XML snippet for injection into the skill tool description."""
        if not self._skills:
            return ""
        lines = ["<available_skills>"]
        for s in self._skills:
            # Escape XML special chars in description
            desc = s.description.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            lines.append(f'  <skill><name>{s.name}</name><description>{desc}</description></skill>')
        lines.append("</available_skills>")
        return "\n".join(lines)
