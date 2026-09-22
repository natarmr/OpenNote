# Engineering Choices

Living record of intentional engineering decisions. Distinct from `ledger.md` (bugs).
Each entry: rationale, defaults, escape hatch, status.

## E1. Hybrid BM25 retrieval — default ON
- **Default:** `Retriever(use_bm25=True, bm25_alpha=0.5)`, `ask(use_bm25=True)`, `agent_turn(use_bm25=True)`. CLI `opennote ask|search|golden` default `--bm25`; `--no-bm25` disables.
- **Rationale:** Exact technical terms ("Stable LatentMoE", "gated MLA", "Kimi Delta Attention") favor keyword match; vector similarity alone misses rare tokens. `Bm25Retriever` (`opennote/retrieval/bm25.py:43`) is local/free — it reads Chroma directly (`bm25.py:43-71`), no embedding load, so the hybrid cost is only a second fetch of `k*2`.
- **Implementation:** `retriever.py:108-116` fetches `k*2` vector + `k*2` BM25, fuses via `hybrid_search(alpha)`. `Bm25Retriever` snapshots at construction; long TUI sessions rebuild the retriever per turn (stale index avoided).
- **Escape:** `--no-bm25` (CLI), `use_bm25=False` (API), `bm25_alpha` tunes blend (0=BM25, 1=vector).
- **Status:** implemented.

## E2. Adaptive top_k — scales with corpus size
- **Default:** When `top_k` is `None` (CLI default, new), `Retriever` picks `5 if chunks < 50 else 8 if chunks < 300 else 12` via `collection.count()` at construction, capped 25 (tool limit `agents/tools.py:162`). Explicit `top_k=int` still honored.
- **Rationale:** kimi-47p → 339 chunks needs broader context than a 1-file notebook; fixed 5 wastes tokens small / starves large. 800/120 chunking (`ingest/chunking.py:53`) is general and unchanged — top_k is the query-time knob.
- **Escape:** `--top-k N` (CLI), `top_k=N` (API), `--top-k` overrides adaptive.
- **Status:** implemented.

## E3. Jinja file-based prompts — user-editable, no code edit
- **Files:** `opennote/prompt_templates/ask_system.jinja`, `ask_post.jinja`, `planner.jinja`, `worker.jinja`, `synthesizer.jinja`; renderer `opennote/chat/render.py`.
- **Rationale:** Parity with lfnovo/open-notebook (`prompts/`), editable without code changes; Jinja `{% if %}` for notebook context (`name`/`project`) and citation allowlist (`Valid tokens: [1]..[N]`). Escaping stays in Python (`escape_source_content`) never in template.
- **Status:** implemented (`jinja2>=3.0.0`, `package-data` in `pyproject.toml`).

## E4. Opt-in multihop ask — planner → workers → synthesizer
- **API:** `ask(multihop=False)`, CLI `--multihop`. Planner capped 3 sub-queries (`chat/planner.py`), per-term `retriever.search(term)` with dedup by chunk id, fallback to single-shot on planner JSON parse failure.
- **Rationale:** Better recall on comparative questions ("When was X and how many Y?") at cost of N+2 LLM calls; off by default for latency.
- **Status:** implemented.

## E5. Positional `[n]` citations — kept
- **Choice:** `[1]`, `[2]` per result set, not stable `source:<hash>` IDs.
- **Rationale:** `validation/citation.py` + `chat/citations.py` + `used_sources` footer are built around positional cites; simpler, less hallucination within one turn. Allowlist line ("cite ONLY [1]..[N]") is the anti-hallucination guard.
- **Status:** kept.

## E6. Injection defense — tagged sources + pre/post reminders
- **Pattern:** `<source id="n" page="...">[n] citation\ncontent</source>` with `escape_source_content` (`chat/prompt.py:98`); `SYSTEM_PRE_TAGGED` + `SYSTEM_POST_TAGGED` remind the model sources are DATA. Kept while adopting lfnovo staged prompts.
- **Status:** kept (ported to Jinja).

