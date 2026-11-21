# Install & deploy

Pick whichever fits you. All paths give you the same `opennote` command.

## One-line install (recommended)

```bash
# macOS / Linux / Git Bash / WSL
curl -fsSL https://raw.githubusercontent.com/natarmr/OpenNote/main/install | bash

# Windows PowerShell 5.1 — bare `curl` is an alias for Invoke-WebRequest, so:
curl.exe -fsSL https://raw.githubusercontent.com/natarmr/OpenNote/main/install | bash
# or, natively in PowerShell:
iwr -useb https://raw.githubusercontent.com/natarmr/OpenNote/main/install.ps1 | iex
```

The installer finds `python3` → `python` → `py`, needs Python 3.10+, installs via `python -m pip`, then checks that `opennote --help` works.

## Via npm or bun

Python 3.10+ is still needed — the npm package is a thin launcher that installs the
Python app into a versioned cache on first use.

```bash
npx @natarmr/opennote --help                  # no install required
npm install -g @natarmr/opennote              # global `opennote` command
bunx @natarmr/opennote --help                 # bun
bun add -g @natarmr/opennote
```

Set `OPENNOTE_PYTHON` to pick a specific interpreter, `OPENNOTE_NPM_CACHE` to move the
cache, or `OPENNOTE_TARBALL_URL` to pin a source tag. Details in `npm/README.md`.

## Via pipx

One command, isolated environment, Python-native:

```bash
pipx install "opennote @ https://github.com/natarmr/OpenNote/archive/refs/heads/main.tar.gz"
```

> Heads up: don't run a bare `pip install opennote` or `npm install -g opennote`. Both
> unscoped names belong to unrelated projects. The official npm package is the scoped
> `@natarmr/opennote`; the Python installers pull OpenNote from GitHub.

## From source (for development)

```bash
pip install -e ".[dev]"      # or: py -m pip install -e ".[dev]"
opennote --help
py -m pytest -q              # full suite — 729 tests
```

Want fully offline answers with a local GGUF model? Add `pip install -e ".[local]"` (`llama-cpp-python`; CPU wheels cover 3.10–3.12).

## Check it works

```bash
opennote --help          # command is on PATH
opennote auth list       # connected providers and where their keys come from
opennote config --effective   # resolved default + full provider status
```

If `opennote` isn't found, restart your shell. `py -m opennote <cmd>` always works as a fallback on Windows.

## Something went wrong?

| Symptom | What to do |
|---|---|
| `opennote` runs some other npm package | `npm uninstall -g opennote`, or use `py -m opennote` |
| "Python 3.10+ is required" | Install 3.10–3.12 (3.13 needs newer `llama-cpp` wheels for `[local]`) |
| Command not on PATH after install | Restart the shell; fallback `py -m opennote <cmd>` |
| Key validation fails | It needs network. `opennote auth add <provider> --no-verify` stores the key now, validates later |
| Key works in env but `auth list` says none | Set the right variable (`GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, `MODEL_API_KEY` for Meta) or use `auth add --env NAME` |
