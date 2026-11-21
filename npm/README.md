# @natarmr/opennote

The one-command npm/bun launcher for [OpenNote](https://github.com/natarmr/OpenNote) — a
private, grounded study harness for your own documents.

> The bare names `opennote` (npm and PyPI) belong to unrelated projects. This is the
> official scoped package.

## Install and run

```bash
# npm — run without installing
npx @natarmr/opennote --help

# npm — install globally, then use the `opennote` command
npm install -g @natarmr/opennote
opennote --help

# bun (installs npm packages, so the same package works)
bunx @natarmr/opennote --help
bun add -g @natarmr/opennote
opennote --help
```

On the first real invocation the launcher installs the Python app into a versioned
cache directory, then runs `python -m opennote` with your arguments. Later runs reuse
the cache. The first run can take a few minutes because OpenNote ships local embedding
and retrieval dependencies.

## Requirements

- **Python 3.10+** on PATH (`python3`, `python`, or the Windows `py` launcher).
- An LLM provider key for answers (`opennote auth add <provider>`). Retrieval works
  without a key.

## Configuration

| Variable | Meaning |
|---|---|
| `OPENNOTE_PYTHON` | Exact Python executable to use (skips `python3`/`python`/`py` detection). |
| `OPENNOTE_TARBALL_URL` | Source tarball to install. Default: OpenNote `main` tarball. Point this at a tag or fork for reproducibility. |
| `OPENNOTE_NPM_CACHE` | Override the cache directory. Default: `%LOCALAPPDATA%\opennote\npm` on Windows, `~/.cache/opennote/npm` elsewhere. |

Uninstall: `npm uninstall -g @natarmr/opennote`. To reclaim disk space, delete the
cache directory shown by `OPENNOTE_NPM_CACHE`.

## Publishing

From this directory:

```bash
npm test
npm publish --access public
```

Bump `version` in `package.json` alongside the Python app version when cutting a release.