## E7. TTS adapter chain — groq → openai → gemini → edge-tts, degrade to .md
- **Order:** `audio/tts.py` tries each backend in order; only after all fail writes `.md` transcript (`backend="none"`). Groq voice `autumn` + `response_format="wav"` (live-probed fix `tts.py:70,101`); edge-tts voice `en-US-AriaNeural` (`tts.py:165`).
- **Status:** fixed & probed (real Groq mp3 311KB, edge-tts 7.2.8).

## E8. Video 3-stage — graceful degradation
- **Stages:** 1: `.png` slides + `.md` script always succeeds; 2: per-slide `.mp3` iff any TTS succeeds; 3: `ffmpeg` mux to `.mp4` iff images + audio present and `ffmpeg` on PATH (`video.py:432`). Missing TTS/ffmpeg surfaces honest `error`, never fake success.
- **Status:** probed (2 pngs + 2 mp3s → mp4).

## E9. Ingest caps — general, not kimi-specific
- **Caps:** `_MAX_INGEST_FILES=500`, skip `_SKIP_DIRS` (hidden dirs), `_MAX_SLIDES=20`, chunk validation (`size>0`, `overlap<size`, `batch_size>=1`) (`pipeline.py`, `video.py`, `chunking.py`). Fixed for any large ingest; kimi was just the stress case.
- **Status:** implemented.

## E10. Embedding model cache — no reload per Retriever
- **Cache:** `_MODEL_CACHE` keyed by `(model_name, device)` in `store/vectors.py:145`; saves ~90s per `Retriever()` construction on Windows.
- **Status:** implemented.

## E12. Context meter — real tokens, % of window, $ spent (opencode-style)
- **Source of truth:** provider-reported `response.usage` captured in `chat/client.py` (`TokenUsage`, `client.last_usage`, `ChatResponse.usage`) across all 5 backends (OpenAI-compat, Anthropic, local llama-cpp). Exact counts when reported; chars/4 estimate marked `~` when the gateway omits usage. No tiktoken (user decision).
- **Summation:** loop sums `TokenUsage` across rounds, multihop sums planner+workers+synth (opencode-style session totals). Fill and spend are separate contracts — per-turn fill never replaces session totals.
- **Display (opencode-style):** 32-col right `SideBar` (`tui/widgets/sidebar.py`, hidden below 112 cols) with Session (notebook + created), Context (live `usage.render()`), Services (provider/model + active skills/plugins), footer (cwd:branch + version). Persistent `ctx` readout in the prompt-bar meta row + on-demand `/context` panel. CLI `ask`/`chat` print the panel. Metering failures log a warning instead of silently hiding the panel.
- **Wiring:** `ask()` attaches `AskResult.usage` via `_track_usage()`; `agent_turn()` from summed round usage; `ChatScreen._sync_sidebar()` refreshes session/services/footer every turn. Spend accumulates in `<notebook>/usage.json`.
- **Glyph policy:** ASCII only in TUI chrome (`|`, `-`, `->`); the `┬╖`/`ΓÇ*` mojibake literals are removed. Model ids shortened for display (`models/gemini-3.5-flash` → `gemini-3.5-flash`, 24-char cap).
- **Status:** implemented, live-verified (Groq reports exact: 543 in / 13 out; TUI screenshot shows sidebar + footer).

## E11. Test conventions
- **Stub:** `conftest.stub_embedder` (deterministic random 16-d) avoids HF downloads; `notebook_manager` uses `tmp_path` with `OPENNOTE_HOME` env var.
- **Large files:** slice to 2 pages in tests (`test_e2e_grounded.py` via `pypdf` writer) to keep time <40s; full 47-page kimi is manual only.
- **No live APIs in committed tests:** `FakeClient`/`FakeRetriever` mimic LLM/retrieval; `py` launcher not `python` per `AGENTS.md`.
- **Status:** convention.

