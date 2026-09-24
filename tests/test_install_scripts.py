"""Regression: installers must stay GitHub-tarball-only.

PyPI `opennote` belongs to an unrelated video-API SDK, and the old
ramratan.in tarball/ps1 URLs are 404 — so the scripts must never reference
either, and must always point at the GitHub main tarball.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARBALL = "https://github.com/natarmr/OpenNote/archive/refs/heads/main.tar.gz"


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _no_bare_pip_install_opennote(text: str) -> bool:
    """Every `pip install ... opennote` line must be the PEP 508 tarball form."""
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if "pip install" in line and "opennote" in line and " @ " not in line:
            return False
    return True


def test_install_bash_uses_github_tarball_only():
    text = _read("install")
    assert TARBALL in text
    assert "ramratan.in/opennote" not in text
    assert "#egg=" not in text
    # No bare `pip install opennote` (would fetch the squatter's SDK).
    assert _no_bare_pip_install_opennote(text)


def test_install_ps1_uses_github_tarball_only():
    text = _read("install.ps1")
    assert TARBALL in text
    assert "ramratan.in/opennote" not in text
    assert "ramratan.in/install.ps1" not in text
    assert _no_bare_pip_install_opennote(text)


def test_readme_and_install_doc_point_at_raw_github():
    for name in (
        "README.md",
        "docs/0-START-HERE/index.md",
        "docs/1-INSTALLATION/index.md",
    ):
        text = _read(name)
        assert "raw.githubusercontent.com/natarmr/OpenNote/main/install" in text
        assert "ramratan.in/install" not in text
