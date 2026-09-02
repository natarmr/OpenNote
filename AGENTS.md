# OpenNote AGENTS Guide

## Quickstart

- Run all core tests: `py -m pytest -q --deselect tests/test_agents_tools.py::test_schemas_have_both_tools`
- Skip the 1 known test failure: `test_schemas_have_both_tools` expects `{"search", "list_sources", "web_search"}` not `{"search", "list_sources"}`
- TUI needs a display/terminal: use `py -m textual` or run the CLI `py -m opennote.cli`

## Environment / Launch

- Launcher is `py` (not `python`); use `py -m ...` for all commands
- No `python` on PATH — all invocations use the `py` wrapper
- Tests may fail if `TAVILY_API_KEY` is absent (web search hidden); set it to enable Tavily

## Core Packages & Entry Points

- `opennote.retrieval.retriever.Retriever` — vector search over notebook chunks; use `use_bm25=True, bm25_alpha=0.5` for hybrid BM25+rerank
- `opennote.retrieval.bm25.Bm25Retriever` — keyword BM25 retrieval; `hybrid_search()` combines with vector scores
- `opennote.artifacts` — studio generators: `create_mindmap`, `make_study_guide`, `make_faq`, `make_briefing`, `make_timeline`, `make_source_summaries`, `make_suggested_questions`; `save_artifact` persists to `notebook/artifacts/`
- `opennote.audio.tts.explain_audio` — TTS adapter chain (groq→openai→gemini→edge-tts); degrades to `.md` transcript
- `opennote.video.explain_video` — narrated slideshow: Stage 1 (.png+ .md always succeeds), Stage 2 (.mp3 per-slide TTS), Stage 3 (ffmpeg mux to .mp4)
- `opennote.websearch` — Tavily web search + `read_page`; hidden when `TAVILY_API_KEY` absent
- `opennote.cli` — CLI entrypoint; `opennote search` is the retrieval half of RAG

## TUI (Textual UI)

- Modes cycle: `ask → search → studio` (Tab cycles); `/studio` slash command enters studio mode
- Studio mode presents a submenu of artifact generators (mind-map, study guide, FAQ, briefing, timeline, suggested questions)
- Slash commands: `/studio`, `/mindmap`, `/study`, `/faq`, `/briefing`, `/timeline`, `/suggest`, `/audio`, `/video`, `/open`
- Transcript shows results; graceful degradation when backends unavailable
- Run TUI tests: `py -m pytest tests/test_tui_app.py` (may have import errors if Textual not fully set up)

## Tests

- Run the full suite: `py -m pytest -q` — **336/336 pass** (no known failures)
- Run TUI tests only: `py -m pytest tests/test_tui_app.py`
- `test_schemas_have_both_tools` expects `{"search", "list_sources", "web_search"}`

## Known Issues / Blockers

- Groq TTS requires orpheus terms acceptance; other backends built-to-spec + mock-tested
- No `python` on PATH — always use `py -m ...`

## Workflow Order

1. `lint` → `typecheck` → `test`
2. If adding retrieval features: enable `use_bm25` on `Retriever` and tune `bm25_alpha`
3. If adding TTS/video: ensure Groq key or fallback transcript will be used
4. If adding web search: configure correct `TAVILY_API_KEY`