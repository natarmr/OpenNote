# OpenNote

A private, local-first study assistant for your terminal. Ingest your PDFs, notes, and web pages; search them without any API key; ask questions and get answers with citations back to the exact passages. Your files stay on your machine, and the only thing that reaches a model provider is the question you asked.

[Get started](docs/0-START-HERE/index.md) | [Install](docs/1-INSTALLATION/index.md) | [How it works](docs/2-CORE-CONCEPTS/index.md) | [User guide](docs/3-USER-GUIDE/index.md)

## Quick start

```bash
pip install -e ".[dev]"

opennote create my_notebook
opennote ingest paper.pdf -n my_notebook
opennote ask "What does the paper claim about retrieval?" -n my_notebook
opennote            # opens the terminal UI
```

Searching works offline. To get generated answers, connect one provider: `opennote auth add groq` (or `anthropic`, `openai`, `google`, `cerebras`, `meta`, `opencode`). Keys go into your OS keychain; you can also supply them through environment variables instead of prompting.

## What you can do

- **Ingest** PDF (Docling with a fallback), DOCX, HTML/URLs, and TXT/MD with page and heading citations.
- **Search** with local embeddings plus BM25 - hybrid, adaptive, and always available without a key.
- **Ask** with grounded answers and citation validation, or chat in a multi-round agent loop that searches when it needs evidence.
- **Study** with generated mind maps, study guides, FAQs, briefings, timelines, suggested questions, narrated audio, and slideshow video.
- **Learn** with built-in tutor, Socratic, and curriculum modes plus local flashcards and assessment.
- **Configure** everything non-secret through one layered `opennote.json`, and control which tools an agent may use with ordered permissions.

The [core concepts guide](docs/2-CORE-CONCEPTS/index.md) explains how these pieces fit together; the [user guide](docs/3-USER-GUIDE/index.md) lists every command.

## Install

One-line installer (handles Python and the `opennote` command):

```bash
# macOS / Linux / Git Bash / WSL
curl -fsSL https://raw.githubusercontent.com/natarmr/OpenNote/main/install | bash

# Windows PowerShell - bare `curl` is an alias, so use:
curl.exe -fsSL https://raw.githubusercontent.com/natarmr/OpenNote/main/install | bash
# or: iwr -useb https://raw.githubusercontent.com/natarmr/OpenNote/main/install.ps1 | iex
```

npm/bun launcher (Python 3.10+ still required):

```bash
npx @natarmar/opennote --help
npm install -g @natarmar/opennote    # then: opennote --help
```

pipx:

```bash
pipx install "opennote @ https://github.com/natarmr/OpenNote/archive/refs/heads/main.tar.gz"
```

Never use bare `pip install opennote` or `npm install -g opennote` - those names belong to unrelated projects. Details and fixes are in the [installation guide](docs/1-INSTALLATION/index.md).

## Commands

The main commands:

```bash
opennote create <name> [--model BAAI/bge-small-en-v1.5]
opennote list
opennote rename <old> <new>
opennote delete <name>
opennote remove <source-substring> -n <name>

opennote ingest <path-or-url> -n <name> [--parser auto|docling|fallback] [--ocr] [--force]
opennote search "<query>" -n <name> [--top-k N] [--source file.pdf] [--no-bm25]
opennote golden <golden.tsv> -n <name>
opennote ask "<question>" -n <name> [--provider groq] [--multihop]
opennote chat -n <name>

opennote auth add groq [--env GROQ_API_KEY] [--no-verify]
opennote auth list
opennote auth models groq [--set <model-id>]
opennote auth verify
opennote auth remove groq
opennote auth endpoint set openai https://host/v1   # also: list, clear

opennote config [--path] [--effective]
opennote skills [show <name>]
opennote plugins
opennote agents [show <name>]
opennote capabilities
opennote artifacts [export|show|check] -n <name>
opennote learn {profile|quiz|review|grade|assess|plan} -n <name>
opennote local {add|list|use|remove}
opennote version
```

The interactive UI adds `/connect`, `/model`, `/switch-model`, `/endpoint`, `/auth`, the studio generators, `/use <skill>`, and the education agents reachable with `Tab`. `F1` opens help and `Ctrl+P` searches all commands. See [COMMAND_REFERENCE.md](COMMAND_REFERENCE.md).

## Configuration

All non-secret settings live in a layered `opennote.json` (global, then project, then project-local - last one wins). Keys stay in the OS keychain or an environment variable; `auth.json` holds only provider settings and endpoint overrides.

```jsonc
{
  "model": "groq/qwen/qwen3.8-27b",   // optional default for new work
  "default_agent": "ask",
  "theme": "tokyonight",             // opencode|opencode-light|tokyonight|catppuccin|gruvbox
  "retrieval": { "bm25": true, "bm25_alpha": 0.5, "context_budget": 12000 },
  "permissions": [
    { "action": "script", "resource": "*", "effect": "ask" },
    { "action": "plugin", "resource": "*", "effect": "deny" }
  ],
  // Optional custom OpenAI-compatible provider (key from keychain or env):
  "providers": {
    "my-gateway": {
      "name": "My Gateway",
      "base_url": "https://llm.example.com/v1",
      "env": "MY_GATEWAY_KEY",
      "models": ["my-model-1"]
    }
  }
}
```

Permissions are ordered and the **last match wins**; effects are `allow`, `deny`, and `ask`. Agents can narrow them further in their frontmatter. `{env:VAR}` is substituted anywhere in the config. `opennote config --effective` shows the resolved provider, key source, model, and endpoint; the [user guide](docs/3-USER-GUIDE/index.md) covers the details.

OpenNote remembers your last-used provider/model so the next launch opens where you left off. If that key is later removed, it reports "no provider connected" rather than showing a stale selection. The built-in providers carry no hardcoded model names - the model menu always comes from the provider's live `/models` list, clearly labelled when it falls back to a cached or configured list.

## Development

```bash
pip install -e ".[dev]"
py -m pytest -q          # full suite (735 tests)
py -m ruff check <files> --statistics
```

See [AGENTS.md](AGENTS.md) for the verification order and repository-specific quirks.

MIT - see [LICENSE](LICENSE).