## E13. Context budget + thinking-strip (upstream context_builder/text_utils parity)
- **Budget:** `chat/context_budget.py` — `fit_tagged_context(results, budget_chars=12000)` with binary-search prefix truncation + `[truncated: showing first K of M chars]` notice inside `<source>`; `omitted_budget` items skipped (never notice-only). Chars-based (no tiktoken per E12); CLI `--context-budget` (0=unlimited), plumbed through `ask()` single-shot + multihop workers/synth.
- **Thinking-strip:** `chat/clean.py:clean_thinking_content()` removes `<think>/<thinking>` blocks, tolerates missing opener, bypasses >100KB; applied in `_single_shot`, `_multihop` workers/synth, `agents/loop.py` final + thought_signature fallback.
- **Status:** implemented (`tests/test_context_budget.py` 8 tests).

## E14. Retrieval parity benchmark (800/120 vs 400/60 vs 1500/150)
- **Method:** `tests/test_retrieval_parity.py` — synthetic rare-term corpus (E1 terms), deterministic BM25-forced recall (`alpha=0` → 1.0 on rare terms), chunk-count scaling assertion, `hybrid_search` alpha unit (0→BM25 wins, 1→vector wins, 0.5 blends).
- **Outcome:** keeps 800/120 default; 400/60 + 1500/150 ingest paths verified via `chunk_size/chunk_overlap` plumbing. No default change — decision rule recorded: re-run matrix on real golden before changing global default.
- **Status:** implemented (4 tests).

## E15. Artifacts convergence (upstream Transformation/SourceInsight parity, no DB)
- **Frontmatter:** `save_artifact(..., prompt_version, sources)` writes YAML `kind/title/created/prompt_version/sources(JSON)` + body; `load_artifact()`/`strip_frontmatter()` back-compat; `PROMPT_VERSIONS` stamps per kind.
- **Jinja studio:** `prompt_templates/studio_*.jinja` (study/faq/briefing/timeline/summary/questions) via `chat/render.py` with legacy string fallback (`_render_studio`); new `kind="insight"` + `make_insight()` per-source analog of `SourceInsight`.
- **Export:** `opennote artifacts export --notebook X --format json` emits `export_artifact_json()` list (kind/title/created/prompt_version/sources/body).
- **Status:** implemented (`tests/test_artifacts_convergence.py` 3 tests); `test_artifacts_tts_video.py:50-58` updated for frontmatter.

## E16. Question-aware answer depth (ask prompt shaping)
- **Rule:** `ask_system.jinja:5` — factoids (who/when/where/how-many/which) 1–2 sentences; explanatory (what/why/how/explain/compare) direct answer + one cited paragraph (mechanism/details/caveats); every added sentence still cited. Tail reminder in `ask_post.jinja`; parity one-liners in `worker.jinja`/`synthesizer.jinja`.
- **Rationale:** grounding rules alone reward minimal answers (Qwen returned bare one-liners); validator passes on ≥1 valid `[n]` so depth needs prompt pressure, not gate changes.
- **Verified:** 48 grounding tests green; live probe (notebook-1/kimi, `qwen3.8-27b`): factoid 266 chars/2 sentences, explanatory 1581 chars structured, all cited; golden recall@12 = 1.00 unchanged.
- **Status:** implemented.

## E17. /video slide-script construction + live-run provider rule
- **Rule:** `chat.py:_run_video` never passes a raw topic to `explain_video` (it only accepts slide JSON). With a provider: `_slides_script_json()` — LLM-grounded 3–5 slides (`title/bullets/narration` + `[n]` citations) over the same 8×800-char chunk context as text kinds, fence-tolerant parse, `ValueError` on garbage (honest `StudioFailed`, no silent template pass). Without a provider: `_fallback_slides_json()` builds ≤4 slides deterministically from chunk snippets. `save_video_artifact` error branch materializes `video-error.md` under the artifacts dir — a returned path must always exist.
- **Provider rule:** live studio runs default to groq `qwen/qwen3.8-27b` (verified grounded 2026-09-22); google flash models were 503/404-unreliable that day. Escalation stays retry-once → change model (live `auth models` list) → change provider → stop-and-report; no-LLM templates never count as a live PASS.
- **Status:** implemented (`tests/test_studio_modes.py` +4; live notebook-1 8/8 incl. 1.2 MB `slideshow.mp4`).
