"""Jinja prompt rendering — templates live in opennote/prompt_templates/."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "prompt_templates"

_env: Environment | None = None


def _get_env() -> Environment:
    global _env
    if _env is None:
        _env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=False,
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )
    return _env


@lru_cache(maxsize=32)
def _get_template(name: str):
    return _get_env().get_template(name)


def render(name: str, **kwargs) -> str:
    """Render a Jinja template from opennote/prompt_templates/.

    Templates use {{ var }} and {% if var %} — escaping of source content
    must happen BEFORE calling this (never inside the template).
    """
    tmpl = _get_template(name)
    return tmpl.render(**kwargs)


def render_string(template_str: str, **kwargs) -> str:
    """Render an ad-hoc Jinja string (for tests / fallbacks)."""
    env = _get_env()
    return env.from_string(template_str).render(**kwargs)
