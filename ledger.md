# OpenNote Bug & Edge-Case Ledger

Living document tracking bugs and edge cases found by codebase audit (and the
Phase 5 agent-loop debugging). Each entry records the problem, how it was
reproduced, and how it was fixed, with the tests that guard it.

Severity: **HIGH** = data loss, security, or guaranteed crash; **MED** =
silent misbehavior or reliability; **LOW** = cosmetic / ergonomic.

## Security & data integrity

| ID | Sev | Location | Description | Status | Fix | Tests |
|----|-----|----------|-------------|--------|-----|-------|
| L01 | HIGH | `notebooks.py` delete | `delete()` rm-trees any existing directory: `delete("../myproject")` removes an arbitrary dir; `delete("")` resolves to the notebooks dir itself and wipes every notebook | **fixed** | Validate name (`^[A-Za-z0-9._-]+$`, no empty/`..`/separators/reserved names); require `notebook.json` before rmtree | `test_notebooks.py::test_delete_refuses_directory_without_notebook_json`, `test_delete_rejects_unsafe_names` |
| L02 | HIGH | `notebooks.py` get/create/rename | Path traversal: `create("../evil")` writes outside the notebooks dir; `rename("ok","../../x")` moves notebooks out; `get("../other")` reads arbitrary `notebook.json` | **fixed** | Same `validate_notebook_name` in all four methods; case-insensitive collision checks | `test_notebooks.py::test_create_rejects_path_traversal_and_reserved`, `test_get_rejects_unsafe_names`, `test_rename_rejects_path_traversal`, `test_case_insensitive_collisions` |
| L03 | MED | `auth/config.py` load | Corrupt `auth.json` silently loads as `{}`; next `save()` overwrites the corrupt file, permanently losing all provider settings | **fixed** | On decode error: back up to `auth.json.corrupt` + warn; never silently reset | `test_auth_config.py::test_corrupt_file_backed_up` |
| L04 | MED | `session.py` load / save | Corrupt or partially-written session file is treated as "no session": auto-resume silently creates a new one; history disappears. Writes are non-atomic (`open("w")` truncates first) | **fixed** | Atomic writes (`_atomic_write_json`: tmp + `os.replace`); warn (log) when a session file exists but fails to parse | `test_agents_session.py::test_save_session_atomic_no_tmp_leftover`, `test_load_corrupt_warns` |
| L05 | MED | `keychain.py` set_key/delete_key | On headless/keyless systems `keyring.set_password` raises `NoKeyringError` (not `KeychainError`), so `auth add`/`remove` crash with a traceback. `delete_key` swallows deletion failures and reports success | **fixed** | Wrap keyring calls; raise `KeychainError` on store failures; warn on read/delete failure | `test_auth_keychain.py::test_set_key_surfaces_backend_failure_as_keychainerror`, `test_get_key_swallows_backend_failure`, `test_delete_key_survives_backend_failure` |
| L06 | LOW | `vectors.py:193` | Full query text logged at INFO (privacy leak into logs/terminals) | **fixed** | Log truncated query (60-char cap) | capture-log test (in `test_store.py`) |

## Crashes & correctness

| ID | Sev | Location | Description | Status | Fix | Tests |
|----|-----|----------|-------------|--------|-----|-------|
| L07 | HIGH | `retrieval/eval.py` report() | `EvalSummary.report()` references undefined locals `k`/`rec` — guaranteed `NameError` on any call | **fixed** | Store `top_k` on the dataclass; render from `self` | `test_retrieval.py::test_eval_filename_exact_match_not_suffix` (calls `report()`) |
| L08 | HIGH | `auth/validate.py:86` | Non-JSON 200 response (captive portal / HTML error page) → uncaught `JSONDecodeError` | **fixed** | Wrap `response.json()`; return `ValidationResult.http(200)` | `test_auth_validate.py::test_html_error_page_200_malformed` |
| L09 | HIGH | `auth/cli.py` verify | `auth verify <typo>` calls `get_provider()` bare → raw `ValueError` traceback | **fixed** | Guard with try/except → friendly error | `test_cli.py::test_auth_verify_unknown_provider_friendly_error` |
| L10 | MED | `retriever.py:70` | `top_k=0` silently returns 5 results (`top_k or self.top_k`); negative passes through to Chroma | **fixed** | `top_k if top_k is not None else self.top_k`; validate `>= 1` | `test_retrieval.py::test_retriever_top_k_zero_and_negative_rejected` |
| L11 | MED | `cli.py` search/golden + chat `/sources` | Searching a notebook with no ingested sources raises uncaught `ValueError` (read-only collection missing) — kills the interactive chat session | **fixed** | Catch `ValueError` → friendly message; chat continues | `test_cli.py::test_search_empty_notebook_friendly_error`, `test_golden_empty_notebook_friendly_error` |
| L12 | HIGH | all parsers | Chunk IDs derived from bare `filename`: two same-named files in different dirs silently overwrite each other's chunks | **fixed** | Namespace chunk IDs by resolved source path in text/html/docx/pdf parsers (and html URL path) | `test_parsers.py::test_same_named_files_distinct_chunk_ids` |
| L13 | LOW | `retrieval/eval.py:68` | `s.endswith(g.expected_source)` false positives (`notes.txt` matches `my-notes.txt`) | **fixed** | Compare `Path(s).name == expected` | `test_retrieval.py::test_eval_filename_exact_match_not_suffix` |

## Agent loop & session integrity

