# Deploy — Install OpenNote

## One-line (global)

```bash
# macOS / Linux / Git Bash / WSL
curl -fsSL https://raw.githubusercontent.com/natarmr/OpenNote/main/install | bash

# Windows PowerShell 5.1 — bare `curl` is an alias for Invoke-WebRequest, use:
curl.exe -fsSL https://raw.githubusercontent.com/natarmr/OpenNote/main/install | bash
# or native PowerShell:
iwr -useb https://raw.githubusercontent.com/natarmr/OpenNote/main/install.ps1 | iex
```

The installer resolves `python3` → `python` → `py`, requires 3.10+, installs from the GitHub tarball via `python -m pip`, then verifies with `opennote --help`.

*Warning:* never run bare `pip install opennote` — that PyPI name belongs to an unrelated video-API SDK.

## From source (developers)

```bash
pip install -e ".[dev]"
opennote --help
pytest -q   # full suite, 455/455 pass
```

Optional extras: `pip install -e ".[local]"` for offline GGUF inference (`llama-cpp-python`).

## Verify

```bash
opennote --help          # console script on PATH
opennote auth list       # providers and key status
```

If the console script is missing, `py -m opennote --help` is the unambiguous equivalent (launcher is `py`, not `python`).

## Troubleshooting

| Problem | Fix |
|---|---|
| `opennote` runs an npm package instead | `npm uninstall -g opennote`, or use `py -m opennote` |
| `Python 3.10+ is required` | Install 3.10–3.12 (3.13 needs newer `llama-cpp` wheels for `[local]`) |
| Console script not on PATH after install | Restart the shell; fallback `py -m opennote <cmd>` |
| Key validation fails | Needs network; `auth add --no-verify` stores without checking |
