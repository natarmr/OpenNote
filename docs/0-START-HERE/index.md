# Get Started with OpenNote

**OpenNote** is a terminal-based, privacy-focused agentic harness for studying your own documents. Ingest sources, ask grounded questions with citations, and generate study artifacts — your data stays on your machine unless you choose a cloud provider.

## Choose Your Path

### One-line install (recommended)

```bash
# macOS / Linux / Git Bash / WSL
curl -fsSL https://ramratan.in/install | bash

# Windows PowerShell (bare `curl` is an alias — use one of these)
curl.exe -fsSL https://ramratan.in/install | bash
iwr -useb https://ramratan.in/install.ps1 | iex
```

### From source (developers)

```bash
pip install -e ".[dev]"
pytest -q   # full suite, 455/455 pass
```

See [Deploy](../1-INSTALLATION/index.md) for details and troubleshooting.

## Prerequisites

- **Python 3.10+** (`python3`, `python`, or `py` launcher)
- **`opennote` on PATH** — verify with `opennote --help`
  (fallback if the console script is missing: `py -m opennote --help`)
- **An LLM provider key** for answers (retrieval works with no key):
  `opennote auth add groq` (or `anthropic`, `openai`, `google`, …).
  Keys live in the OS keychain, else `<PROVIDER>_API_KEY` env vars.

## Your first 5 minutes

```bash
opennote create my_notebook
opennote ingest paper.pdf --notebook my_notebook
opennote ask "What does the paper claim about retrieval?" --notebook my_notebook
opennote            # launches the terminal UI
```

In the TUI, `Tab` cycles `ask → search → studio`. Try `/mindmap <topic>`, then `/open` to view it.

## Next steps

- [Features](../2-CORE-CONCEPTS/index.md) — how notebooks, retrieval, and grounded Q&A work
- [User Guide](../3-USER-GUIDE/index.md) — every command with examples
- [Deploy](../1-INSTALLATION/index.md) — install options and fixes