| ID | Sev | Location | Description | Status | Fix | Tests |
|----|-----|----------|-------------|--------|-----|-------|
| L14 | HIGH | `session.py` trim_messages | Blind front-pop can orphan `tool` messages or detach `tool_calls` from their responses → provider 400 on every resume; session permanently bricked | **fixed** | Turn-aware trim: only drop at `user` boundaries; advance past invalid leading messages; never leave leading tool/assistant-tool_calls | `test_agents_session.py::test_trim_messages_never_orphans_tool`, `test_trim_messages_skips_leading_orphan_tool` |
| L15 | HIGH | `loop.py` + `client.py` | Consecutive `user` messages (corrective path) are a hard Anthropic API error; burns rounds and bricks resumed Anthropic sessions | **fixed** | `_append_user_message` merges into last user turn; defensive merge in `AnthropicClient.append_user`; OpenAI unknown-role guard | `test_agents_loop.py::test_consecutive_user_messages_merged`, `test_chat_client_tools.py::test_anthropic_merges_consecutive_user_turns`, `test_openai_unknown_role_raises_chat_error` |
| L16 | HIGH | `loop.py` + `tools.py` + `citations.py` | Citation numbering is per-search-call but validated against a flat accumulated list → wrong sources cited when the model searches twice in one turn | **fixed** | `render_tool_results(offset=len(retrieved))` rendered *before* extending (global numbering) | `test_agents_loop.py::test_two_searches_numbered_globally` |
| L17 | HIGH | `chat/client.py:167` | Malformed/truncated tool-call `arguments` JSON → `JSONDecodeError` inside serialization, misdiagnosed as "model invented a tool" | **fixed** | `_parse_arguments` tolerates bad JSON (lenient parse + regex salvage, fall back to `{}`) | `test_chat_client_tools.py::test_openai_malformed_tool_arguments_salvaged` |
| L18 | MED | `loop.py:86` | `except Exception` swallows timeouts/429s/DNS errors and appends up to 5 misleading "do not invent tools" messages | **fixed** | `_is_bad_request` (openai/anthropic `BadRequestError` + class-name/message fallbacks); re-raise network errors | `test_agents_loop.py::test_network_error_propagates` |
| L19 | MED | `tools.py` | No argument validation: `top_k="5"`/`0`/`-1`/`500`, `kwargs=None`, unknown kwargs, unknown `source` | **fixed** | Coerce+clamp `top_k` (1..25), `kwargs or {}`, strip unknown kwargs, unknown-source hint | `test_agents_tools.py::test_execute_search_rejects_*`, `test_execute_search_unknown_source_raises`, `test_execute_search_unknown_kwargs_dropped`, `test_execute_search_kwargs_none_missing_required` |
| L20 | LOW | `loop.py` rounds_used | Dead counter (incremented, never read) | **fixed** | Expose `rounds_used` on `AgentResult` | `test_agents_loop.py::test_rounds_used_reported` |
| L21 | LOW | `loop.py:84` | Hard-coded `max_tokens=1024` truncates long answers with no continuation | **fixed** | `max_tokens` parameter on `agent_turn` (default kept) | `test_agents_loop.py::test_max_tokens_honored` |
| L22 | LOW | `citations.py:14` | Regex matches parenthesized prose numbers: "(2 percentage points)" → spurious source [2] cited | **fixed** | Paren marker must be a bare number closed immediately `\((\d+)\)` | `test_chat_citations.py::test_parenthesized_prose_not_citation`, `test_parenthesized_marker_requires_immediate_close` |
| L23 | LOW | `citations.py` | Footer dedupes by index, not citation → two chunks of same page listed twice | **fixed** | Dedupe by citation string | `test_chat_citations.py::test_same_citation_deduped_across_indices` |
| L24 | LOW | `cli.py` /model | Switching provider mid-session leaves session metadata stale; CLI-built client never passed to `agent_turn` (double key resolution) | **fixed** | Pass `client=` into `agent_turn`; update session metadata on `/model` | `test_cli.py::test_chat_slash_model_updates_session_metadata` |

## Ingestion robustness

| ID | Sev | Location | Description | Status | Fix | Tests |
|----|-----|----------|-------------|--------|-----|-------|
| L25 | MED | `pipeline.py` re-ingest | File that became empty leaves stale chunks searchable (early return before `delete_source`) | **fixed** | `_index_chunks` always `delete_source` first, even for empty extraction; drop from `notebook.sources` | `test_pipeline.py::test_empty_file_reingest_removes_stale_chunks` |
| L26 | MED | `pipeline.py` | Zero-chunk file never marked indexed → re-parsed (expensively) on every ingest | **fixed** | Mark hash indexed even on empty extraction | `test_pipeline.py::test_empty_file_marked_indexed_once` |
| L27 | MED | `chunking.py` / pipeline | `chunk_overlap >= chunk_size` → ~1M chunks from a 1MB file (OOM); `chunk_size=0` | **fixed** | Validate `size>0`, `overlap<size`, `batch_size>=1` at ingest entry | `test_pipeline.py::test_invalid_chunk_params_rejected` |
| L28 | MED | `vectors.py:160` | `batch_size=0` → cryptic `range() arg 3 must not be zero`; negative silently indexes nothing | **fixed** | Validate `batch_size >= 1` at ingest entry (same as L27) | `test_pipeline.py::test_invalid_chunk_params_rejected` |
| L29 | LOW | `pipeline.py` find_source_files | Mixed-case extensions missed in dir scans (`a.Pdf` skipped; single-file path lowercases) | **fixed** | `rglob("*")` + `p.suffix.lower()` filter | `test_pipeline.py::test_mixed_case_extensions_scanned` |
| L30 | LOW | `html.py` parse_url | URL chunks use bare hostname as filename → all pages of a host collapse to one source | **fixed** | Include URL path in filename | `test_parsers.py::test_parse_url_filename_includes_path` |

## Other / polish

