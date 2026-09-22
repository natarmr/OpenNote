# Deploy — Install OpenNote

## One-line (global)

```bash
# macOS / Linux / Git Bash / WSL
curl -fsSL https://ramratan.in/install | bash

# Windows PowerShell 5.1 — bare `curl` is an alias for Invoke-WebRequest, use:
curl.exe -fsSL https://ramratan.in/install | bash
# or native PowerShell:
iwr -useb https://ramratan.in/install.ps1 | iex
```

The installer resolves `python3` → `python` → `py`, requires 3.10+, installs via `python -m pip` (PyPI, GitHub tarball fallback), then verifies with `opennote --help`.

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
