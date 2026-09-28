# OpenNote AGENTS Guide

## Commands

- No `python` on PATH — only `py`. `opennote.exe` is installed, so bare `opennote` works; fallback `py -m opennote ...` / `py -m pytest ...`
- Full suite: `py -m pytest -q` — **641 tests, ~3.2 min**. Focused: `py -m pytest tests/<file>.py -q`
- No mypy, no CI, no pre-commit, no `opencode.json`. `py -m py_compile` touched files instead
- Lint: `py -m ruff check <touched-files>` — **no ruff config and ~200 pre-existing findings** in files like `chat/ask.py`. House style is `List[]`/`Optional[]`/`except Exception`, which ruff dislikes. So *count* findings before and after (`--statistics`) and keep the delta at zero; do not "fix" house style to satisfy ruff
- Order that matters: `py_compile` → focused `pytest` → full `pytest` → ruff delta
- Commit style is conventional: `type(scope): description` on `main`. `git status` before/after; commit only when asked; never push unprompted

## Testing quirks

- **`kimi.pdf` and `injection-test-set.*` are gitignored *and* untracked** — a fresh clone has neither, so `test_e2e_grounded.py` and two tests in `security/test_injection_gate.py` fail there. The `.txt` is read by **absolute** `D:/Code/OpenNote/...` path, so it breaks if moved. Never add a new dependency on these; build fixtures in `tmp_path` instead
- Web-search tests are **fully stubbed** — every `TAVILY_API_KEY` is `monkeypatch`ed, no key or network needed. `tests/conftest.py::loopback_http` is the only real socket, and it binds 127.0.0.1
- Headless TUI tests use `app.run_test()`. Unmounted widgets raise `NoScreen` on `screen` access — `Transcript._reveal` is hardened for this, so mirror that if you add another one. **The `_wait_idle` helper requires `settle=3` consecutive idle polls** (4 files, 15 call sites) because several paths never call `set_busy` — returning on the first non-busy poll asserts on a half-finished turn. When a handler is synchronous, call it directly and `await pilot.pause()`; that is not timing-sensitive
- E2E loads embedding weights (~90 s first call, module-cached). `py -m opennote artifacts check -n <nb>` is the fast live self-check (fallback path, no LLM cost)
- The 4 TUI files are ~100–130 s of the 3 min; run them separately while iterating
- `test_schemas_have_both_tools` (`tests/test_agents_tools.py`) pins the 5 core tools; dynamic tools arrive via `get_tool_schemas`
- `scripts/measure_grounding.py` is a **diagnostic, not a test** — it makes real LLM calls

## Architecture that isn't obvious from filenames

- Two *different* answer validators, and which one runs depends on the path:
  - `agents/loop.py` + `submit_grounded_answer` → `filter_grounded_answer` (two-tier: `quote_span` 0.85 **and** claim-text coverage 0.6). Surfaces drops as a TUI info line
  - everything else (free-form replies, `opennote ask`) → `validate_freeform_answer`, a legacy heuristic that **passes on any valid `[n]` marker**. Do not treat it as load-bearing