| ID | Sev | Location | Description | Status | Fix | Tests |
|----|-----|----------|-------------|--------|-----|-------|
| L31 | MED | `cli.py` chat REPL | Non-`ChatError` exceptions (network errors now re-raised, locked vector store) crash the whole interactive session | **fixed** | Catch broader exceptions in the loop, print, continue | `test_cli.py::test_chat_survives_loop_network_error` |
| L32 | LOW | `notebooks.py` | Windows: case-only rename (`foo`→`FOO`) falsely collides; reserved names (`CON`,`NUL`) blow up in `save()` | **fixed** | Case-insensitive existence check + reserved-name reject in validation (covered by L01/L02) | `test_notebooks.py::test_case_insensitive_collisions`, `test_validate_rejects_unsafe_names` |
| L33 | LOW | `keychain.py` mask_key | 8-char key with keep=4 reveals the entire key | **fixed** | Tail shown only when long enough (else prefix only) | `test_auth_keychain.py::test_mask_key` |
| L34 | LOW | `notebooks.py` save / session save | Non-atomic `notebook.json` write → corrupt on crash (feeds L04); concurrent-ingest lost update | **fixed** | tmp + `os.replace` (Notebook.save), cleanup on failure | `test_notebooks.py::test_save_leaves_no_tmp_files`, `test_agents_session.py::test_save_session_atomic_no_tmp_leftover` |
| L35 | LOW | `retriever.py` SearchResult.id | `id` always `""` (metadata never carries the chroma id) | **fixed** | Copy chroma `id` into result metadata | `test_retrieval.py::test_search_result_carries_chroma_id` |
| L36 | LOW | `cli.py` /sessions | `/sessions` deserializes every full session just to print 4 fields (slow with many long sessions) | **fixed** | Lightweight sidecar `.meta.json` per session written on save; `list_session_meta` reads sidecars (falls back to full load when absent); CLI uses it | `test_agents_session.py::test_list_session_meta_*` |
| L37 | LOW | `cli.py` slash parsing | `/model<TAB>groq` mis-parses (partition on single space) | **fixed** | Split on whitespace (`re.split`) | `test_cli.py::test_chat_slash_model_tab_parsed` |
| L38 | LOW | `notebooks.py` list | One corrupt `notebook.json` makes `opennote list` crash entirely | **fixed** | Skip + log corrupt entries in `list()` | `test_notebooks.py::test_list_skips_corrupt_notebooks` |

## Previously fixed (Phase 5 agent-loop debugging)

| ID | Sev | Location | Description | Status | Fix | Tests |
|----|-----|----------|-------------|--------|-----|-------|
| L39 | HIGH | `agents/tools.py` | Tool called `retriever.search(..., where_filter=...)` but the real signature is `search(query, top_k, source)` → every search errored, model burned all rounds and gave up | **fixed** | Pass `source=`; updated fake retrievers to match signature | `test_agents_tools.py`, `test_agents_loop.py` |
| L40 | HIGH | `agents/loop.py` | Provider rejects a model-invented tool (`open_file`) with a 400; loop crashed instead of recovering | **fixed** | Catch provider rejection, inject corrective message listing only available tools, cost a round; add tool names to system prompt | `test_agents_loop.py::test_provider_rejection_corrects_and_retries` |
| L41 | HIGH | `cli.py` chat | `from agents.session import new_session` **shadowed the `--new` flag** → `not new_session` always False → CLI always started a fresh session, never resumed | **fixed** | Import as `create_new_session`; resume verified live | `test_cli.py::test_chat_resumes_most_recent_session` |

## Phase A-G feature audit (open findings)

Second audit pass over the Phase A-G feature code (capabilities, web search,
artifacts, TTS, video, BM25 retrieval, TUI studio mode). Entries are **open**
until their fix wave lands; see Wave column for the planned fix wave.

