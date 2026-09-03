"""Parse SKILL.md frontmatter (YAML) + body."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Tuple

import yaml

# agentskills.io name rule: lowercase alphanumeric + single hyphen separators,
# 1-64 chars, not starting/ending with hyphen, no consecutive hyphens.
_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_MAX_NAME_LEN = 64
_MAX_DESC_LEN = 1024


def parse_skill_file(path: Path) -> Tuple[Dict, str]:
    """Parse *path* (SKILL.md) into (frontmatter dict, body str).

    Raises ValueError on malformed YAML or missing frontmatter delimiters.
    """
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"{path}: missing opening '---' frontmatter delimiter")
    # Split on the second "---" line
    # frontmatter is between first and second "---" at start of line
    parts = text.split("\n---", 1)
    if len(parts) < 2:
        # Fallback: try "---\n" variant
        # Some files use "\n---\n" on its own line
        idx = text.find("\n---", 3)
        if idx == -1:
            raise ValueError(f"{path}: missing closing '---' frontmatter delimiter")
        fm_text = text[3:idx]
        body = text[idx + 4 :]
    else:
        fm_text = parts[0][3:]  # strip leading "---"
        body = parts[1]
        # body starts with maybe "\n" then content; strip one leading newline if present
        if body.startswith("\n"):
            body = body[1:]
        elif body.startswith("\r\n"):
            body = body[2:]

    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: invalid YAML frontmatter: {exc}") from exc

    if not isinstance(fm, dict):
        raise ValueError(f"{path}: frontmatter must be a mapping")

    return fm, body.lstrip("\n")


def validate_frontmatter(fm: Dict, skill_dir_name: str, path: Path) -> None:
    """Validate required fields; raise ValueError on violation."""
    name = fm.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{path}: frontmatter 'name' is required and must be a non-empty string")
    if len(name) > _MAX_NAME_LEN:
        raise ValueError(f"{path}: 'name' too long ({len(name)} > {_MAX_NAME_LEN})")
    if not _NAME_RE.match(name):
        raise ValueError(
            f"{path}: 'name' {name!r} must match ^[a-z0-9]+(-[a-z0-9]+)*$ "
            "(lowercase, no leading/trailing/consecutive hyphens)"
        )
    if name != skill_dir_name:
        raise ValueError(
            f"{path}: 'name' {name!r} must match directory name {skill_dir_name!r}"
        )

    desc = fm.get("description")
    if not isinstance(desc, str) or not desc.strip():
        raise ValueError(f"{path}: frontmatter 'description' is required and must be non-empty")
    if len(desc) > _MAX_DESC_LEN:
        raise ValueError(f"{path}: 'description' too long ({len(desc)} > {_MAX_DESC_LEN})")
    # license / compatibility are optional strings if present
    for opt in ("license", "compatibility"):
        if opt in fm and fm[opt] is not None and not isinstance(fm[opt], str):
            raise ValueError(f"{path}: frontmatter '{opt}' must be a string if present")
    if "metadata" in fm and fm["metadata"] is not None:
        if not isinstance(fm["metadata"], dict):
            raise ValueError(f"{path}: frontmatter 'metadata' must be a mapping if present")
        for k, v in fm["metadata"].items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise ValueError(f"{path}: frontmatter 'metadata' must be str->str")
