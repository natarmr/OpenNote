# OpenNote AGENTS Guide

## Quickstart

- Run all core tests: `pytest -q`
- Bare `opennote` launches the TUI (no subcommand) and runs every CLI command (`opennote ask/search/ingest/...`)

## Environment / Launch

- Prefer the bare installed commands: `opennote`, `pytest` (both on PATH via `pip install -e ".[dev]"`)
- Fallback when the console scripts are missing: `py -m opennote.cli ...` / `py -m pytest ...` (launcher is `py`, not `python`)
- Tests may fail if `TAVILY_API_KEY` is absent (web search hidden); set it to enable Tavily

## Core Packages & Entry Points

- `opennote.retrieval.retriever.Retriever` — hybrid BM25+vectors by default (adaptive `top_k` 5→8→12 by corpus size; see `engg_choices.md:E1/E2`); pass `use_bm25=False` or `--no-bm25` to disable
- `opennote.retrieval.bm25.Bm25Retriever` — keyword BM25 retrieval; `hybrid_search()` combines with vector scores
- `opennote.artifacts` — studio generators: `create_mindmap`, `make_study_guide`, `make_faq`, `make_briefing`, `make_timeline`, `make_source_summaries`, `make_suggested_questions`; `save_artifact` persists to `notebook/artifacts/`
- `opennote.audio.tts.explain_audio` — TTS adapter chain (groq→openai→gemini→edge-tts); degrades to `.md` transcript
- `opennote.video.explain_video` — narrated slideshow: Stage 1 (.png+ .md always succeeds), Stage 2 (.mp3 per-slide TTS), Stage 3 (ffmpeg mux to .mp4)
- `opennote.websearch` — Tavily web search + `read_page`; hidden when `TAVILY_API_KEY` absent
- `opennote.skills` — SKILL.md discovery (shared dirs: `./skills/`, `./.agents/skills/`, `./.claude/skills/`, `./.opennote/skills/`, `~/.agents/skills/`, `~/.claude/skills/`, `~/.opennote/skills/`); `skill` + `run_skill_script` tools in agent loop
- `opennote.plugins` — Python plugin loader (`.opennote/plugins/*.py`, `~/.opennote/plugins/*.py`, `[project.entry-points."opennote.plugins"]`); built-in `supermemory` plugin gated on `SUPERMEMORY_API_KEY`
- `opennote.agents.defs` — agent definitions (markdown frontmatter in `.opennote/agents/*.md`, `~/.opennote/agents/*.md`); `task` tool for subagents (explore/general)
- `opennote.agents.tools.ToolContext` — decouples tools from bare Retriever; `execute_tool(name, ctx, kwargs)` with `get_tool_schemas(ctx)`
- `opennote.cli` — CLI entrypoint; `opennote search` is the retrieval half of RAG; `opennote skills|plugins|agents|capabilities` introspection

## TUI (Textual UI)

- Modes cycle: `ask → search → studio` (Tab cycles); `/studio` slash command enters studio mode
- Studio mode presents a submenu of artifact generators (mind-map, study guide, FAQ, briefing, timeline, suggested questions)
- Slash commands: `/studio`, `/mindmap`, `/study`, `/faq`, `/briefing`, `/timeline`, `/suggest`, `/audio`, `/video`, `/open`, `/skills`, `/skill`, `/plugins`, `/agents`, `/agent`, `/capabilities`, `/context`, `/snake` (waiting-room game, allowed while busy; completions toast over the modal)
- Context meter (`opennote/context_meter.py`, `engg_choices.md:E12`): provider-reported tokens when available (`~` estimate otherwise); 32-col right `SideBar` (session/context/services/footer, hidden <112 cols) + persistent `ctx` readout in prompt bar + `/context` panel; spend in `<notebook>/usage.json`; TUI chrome is ASCII-only
- Transcript shows results; graceful degradation when backends unavailable
- Run TUI tests: `pytest tests/test_tui_app.py` (may have import errors if Textual not fully set up)

## Tests

- Run the full suite: `pytest -q` — **410/410 pass** (includes `tests/test_e2e_grounded.py`); `pytest tests/data/kimi.tsv` golden: `opennote golden tests/data/kimi.tsv`
- Run TUI tests only: `pytest tests/test_tui_app.py`
- `test_schemas_have_both_tools` expects `{"search", "list_sources", "web_search", "read_page", "submit_grounded_answer"}` (core only; dynamic tools via `get_tool_schemas`)

## Known Issues / Blockers

- Groq TTS requires orpheus terms acceptance; other backends built-to-spec + mock-tested
- No `python` on PATH — always use `py -m ...` as fallback when bare `opennote`/`pytest` are missing

## Workflow Order

1. `lint` → `typecheck` → `test`
2. If adding retrieval features: hybrid is default-on; use `--no-bm25` / `use_bm25=False` to disable and tune `bm25_alpha`; `top_k` is adaptive (5→8→12), see `engg_choices.md`
3. If adding TTS/video: ensure Groq key or fallback transcript will be used
4. If adding web search: configure correct `TAVILY_API_KEY`
5. If adding skills: `npx skills add <owner/repo> -a codex` (→ `.agents/skills/` — shared dir scanned by opennote)
6. If adding plugins: place `.py` in `.opennote/plugins/` and set `SUPERMEMORY_API_KEY` for supermemory
7. If adding agents: create markdown in `.opennote/agents/*.md` with frontmatter (`mode: primary|subagent`)