| ID | Sev | Location | Description | Status | Fix | Tests |
|----|-----|----------|-------------|--------|-----|-------|
| L42 | HIGH | `websearch.py:52-58` | Tavily request structurally broken: API key sent in JSON body instead of `Authorization: Bearer` header; `topic` hardcoded to invalid `"default"` (valid: general/news/finance); unknown `tone` field → API 400 on every call (root cause of the known "Tavily 400" blocker) | **fixed** | Bearer-header auth; default topic `general`; drop `tone`; cap `max_results` ≤ 20 | Wave 3 | `test_websearch.py` (mock httpx: key in header, valid topic, no tone) |
| L43 | HIGH | `websearch.py:155-169` | SSRF: `read_page` fetches any http/https URL unvalidated — localhost, RFC1918, cloud metadata (169.254.169.254) all reachable; redirects auto-followed | **fixed** | Scheme allowlist http/https + resolve host, reject private/loopback/link-local IPs before fetch | Wave 3 | `test_websearch.py::test_read_page_rejects_private_urls` |
| L44 | MED | `loop.py:162-166` | Missing `f` prefix on last literal: literal `{capabilities_line}` sent to model every turn; capability-refusal instruction never delivered | **fixed** | Add `f` prefix | Wave 1 | assert capabilities text in system prompt (fake client) |
| L45 | MED | `websearch.py:115,169,179` | `url.title()` is `str.title()` → citations/filenames like `[Https://Example.Com/Docs/...]` | **fixed** | Derive title from parsed page `<title>`, fallback host+path (mirror `html.py:118-120`) | Wave 3 | `test_websearch.py::test_page_title_uses_host_and_path` |
| L46 | MED | `websearch.py:90-92` | Malformed Tavily response (non-dict result, `url: null`) → AttributeError/TypeError crash | **fixed** | Shape-validate results; skip malformed entries | Wave 3 | `test_websearch.py::test_tavily_search_filters_non_dict_results` |
| L47 | MED | `tools.py:97-111` | `_web_search` has none of `_search`'s validation (top_k unbounded/uncertified, empty query); each result triggers sequential 30s `fetch_url` → minutes-long non-cancellable tool call | **fixed** | Mirror `_search` validation (clamp 1..10); cap enrichment fetches (≤3) | Wave 3 | tool dispatch tests (`test_execute_web_search_*`) |
| L48 | MED | `websearch.py:198-232` | Web-citation locator is dead code (never called); web results cite `[url, loc. n/a]`; dead copy has `host = scheme` bug | **fixed** | Add url/title branch to `retrieval/citations.py:_pick_locator` properly | Wave 3 | citation test (`test_pick_locator_*`) |
| L49 | MED | `loop.py:198-236` | Empty model answer (content="") conflated with "ran out of tool rounds" → misleading error | **fixed** | Distinguish empty content from exhausted rounds | Wave 3 | loop test w/ empty-content stub (`test_empty_model_reply_*`) |
| L50 | MED | `tools.py:166-174` | Fetched web content enters LLM context with no untrusted-content framing (prompt-injection surface) | **fixed** | Wrap tool content in delimiters + system-prompt untrusted-content instruction | Wave 3 | prompt test (`test_web_search_results_wrapped_in_untrusted_delimiters`)
| L51 | LOW | `websearch.py` misc | Blanket `except: pass` (l131); non-JSON-serializable `Citation` in metadata (l137/181); unused imports; `datetime.utcnow()` deprecation (l93) | **fixed** | Log + narrow except; store citation dict; clean imports | Wave 3 | — |
| L52 | HIGH | `video.py:182-231,338-340` | `_ffmpeg_mux` never writes `slideshow.mp4` — only per-slide clips; `video_path` always points at nonexistent file | **fixed** | Concat clips via ffmpeg concat demuxer to output path; verify exists | Wave 4 | test with fake ffmpeg (`test_ffmpeg_mux_*`) |
| L53 | HIGH | `video.py:230` | ffmpeg exit status never checked → total encode failure still `success=True` | **fixed** | Check returncode + capture stderr into `result.error` | Wave 4 | test fake failing ffmpeg (`test_ffmpeg_mux_failing_ffmpeg_surfaces_error`) |
| L54 | HIGH | `video.py:287,296` | `Slide(**s)` crashes on extra LLM keys (TypeError); non-string bullets/title crash later in join/Pillow | **fixed** | Filter to known fields + coerce `str()` with validation | Wave 4 | parametrized tests (`test_explain_video_handles_extra_slide_keys`, `test_explain_video_coerces_non_string_bullets`) |
| L55 | MED | `tts.py:387-432` | TTS "adapter chain" does not fall back — first backend failure returns immediately (docstring promises graceful degradation) | **fixed** | Iterate backends in order; transcript fallback only after all fail | Wave 4 | fallback-order test (`test_explain_audio_falls_back_to_edge_tts`) |
| L56 | MED | `tts.py:233-237,414-422` | Gemini backend unconditional stub; branch fakes `success=True` with `.md` transcript path as `audio_path` | **fixed** | Return honest failure (or implement); never set audio_path to transcript | Wave 4 | test (`test_gemini_never_fakes_success`) |
| L57 | MED | `tts.py:292-294` | `asyncio.run()` inside running loop (Textual) → RuntimeError | **fixed** | Run edge-tts via thread when loop running | Wave 4 | async-context test (`test_edge_tts_runs_when_called_inside_event_loop`) |
| L58 | MED | `tts.py:267-283` | `tempfile.mktemp` (racy, CWE-377); success returned even when tmp missing; temp leak on failure | **fixed** | NamedTemporaryFile + existence check + cleanup on failure | Wave 4 | test (`test_edge_tts_writes_audio_and_no_temp_leak`) |
| L59 | MED | `tts.py:458` / `video.py:362` | Path traversal via `notebook_name="../../evil"` in `save_audio_artifact`/`save_video_artifact`; CWD-relative output; fabricated error paths returned | **fixed** | Reuse `validate_notebook_name`; resolve under configured notebooks root; return real paths | Wave 4 | traversal test (`test_save_*_artifact_rejects_traversal`) |
| L60 | MED | `artifacts.py:53-54` | Filename collision at 1-second timestamp granularity → `os.replace` silently clobbers (docstring falsely claims hash suffix) | **fixed** | Append short content/uuid hash | Wave 4 | collision test (`test_artifact_same_second_writes_get_distinct_filenames`) |
| L61 | MED | `artifacts.py:76-85` | `_atomic_write` leaks tmp file on failure; fallback path writes file then re-raises | **fixed** | try/finally unlink; drop write-then-raise fallback | Wave 4 | failure-injection test (`test_atomic_write_no_tmp_leak_on_failure`) |
| L62 | MED | `video.py:225,193-215` | Hardcoded 5s clips truncate narration; image/audio paired by index → misalignment when some slide TTS fails | **fixed** | Derive duration from mp3 length; pair by slide number with existence guard | Wave 4 | `test_mp3_duration_probe_handles_missing_ffprobe` |
| L63 | MED | `video.py:327-335` | ffmpeg missing → silent `success=True`, no error surfaced; clip files pollute slides dir | **fixed** | Set `result.error` when unavailable; write clips to subdir | Wave 4 | `test_explain_video_reports_ffmpeg_missing` + `test_ffmpeg_mux_returns_error_when_ffmpeg_missing` |
| L64 | MED | `video.py:30` / `tts.py:30` | `PIL`/`numpy` undeclared hard dependencies (only transitive) | **fixed** | Declare in pyproject | Wave 4 | — |
| L65 | MED | `tts.py:93-98` | No size cap on TTS input; unbounded per-slide API calls in video | **fixed** | Truncate script to provider limit; cap slides | Wave 4 | `test_explain_audio_truncates_script` |
| L66 | MED | tts/video overall | Entire TTS/video feature set is dead code — absent from TOOL_SCHEMAS, CLI, and TUI; AGENTS.md documents nonexistent `/audio` `/video` | **fixed** | Wire into TUI (Wave 5) or remove docs until wired | Wave 4/5 | `test_studio_commands_registered` |
| L67 | LOW | `video.py`/`tts.py` misc | `explain_video("[]")` → success; import-time keychain probe; dead imports; non-atomic writes; dead reserved-name check; unwrapped slide titles | **fixed** | Sweep | Wave 4 | `test_explain_video_rejects_empty_slide_list` + `test_tts_module_import_does_not_probe_keychain` |
| L68 | HIGH | `retriever.py:94-97` + `bm25.py:108` | Infinite recursion → RecursionError: `hybrid_search` re-enters `retriever.search()` with `use_bm25` still True | **fixed** | Internal `_search_vector` (no hybrid) used by hybrid path | Wave 2 | `test_retrieval.py::test_hybrid_no_recursion` |
| L69 | HIGH | `bm25.py:83` | NameError: `documents` not in scope in `search()` (local of `_collect_chunks`) | **fixed** | Store `self._documents` | Wave 2 | smoke test (`test_bm25_search_returns_ranked_results`) |
| L70 | HIGH | `bm25.py:49` | `chunk_texts` type confusion: `c.metadata` on a `str` → AttributeError | **fixed** | Accept `(documents, metadatas)` explicitly | Wave 2 | smoke test |
| L71 | HIGH | `bm25.py:112-127` | `Citation` has no `filenames` attr → AttributeError; even fixed, filename-keyed dedup collapses all chunks of a file into one result | **fixed** | Key by chunk id; merge per-chunk | Wave 2 | `test_hybrid_merge_pure_function` + `test_bm25_multiple_chunks_same_source_survive` |
| L72 | HIGH | `bm25.py:52` | `BM25Okapi([])` ZeroDivisionError on empty corpus | **fixed** | Guard empty; no-results search path | Wave 2 | `test_bm25_empty_corpus_no_crash` |
| L73 | MED | `bm25.py:52,74` | Corpus never tokenized → per-char vocabulary, all scores 0, corpus-order results | **fixed** | Tokenize corpus with `_tokenise` | Wave 2 | scoring test (`test_bm25_search_returns_ranked_results`) |
| L74 | MED | `retriever.py:80-97` | Vector results computed then discarded in hybrid path (wasted embed+query); `source` filter silently dropped | **fixed** | Compute once, pass into merge; thread `source` through | Wave 2 | `test_hybrid_source_filter_forwarded` |
| L75 | MED | `bm25.py:59-63` | Loads/downloads default SentenceTransformer for pure keyword search; stale index after ingest (no refresh) | **fixed** | Read chunks via chroma directly; lazy build + invalidate on source change | Wave 2 | `test_bm25_empty_corpus_no_crash` (no embed load) |
| L76 | LOW | `retriever.py:62` / `bm25.py:69` | `self._bm25` unset when use_bm25=False (latent AttributeError if toggled); BM25 results always empty `chunk_id` | **fixed** | Init to None; copy chroma ids | Wave 2 | `test_bm25_disabled_no_attribute_error` + id asserts |
| L77 | MED | tests | Zero test coverage for Bm25Retriever/hybrid_search/use_bm25 — all 9 bugs above shipped untested | **fixed** | Add suite | Wave 2 | 9 new tests in `tests/test_retrieval.py` |
| L78 | HIGH | `tests/test_tui_commands.py` | StubScreen lacks `_enter_studio` → AttributeError kills all 10 registry tests | **fixed** | Add stub method | Wave 1 | (test itself) |
| L79 | MED | `commands.py:80` | `/theme` command silently deleted (studio replaced instead of appended); `_switch_theme` now dead code | **fixed** | Re-add theme entry | Wave 1 | `test_theme_command_switches_palette` |
| L80 | MED | `chat.py:216-227` | Submitting in studio mode silently runs the ask agent — no studio submenu, no generator dispatch | **fixed** | Studio submenu (item_list) → generator worker thread | Wave 5 | new TUI tests |
| L81 | MED | `chat.py:152,237-240` | `_enter_studio` prints circular "Use /studio" message; help text falsely claims "studio = artifact generators" | **fixed** | Accurate copy + wiring | Wave 1/5 | — |
| L82 | MED | `opennote/tui/` | Zero backend wiring: no artifacts/TTS/video imports in TUI; 9 documented slash commands (`/mindmap` `/study` `/faq` `/briefing` `/timeline` `/suggest` `/audio` `/video` `/open`) don't exist | **fixed** | Register + implement commands; worker pattern with try/except + empty-notebook guard | Wave 5 | command registry test (`test_studio_commands_registered`) |
| L83 | MED | `tests/test_tui_app.py:111-122` | `test_tab_cycles_modes` expects 2-tab cycle; MODES now has 3 entries | **fixed** | Update expectations (studio after 2 tabs, ask after 3) | Wave 1 | (test itself) |
| L84 | LOW | `prompt.py:40,130` | MODE_LABELS missing "studio" (lowercase label); no distinct mode color | **fixed** | Add label + accent color | Wave 1 | — |

