"""Skills subsystem — discovery + loading of SKILL.md-based agent skills.

Skills follow the agentskills.io / vercel-labs/skills standard:
each skill is a directory containing a SKILL.md with YAML frontmatter
(name, description, optional license/compatibility/metadata).
"""

from opennote.skills.registry import Skill, SkillRegistry  # noqa: F401

__all__ = ["Skill", "SkillRegistry"]
