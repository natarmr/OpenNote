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