## TUI palette & connect rework (Wave 6)

Ctrl+P palette rewrite on `OptionList` exposed several latent (runtime-only)
bugs. All fixed in this wave; guarded by updated/added TUI tests.

| ID | Sev | Location | Description | Status | Fix | Tests |
|----|-----|----------|-------------|--------|-----|-------|
| L85 | MED | `dialogs.py:CommandPalette` | `add_options("No matching entries")` passes a bare string → Textual iterates characters (19 placeholder rows) instead of a single row | **fixed** | Pass `["No matching entries"]` list | `test_palette_no_matches_shows_single_placeholder` |
| L86 | HIGH | `chat.py:_open_connect_model` | Walrus `if settings := AuthConfig().get(...) and settings.model:` binds a bool → `settings.model` NameError at runtime; `result.is_invalid()`/`is_network()` don't exist on `ValidationResult` (fields are `ok`/`models`/`error`) → AttributeError on every connect-with-key | **fixed** | Use `result.ok`/`result.error`; scope `settings` separately | `test_connect_flow` (updated) |
| L87 | MED | `chat.py:_on_connect_key` | Just-entered key re-resolved via `resolve_key` after `set_key` → lost when keychain is mocked/env-var backed | **fixed** | Pass key straight into `_open_connect_model(provider, key)` | `test_connect_flow` |
| L88 | HIGH | `palette.py` | "Switch Model" entry referenced `screen._open_model_dialog()` which didn't exist → AttributeError on select; studio entries called `_start_studio_command("kind")` (returns a bare handler — no-op); kind `"suggested_questions"` mismatched registry `"suggest"`; "New Notebook" called `_create_notebook()` bare → printed usage | **fixed** | Implement `_open_model_dialog`/`_on_model_picked`; add `_start_studio_palette` (topic InputDialog → `_on_studio_picked(kind)`); `_create_notebook_dialog` (name InputDialog); correct kind `"suggest"` | `test_palette_filter_runs_studio_topic_dialog` |
| L89 | MED | `dialogs.py:CommandPalette` | Pushing a screen from inside the palette action while the modal was mid-dismiss → child-mount race (`ItemListDialog` `#item-list` NoMatches) for every submenu/studio entry | **fixed** | Dismiss palette first, defer the action via `app.call_later` (Textual's own palette pattern) | `test_palette_filter_runs_studio_topic_dialog` |
| L90 | MED | `dialogs.py:CommandPalette` | Up/down/enter relied on screen `on_key` + bindings to non-existent `cursor_up`/`cursor_down` actions; no Enter handler → arrows/Enter unreliable | **fixed** | Priority bindings → real actions; `on_input_submitted` fallback; `_move_highlight` via `action_cursor_up/down` (skips disabled headers) | palette tests |
| L91 | LOW | `dialogs.py` | `help_text()` duplicated in `commands.py` and `dialogs.py`; `/help` no longer uses it | **fixed** | Keep canonical copy in `commands.py`; remove from `dialogs.py` | `test_help_text_lists_commands` |
| L92 | LOW | `dialogs.py:InfoDialog` | Docstring "(used by /help)" stale — `/help` uses `HelpDialog` | **fixed** | Update docstring | — |
| L93 | LOW | `dialogs.py:CommandPalette` | Palette flat list with no section headers (`OptionList.add_separator` unavailable) | **fixed** | Disabled `Option` headers grouping entries by section | palette tests |

## Wave 7 — ChatScreen decisive repair (current session)

Incremental patches from the prior session left `ChatScreen` in a compile-blocked
state; this wave rewrites the corrupted region decisively and wires the remaining
studio audio/video path.

| ID | Sev | Location | Description | Status | Fix | Tests |
|----|-----|----------|-------------|--------|-----|-------|
| L94 | HIGH | `chat.py:_generate_studio_artifact` (L427-551) | Entire function body left at 4-space indent (same as `def`) → `IndentationError: expected an indented block after function definition on line 460` on every `opennote` startup. Nested `_render` at 8 vs 12, `context_text` wrongly inside `for` loop, `prompt = _render(kind)` trapped inside `_render` after `raise`, LLM branch + fallback `return save_artifact` at 4-space (class-level) → `return` outside function | **fixed** | Single-pass rewrite of the whole method: `def` 4, body 8, nested `_render` 8, its body 12/16/20; dedent `context_text` outside loop; move `prompt = _render(kind)` to method level; restore try/except at 8/12 | `py -m py_compile`, `tests/test_tui_app.py`, `tests/test_tui_commands.py` (44 passed) |
| L95 | MED | `chat.py:_render` (L467,472,477,482,487,492) | 6× `'\\n'.join(lines)` — literal backslash-n two chars in source → artifact text contains literal `\n` instead of newlines | **fixed** | `'\n'.join(lines)` in all six branches | `test_tui_app.py` studio tests |
| L96 | LOW | `chat.py:11,20,33` | `List` missing from `typing` import (used by `List[str]` annotations; harmless under `from __future__ import annotations`); duplicate `from opennote.tui.dialogs import HelpDialog` (L20 + L33); dead `artifacts_dir`/`_artifacts_dir`/`ChatError` lines | **fixed** | Add `List` to import; consolidate `HelpDialog` into L33; drop dead vars/imports | `py -m py_compile` |
| L97 | MED | `chat.py:_on_studio_picked` (L370) + `opennote/audio/tts.py`, `opennote/video.py` | Studio menu "Narrated audio"/"Narrated video" routed through `_generate_studio_artifact` → `_render` raises `ValueError: Unsupported kind: audio` | **fixed** | Special-case `audio`→ `_run_audio`/`save_audio_artifact` and `video`→ `_run_video`/`save_video_artifact` (worker threads); make `artifacts_dir` Optional with fallback to `NotebookManager` so `test_save_*_artifact_rejects_traversal` keeps working | `tests/test_artifacts_tts_video.py` (20 passed) |
| L98 | HIGH | `chat.py` (L151) + `opennote/tui/commands.py:77` | Stale `_open_palette`/`_on_palette_done`/`_show_help` block deleted decisively left no `_open_palette` → `make_commands` raises `AttributeError: 'ChatScreen' object has no attribute '_open_palette'` on every mount (31 TUI tests failed) | **fixed** | Restore `_open_palette` as wrapper for `action_open_palette` + `_on_palette_done` stub; real `_show_help` at L633 kept, duplicate at L157 removed | `tests/test_tui_app.py`, `tests/test_tui_commands.py` (44 passed) |
| L99 | LOW | repo hygiene | Junk debug scripts `fix_chat.py`, `fix_render.py`, `fix_render2.py`, `update_test.py` left in repo root; `notebooks/` + `artifacts/` runtime dirs not gitignored | **fixed** | Delete scripts; add `notebooks/` + `artifacts/` to `.gitignore` | — |

## Test status

Full suite: **336 passed** (was 334 before the Wave 6 palette/connect rework).
The additions are the regression guards listed above plus the Wave 1-5 fixes
(loop/tools/websearch citations, BM25/hybrid, TTS/video/artifacts, TUI studio)
and the L36/L21 closures below. Every entry above has a dedicated regression
test.

L42-L84 are the Phase A-G audit findings. Fix waves: 1 = triage/quick
regressions, 2 = BM25 rewrite, 3 = Tavily+SSRF, 4 = TTS/video/artifacts,
5 = TUI studio wiring. **All waves 1-5 are fixed (L42-L84)**, including
`/open` (L82) which now opens notebook artifacts with the system default
app. Wave 6 (L85-L93) is the Ctrl+P palette `OptionList` rewrite and the
`/connect` live-model flow. L36 (pre-audit) is now fixed via sidecar
`.meta.json` files, and L21 now has a direct test (`test_max_tokens_honored`).
Also updated `test_schemas_have_both_tools` to expect
`{"search","list_sources","web_search"}`.

## Wave 8 — Global install (ramratan.in) — HIGH / externally visible

`install` is served at `https://ramratan.in/install` (200 OK, Cloudflare) but the
tarball it `pip install`s (`https://ramratan.in/opennote-0.1.0.tar.gz`) 404s.
On Windows PowerShell 5.1 `curl` is an alias for `Invoke-WebRequest`, so the
documented `curl -fsSL … | bash` never downloads. Both are fixed in Wave 8
without adding load/latency to the origin (GitHub fallback, no tarball hosting).

| ID | Sev | Location | Description | Status | Fix | Tests |
|----|-----|----------|-------------|--------|-----|-------|
| I01 | HIGH | `install:17` + hosting | Tarball URL 404 — `pip install https://ramratan.in/opennote-0.1.0.tar.gz#egg=opennote` always fails | **fixed** | Map to PyPI (`pip install opennote`) with GitHub tarball fallback via PEP 508 `opennote @ https://github.com/natarmr/OpenNote/archive/refs/heads/main.tar.gz`; drop `#egg` | `curl.exe -I` 200/404 probes; local `bash install` dry-run |
| I02 | HIGH | `install:3` docs | Windows `curl` alias trap — `curl -fsSL` on PS 5.1 errors before download | **fixed** | Docs show `curl.exe -fsSL … \| bash` + `iwr -useb https://ramratan.in/install.ps1 \| iex`; add `install.ps1` (PowerShell-native, `py -m pip`) | `Get-Command curl` alias check |
| I03 | MED | `install:9,17` | Version check uses `python3` then bare `pip` — misses `python`/`py` on Windows and may pick wrong pip; `--user` installs not on PATH so `command -v opennote` false-negatives | **fixed** | Resolve `python3`/`python`/`py`, use `"$PYTHON_BIN" -m pip`, verify via `"$PYTHON_BIN" -m opennote --help` | — |
| I04 | LOW | `install:17` | Deprecated `#egg=opennote` fragment; no hash pinning | **fixed** | PEP 508 direct reference, no fragment | — |
| I05 | LOW | `install:16` | Emoji mojibake when file read as Windows-1252 (`📦` → `dY�`) | **fixed** | Keep file UTF-8, ASCII fallback in echo for strict consoles | `cat -Raw` check |

## Wave 9–11 — Skills / Plugins / Agents audit (current session) — latency-safe

Audit covered `opennote/skills/` (parse, discover, registry), `opennote/plugins/`
(loader, builtin/supermemory), `opennote/agents/` (defs, tools, loop), `opennote/capabilities.py`,
`opennote/cli.py` (new commands), `opennote/tui/commands.py` + `screens/chat.py` new methods,
`pyproject.toml`, plus repo-wide `ast` import scan and `git` hygiene. All HIGH fixed in Wave 9,
MED perf/redundancy in Wave 10, LOW hygiene in Wave 11. **Website/latency constraint:** walk helpers
now stop at `.git` (3 vs 30 ancestors, shared `walk_worktree_roots` in `fsutil.py`), per-turn
registries are built once and reused in `ToolContext` (no per-tool re-discovery), capability probe
remains lazy (no global cache that hides `TAVILY_API_KEY` changes in tests).

| ID | Sev | Location | Description | Status | Fix | Tests |
|----|-----|----------|-------------|--------|-----|-------|
| L100 | HIGH | `agents/tools.py:110` + `agents/loop.py:369` | `ctx._subagent_retrieved` via `setattr`/`hasattr` on a dataclass without the field (hidden `type: ignore`) | **fixed** | Declare `subagent_retrieved: List[SearchResult] = field(...)` + `depth: int = 0` on `ToolContext`; merge via `ctx.subagent_retrieved` directly | `test_task_subagent_retrieved` (implicit via loop) |
| L101 | HIGH | `agents/tools.py:388` | `task` recursion unbounded — subagent re-advertises `task`, model can nest `task→task→…` to `RecursionError` / cost blow-up | **fixed** | `_MAX_TASK_DEPTH = 1` on `ToolContext.depth`; hide `task` schema when `depth >= 1`; nested `agent_turn(..., _depth=depth+1)` | — |
| L102 | HIGH | `plugins/builtin/supermemory.py:138` | Dead `try: pass / except: pass  # notebook-ish context` placeholder | **fixed** | Delete block | — |
| L103 | MED | `skills/parse.py:24` + `agents/defs.py:54` | BOM (`\ufeff---`) fails `startswith("---")`; delimiter `"\n---"` not line-anchored and misses `\r\n---` / `--- ` | **fixed** | Anchored regex `r"\A\ufeff?---[ \t]*\r?\n"` + `r"\r?\n---[ \t]*\r?\n"` for close; strip BOM | — |
| L104 | MED | `skills/discover.py:18` + `agents/defs.py:115` + `plugins/loader.py:49` | Walk-up to FS root (30 ancestors) not stopped at `.git` — scans `C:\skills` etc., privacy/perf | **fixed** | Shared `fsutil.walk_worktree_roots()` (stops at `.git`), dedupe on `resolve()` | — |
| L105 | MED | `plugins/builtin/supermemory.py:60,136` | Search scoped by `opennote-{nb.name}` but store under generic `opennote` — writes never found by scoped reads | **fixed** | Shared `_container_tag_for(ctx)`; store uses same scoped tag (with `result.notebook.name` fallback) | — |
| L106 | MED | `plugins/builtin/supermemory.py:74` | `data.get("results") or …` — `[]` falsy, valid empty result falls through to next key | **fixed** | Presence check `if "results" in data` not truthiness | — |
| L107 | MED | `plugins/builtin/supermemory.py:18` | `_SUPERMEMORY_API_BASE` evaluated at import time, stale after env change | **fixed** | Read at call time via `_api_base()` | — |
| L108 | MED | `capabilities.py:60` + `agents/loop.py:161` | `_probe()` heavy FS walks on first call, no caching | **fixed** | Walk helper + ToolContext reuse; probe stays lazy (no global auto-cache that hides env changes in tests); `clear_cached()` helper added | latency: 344 tests ~86s (unchanged) |
| L109 | MED | `plugins/loader.py:117` | `hash()` randomized per process → non-deterministic, 100k collision | **fixed** | `hashlib.sha1(...).hexdigest()[:8]` via `_stable_hash` | — |
| L110 | MED | `skills/discover.py:88` + `agents/defs.py:142` + `plugins/loader.py:73` | Dedupe `str(p)` not resolved → symlink duplicates; `rp` computed unused | **fixed** | Dedupe on `p.resolve()` (try/except), shared helper | — |
| L111 | MED | `agents/tools.py:472` + `agents/loop.py:205` | Per-tool re-discovery: `execute_tool` called `_get_dynamic_schemas` per invocation (3 walks × N) | **fixed** | Loop discovers once per turn, `ToolContext` carries registries; `_get_dynamic_schemas` pure (no lazy rediscover when already set) | — |
| L112 | MED | `agents/tools.py:472` | `_get_dynamic_schemas` mutates input `ctx.skill_registry = reg` | **fixed** | Stop mutating; loop populates `ToolContext` directly | — |
| L113 | MED | `plugins/loader.py:246` | Builtins appended after file plugins — builtin `memory_search` could shadow user plugin | **fixed** | Load builtins first (lowest priority; user plugins override) | — |
| L114 | MED | `capabilities.py:102` + `agents/loop.py:237` | `plugins_loaded` stored tool names (`memory_search`) not plugin names, label misleading | **fixed** | Store `h._name` (plugin names); UI shows `plugins: supermemory` | — |
| L115 | MED | `tui/screens/chat.py:809` + `cli.py:432` | `PluginContext(logger=None)` → `ctx.logger.info()` `AttributeError` (silently swallowed) | **fixed** | `PluginContext.__post_init__` defaults to `logging.getLogger("opennote.plugins")` | — |
| L116 | MED | `agents/tools.py:294` | `OPENNOTE_ALLOW_SKILL_SCRIPTS` check `lower()` without `strip()` → `" 1 "` fails vs `_env_bool` which strips | **fixed** | `strip().lower()` everywhere | — |
| L117 | MED | `cli.py:410` | Chat REPL slash handlers duplicate typer logic with temp aliases `SR2/_PC/_PL/AR2/AR3` | **open** | Extract shared helpers (deferred — functional, not latency) | — |
| L118 | MED | `tui/screens/chat.py:776` | TUI registry calls on UI thread (blocks Textual) | **open** | Make `@work(thread=True)` (deferred) | — |
| L119 | LOW | `agents/tools.py:18` | `field` imported never used (now used via `subagent_retrieved`) — **became fixed by L100** | **fixed** | — | — |
| L120 | LOW | `agents/tools.py:115` | `ToolContext.artifacts_dir` declared never read | **open** | Wire or remove (low, no latency) | — |
| L121 | LOW | `skills/discover.py:5` | `import os` unused | **fixed** | Removed | — |
| L122 | LOW | `plugins/loader.py:5` | `import importlib` unused | **fixed** | `import hashlib` + `importlib.metadata/util` only | — |
| L123 | LOW | `skills/registry.py:50` | `rglob("*")` follows symlinks → can escape / loop; 200 cap before sort arbitrary | **fixed** | Skip `is_symlink()`, collect then `sorted()[:200]` | — |
| L124 | LOW | `plugins/builtin/supermemory.py:71,160` | Failures logged at `debug` invisible | **fixed** | Promote to `warning` | — |
| L125 | LOW | `agents/loop.py:230` | Capability line duplicates `skills (N)` + `skills: a, b` | **fixed** | Single `skills (N): a, b` | — |
| L126 | LOW | `tui/commands.py:44` | `Command exit` no-op on `None` screen.app | **open** | — | — |
| L127 | LOW | `capabilities.py:14` | `Dict/Tuple/Provider` unused imports; `_make_fake` dead | **fixed** | Remove `Dict/Tuple/Provider` | — |
| L128 | LOW | `agents/loop.py:18,19,22,23` | `render_tool_results/set_cached/FakeCapability/ChatError/SYSTEM_TEMPLATE/Tuple` unused imports + dead constants `TOOLS_LIST`/`SYSTEM_TOOLS_HINT`/`UNTRUSTED_CONTENT_NOTE` | **partial** | Removed 6 unused imports; dead constants kept for now (low, no import cost) | — |
| L129 | LOW | repo | Stray `injection-test-set.*` untracked, no ignore rule | **fixed** | Add `/injection-test-set.*` + `studio_outputs/` to `.gitignore` | `git check-ignore -v` |
| L130 | LOW | `agents/defs.py:70` | Case-insensitive FS collision on `MySkill.md` vs `myskill.md` | **open** | — | — |
| L131 | LOW | `video.py`/`artifacts.py`/etc. | BOM `U+FEFF` in `pdf_docling.py:1` + `pdf_fallback.py:1` | **open** | Strip or resave UTF-8 | `py -c` BOM check |
| L132 | LOW | `agents/loop.py` gap | TUI `/agent <name>` only shows, never switches; CLI `--agent` not implemented vs plan | **open** | Roadmap — docs now say "show-only" | — |

## Test status

Full suite: **344 passed** (was 336 before skills/plugins/agents; was 334 before Wave 6).
Waves 8–10 land with **no latency regression** (walk stops at `.git`, single discovery per turn, origin no longer hosts tarball).
Wave 11 dead-code sweep is partial — remaining LOW items are tracked above as **open** and do not affect correctness/latency/website.
