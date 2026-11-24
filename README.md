# OpenNote

A private, local-first study assistant for your terminal. Ingest your PDFs, notes, and web pages; search them without any API key; ask questions and get answers with citations back to the exact passages.

Follow [@zirmythen on X](https://x.com/zirmythen) for updates.

[Get started](docs/0-START-HERE/index.md) | [Install](docs/1-INSTALLATION/index.md) | [How it works](docs/2-CORE-CONCEPTS/index.md) | [User guide](docs/3-USER-GUIDE/index.md)

## Quick start

```bash
pip install -e ".[dev]"

opennote create my_notebook
opennote ingest paper.pdf -n my_notebook
opennote ask "What does the paper claim about retrieval?" -n my_notebook
opennote            # opens the terminal UI
```

Searching works offline. For generated answers, connect a provider: `opennote auth add groq` (or `anthropic`, `openai`, `google`, `cerebras`, `meta`, `opencode`).

## What you can do

- Ingest PDF, DOCX, HTML/URLs, and TXT/MD with page and heading citations.
- Search with local embeddings plus BM25 - no key needed.
- Ask with grounded answers, or chat while the model searches for evidence.
- Generate study guides, mind maps, FAQs, narrated audio, and slideshow video.
- Study with built-in tutor, Socratic, and curriculum modes and local flashcards.

See [how it works](docs/2-CORE-CONCEPTS/index.md) for the details.

## Install

```bash
# macOS / Linux / Git Bash / WSL
curl -fsSL https://raw.githubusercontent.com/natarmr/OpenNote/main/install | bash

# Windows PowerShell
curl.exe -fsSL https://raw.githubusercontent.com/natarmr/OpenNote/main/install | bash
```

npm/bun launcher (Python 3.10+ still required): `npx @natarmar/opennote --help`. Never use the bare `opennote` names on npm or PyPI - they belong to unrelated projects. More options and fixes are in the [installation guide](docs/1-INSTALLATION/index.md).

## Commands

The usual starting points:

```bash
opennote ingest <path-or-url> -n <name>
opennote search "<query>" -n <name>
opennote ask "<question>" -n <name>
opennote chat -n <name>
opennote auth add groq
opennote config --effective     # see the active provider, key source and model
```

Everything else - notebooks, providers, artifacts, learning tools, local models, and all TUI commands - is in the [user guide](docs/3-USER-GUIDE/index.md) and [command reference](COMMAND_REFERENCE.md).

## Configuration

Non-secret settings live in a layered `opennote.json` (global, project, project-local). Keys stay in the OS keychain or an environment variable.

```jsonc
{
  "model": "groq/qwen/qwen3.8-27b",   // optional default for new work
  "theme": "tokyonight",
  "permissions": [
    { "action": "script", "resource": "*", "effect": "ask" }
  ]
}
```

OpenNote remembers your last-used provider/model, and never shows a model menu from hardcoded names - the list comes from the provider's live `/models` endpoint. Full config and permission details are in the [user guide](docs/3-USER-GUIDE/index.md).

## Development

```bash
pip install -e ".[dev]"
py -m pytest -q
```

See [AGENTS.md](AGENTS.md) for verification order and repository quirks. MIT - see [LICENSE](LICENSE).
