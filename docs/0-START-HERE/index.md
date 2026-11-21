# Welcome to OpenNote

OpenNote is a small, local-first study assistant that lives in your terminal. You give it your PDFs, notes, or web pages; it indexes them on your machine; then you ask questions and get answers with citations back to the exact passages. No cloud indexing, no data harvesting — the only time anything leaves your computer is when you ask a model you chose a question.

## Get it running

The one-line installer handles Python, the package, and the `opennote` command for you:

```bash
# macOS / Linux / Git Bash / WSL
curl -fsSL https://raw.githubusercontent.com/natarmr/OpenNote/main/install | bash

# Windows PowerShell — bare `curl` is an alias, so use one of these:
curl.exe -fsSL https://raw.githubusercontent.com/natarmr/OpenNote/main/install | bash
iwr -useb https://raw.githubusercontent.com/natarmr/OpenNote/main/install.ps1 | iex
```

Already have Python 3.10+ and prefer source? `pip install -e ".[dev]"`.

See [Install & deploy](../1-INSTALLATION/index.md) if anything complains.

## Connect a model (retrieval needs no key)

Searching your documents works offline out of the box. To get generated answers, connect one provider:

```bash
opennote auth add groq        # prompts for a key, checks it, picks a model
```

Keys are stored in your OS keychain — never in a file. Prefer environment variables? `opennote auth add groq --env GROQ_API_KEY` reads the key from your shell instead. You can also just set `GROQ_API_KEY` and skip `auth add` entirely.

Run `opennote config --effective` any time to see what's connected and which default OpenNote will use.

## Your first five minutes

```bash
opennote create my_notebook
opennote ingest paper.pdf --notebook my_notebook
opennote ask "What does the paper claim about retrieval?" --notebook my_notebook
opennote            # opens the interactive terminal UI
```

In the TUI, `Tab` moves between **ask → search → studio** (plus tutor/socratic/curriculum if you're in the mood to study). Try `/mindmap <topic>`, then `/open` to look at what it made. Press `F1` if you forget a command, or `Ctrl+P` to search all of them.

## Where to go next

- [How OpenNote thinks](../2-CORE-CONCEPTS/index.md) — notebooks, chunks, retrieval, grounding
- [User guide](../3-USER-GUIDE/index.md) — every command, with examples
- [Install & deploy](../1-INSTALLATION/index.md) — options and troubleshooting
