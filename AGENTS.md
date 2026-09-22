# OpenNote AGENTS Guide

## Commands

- Tests: `pytest -q` (full suite, ~3 min) · focused: `pytest tests/<file>.py -q`
- CLI/TUI: bare `opennote` (TUI with no subcommand) and `opennote ask/search/ingest/...`
- No `python` on PATH — only `py`. Fallback if console scripts missing: `py -m opennote ...` / `py -m pytest ...`
- Lint: `py -m ruff check <touched-files>` — repo has pre-existing hits and no ruff config; match surrounding style, add zero *new* findings (diff against baseline)
- No mypy installed — `py -m py_compile` touched files instead. Order: `py_compile` → `pytest`
- No CI, no pre-commit, no `opencode.json`. `git status` before/after; commit only when asked

## Architecture

- `opennote/cli.py:app` — Typer entry (`opennote = "opennote.cli:app"`); `opennote/__main__.py` makes `py -m opennote` work
- `opennote/retrieval/retriever.py:Retriever` — hybrid BM25+vectors, adaptive `top_k` 5→8→12 (`engg_choices.md:E1/E2`); `--no-bm25` / `use_bm25=False` disables
- `opennote/artifacts.py` — `save_artifact` → `<notebook>/artifacts/` (atomic, frontmatter `kind/title/created/prompt_version/sources`); mindmap extras `parse_mindmap`, `to_ascii_tree`, `short_artifact_display`
- `opennote/audio/tts.py:explain_audio` — groq→openai→gemini→edge-tts, degrades to `.md`; `opennote/video.py:explain_video` — slides→TTS→ffmpeg, each stage degrades
- `opennote/agents/loop.py:agent_turn` — multi-round tool loop; `agents/tools.py:execute_tool` + `get_tool_schemas(ctx)`; `ToolContext` decouples tools from Retriever
- Skills (`opennote/skills/`, SKILL.md in `./skills/`, `./.agents/skills/`, `./.claude/skills/`, `./.opennote/skills/`, `~/.agents/skills/`, `~/.claude/skills/`, `~/.opennote/skills/`) are **model-invoked**; users trigger via TUI `/use <skill> [task]`, which arms one ask turn
- Plugins: `.opennote/plugins/*.py` (+ `~/.opennote/`, entry-points); agents: `.opennote/agents/*.md` frontmatter (`mode: primary|subagent`)
- TUI (`opennote/tui/`): Tab cycles `ask→search→studio`; palette in `tui/palette.py`; commands in `tui/commands.py` (see `COMMAND_REFERENCE.md`); chrome is **ASCII-only**; sidebar hides <112 cols; spend in `<notebook>/usage.json`
- Runtime state (`.opennote/`, notebooks, keys) is gitignored — never commit it

## Testing quirks

- `TAVILY_API_KEY` absent → web-search tests hide/fail; set it for full signal
- Root fixtures must stay: `kimi.pdf` (`test_e2e_grounded.py` asserts it exists), `injection-test-set.txt` (read by **absolute** `D:/Code/OpenNote/...` path in `test_injection_gate.py` — breaks if moved)
- E2E loads embedding weights (~90s first call, module-cached); `opennote artifacts check -n <nb>` is the fast live studio self-check (fallback path, no LLM cost)
- `test_schemas_have_both_tools` pins core tools `{"search", "list_sources", "web_search", "read_page", "submit_grounded_answer"}`; dynamic tools come via `get_tool_schemas`
- Headless TUI tests use `app.run_test()` pilot; unmounted widgets raise `NoScreen` on `screen` access (`Transcript._reveal` is hardened for this)

## Conventions

- Match file-local style (`Optional[]`/`List[]`, `except Exception` in TUI) over ruff ideals
- User docs live in `docs/0-START-HERE|1-INSTALLATION|2-CORE-CONCEPTS|3-USER-GUIDE/index.md`, linked from README header — keep them short and command-accurate
- Record fixes in `ledger.md` (`| L<n> | SEV | location | ... |`) and design calls in `engg_choices.md` (`## E<n>`); next IDs continue the sequence