- `security/delimit.py` owns `<source>` framing. `render_source_block` is the shared builder, called from exactly three places — `agents/loop.py:118`, `chat/prompt.py:108`, `chat/context_budget.py:94` — and `transcript.py:107` still builds its own `<source provenance="derived">` wrapper with a local f-string, using only the shared *escape*. Add a renderer there rather than another inline f-string. `escape_delimited(text, tag)` is the general escape; `escape_source_content` wraps `tag="source"`; `render_worker_answers` frames model-stage output in `<worker-answer>`. It escapes **both** its own tag and `source` — a worker must not be able to forge a source boundary
- `websearch._fetch_guarded` is the *only* network fetch of untrusted URLs (3 call sites: Tavily enrichment, `read_page`, `ingest <url>`). It canonicalises the host, resolves it, and walks the redirect chain itself — trafilatura follows redirects inside one urllib3 call, so a pre-request check cannot see where it lands
- `plugins/loader.py:PluginLoader.load` is the single choke point for plugin loading, gated on `OPENNOTE_ALLOW_PLUGINS` (warn-once, then fail closed). Built-ins are exempt. Every caller — TUI sidebar, capability probe, both agent-turn sites, lazy loads, CLI — inherits it
- `retrieval/retriever.py` — hybrid BM25+vectors; adaptive `top_k` 5→8→12 by corpus size (`engg_choices.md:E1/E2`), so it is **constant per notebook**, not per turn
- `opennote/artifacts.py` — `save_artifact` → `<notebook>/artifacts/`, YAML frontmatter `kind/title/created/prompt_version/sources` (`PROMPT_VERSIONS` stamps it); mindmap helpers `parse_mindmap`, `to_ascii_tree`, `short_artifact_display`
- `audio/tts.py:explain_audio` — groq→openai→gemini→edge-tts, degrades to `.md`; only groq is live-verified. `video.py:explain_video` — slides→TTS→ffmpeg, degrades at each stage and takes **slide JSON**, not a raw topic
- Plugins: `.opennote/plugins/*.py` (gated). Agents: `.opennote/agents/*.md` frontmatter `mode: primary|subagent|all`, optional `model:` override. Skills are `SKILL.md` under `./skills/`, `~/.agents/skills/`, etc., are **model-invoked**; users arm one via TUI `/use <skill> [task]`
- No `temperature` is set on any provider call. Answer variance across runs is model sampling, not retrieval — don't go hunting for a retrieval bug
- `opennote/cli.py:app` is the Typer entry; bare `opennote` with no subcommand launches the TUI; `opennote/__main__.py` enables `py -m opennote`
- `agents/loop.py:agent_turn` — multi-round tool loop; `ToolContext` decouples tools from `Retriever`
- TUI (`opennote/tui/`): Tab cycles `ask→search→studio`; commands in `tui/commands.py` (see `COMMAND_REFERENCE.md`); chrome is **ASCII-only**; sidebar hides below `SIDEBAR_MIN_WIDTH = 112`

## Env vars that change behaviour

`OPENNOTE_HOME` (notebook store; defaults to `cwd/.opennote`, but `resolve_home()` adopts an **ancestor's** `.opennote` if one exists) · `OPENNOTE_ALLOW_PLUGINS` · `OPENNOTE_ALLOW_SKILL_SCRIPTS` · `TAVILY_API_KEY` · `SUPERMEMORY_API_KEY` / `_API_BASE` / `_CONTAINER_TAG`. LLM keys are BYOK and live in the OS keychain, never the env — read them with `auth/keychain.resolve_key`, don't add a file.

## Conventions

- Match file-local style over ruff ideals. TUI code catches `Exception` broadly by design
- **`ledger.md`** gets one row per fix (`| L<n> | SEV | location | ... |`) — **next is L184**. **`engg_choices.md`** gets one per design call (`## E<n>`) — **next is E27**
- `security/scan.py` is *telemetry, not a gate*, so the `<source>` delimiter plus an explicit prompt rule is the whole prompt-injection defense. Every template in `prompt_templates/` that receives retrieved text must state the data-not-instructions rule; `security/test_prompt_injection_rule.py` enforces this and classifies every template deliberately
- **A silent fallback is a bug, not graceful degradation.** A `logger.warning` proves nothing: the TUI pins the `opennote` logger to `WARNING` with a `NullHandler`. Where a feature can degrade, assert on the *result* so a permanently dead path cannot look healthy — the Tavily enrichment fetch had been dead for its whole life behind an `except Exception` and nobody noticed
- **Do not tune `_TEXT_SUPPORT_THRESHOLD` from `tests/security/test_grounding_calibration.py`.** That corpus is an adversarial boundary probe, not a sample; it once looked like a 50% false-drop rate. Measure with `py scripts/measure_grounding.py --notebook notebook-1 --provider groq`, which costs LLM calls. Current value 0.6 is measured, not guessed
- User docs live in `docs/0-START-HERE|1-INSTALLATION|2-CORE-CONCEPTS|3-USER-GUIDE/index.md`, linked from the README header — keep them short and command-accurate
- Runtime state (`.opennote/`, notebooks, `auth.json`, `run-*/`) is gitignored — never commit it
