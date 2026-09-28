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
| L100 | HIGH | `tui/screens/chat.py:168-211` + `tui/widgets/transcript.py` | Mount/resize path re-rendered entire `transcript.json` on every `on_resize`/`_finish_mount` → `nb2` showed the same answer 2×+ stacked vertically | **fixed** | `_history_rendered` delta guard (0→len on first render, slice on re-render), split `_fit_sidebar` (mount-only) vs `_fit_sidebar_display` (resize-only), `transcript.clear()` paths call `_reset_history_rendered()`, live echo in `_start_ask`/`_start_search` with optimistic cursor bump and `on_turn_result` sync | `tests/test_tui_history_render.py` (6) — resize/double-mount/echo/switch/clear-undo dedup |
| L101 | HIGH | `chat/ask.py:56` | `_single_shot` fitted context to budget then overwrote `tagged` with unbounded `build_tagged_context` — every ask sent full context (prompt/cost blowup) | **fixed** | Delete line 56; reuse fitted `tagged` | `test_single_shot_honors_context_budget` |
| L102 | HIGH | `tui/screens/chat.py:783-819` | Empty index returned `""` posted as success; LLM exceptions saved as `body="LLM error: …"` real artifacts | **fixed** | `_run_studio` posts `StudioFailed` on empty detail; LLM errors propagate (never persisted); dropped worker-thread `transcript.add_error` | studio-modes empty/raise tests + `_run_studio` pilot test |
| L103 | MED | `tui/screens/chat.py:on_studio_result_msg` | Degraded audio/video (`.md`/script fallback) displayed as full success | **fixed** | Non-binary suffix (not `.mp3/.wav/.mp4`) adds "full audio/video unavailable" note | degraded-AV pilot tests |
| L104 | HIGH | `ingest/pipeline.py:188-267` + `cli.py` | URL/total parse failure returned 0 → `Indexed 0 chunk(s)` exit 0; `remove_source` swallowed backend errors, list updated → stale chunks searchable | **fixed** | Raise `ValueError` when files failed with 0 indexed (up-to-date/empty still 0); URL errors raise; `remove_source` propagates, list updated only on success; CLI 0-count message honest | pipeline raise/remove tests + CLI zero test |
| L105 | HIGH | `agents/loop.py:418` | Free-form validator crash fell open to unvalidated `response.content` | **fixed** | Fail closed to abstention + warning log | validator-crash test |
| L106 | MED | `transcript.py:47-51`, `context_meter.py:177-196` | Corrupt transcript/usage silently reset to empty/$0.00 | **fixed** | Quarantine to `*.corrupt.<ts>.json` + warning; `record_spent` returns persisted total on write failure | state-safety tests |
| L107 | MED | `chat/ask.py:_multihop` | Worker gaps (`[No answer]`/`[No results]`) and planner fallback invisible in answer | **fixed** | Partial-coverage / single-shot fallback notes appended post-validation | multihop annotation tests |
| L108 | HIGH | `capabilities.py:120-137`, `agents/loop.py:180-201` | Capability probe never cached (test-only setter); registries discovered 2×/turn incl. plugin re-exec | **fixed** | 120s TTL cache (+`clear_cached` on connect, timestamp fix in `set_cached`); loop keeps single discovery pass | TTL + stub-honored tests |
| L109 | HIGH | `tui/screens/chat.py` + `retrieval/` | `Retriever()` rebuilt per query (3 Chroma clients + full BM25 snapshot each) | **fixed** | Session `_get_retriever` cache keyed by (dir, top_k, bm25, alpha); invalidated on ingest/switch/create; `_run_ask` falls back to lazy build on empty index | retriever-reuse test (old test caught the empty-index regression pre-fix) |
| L110 | MED | `agents/loop.py:89-111,449-470` | Unbounded tool payloads resent every round (O(rounds×chunks) bloat) | **fixed** | `_MAX_CHUNK_CHARS=8000` render cap + `_MAX_RETRIEVED=100` with omission notice (indices always valid) | cap + truncation tests |
| L111 | MED | `tui/screens/chat.py:381-421,1337-,1875-,1945-` | Sidebar discovery + git subprocess, `/sources` Retriever build (~90s), model validation httpx — all on UI thread | **fixed** | 60s TTL on slow sidebar sections; `/sources` → worker + `SourcesResultMsg/Failed`; connect/switch-model validation → worker + flow-dispatched `ModelsResultMsg` | tui-workers tests (7) |
| L112 | LOW | `tui/widgets/transcript.py`, `ingest/pipeline.py`, `tui/widgets/prompt.py` | Unbounded RichLog; per-file manifest rewrites; dead duplicate key block | **fixed** | `max_lines=2000`; manifest flush every 10 files + final; removed dead block | manifest implicitly covered; suite 488/488 |
| L113 | INFO | rejected, no change | Hybrid k×2 over-fetch (E1 design, millisecond-scale cost — construction dominates); `_parse_arguments` salvage (degrades to loud arg errors); palette nav/toast/model-cache items stay open LOWs | — | documented | — |

## Test status

Full suite: **461 passed** (was 455 before this fix).
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

## Wave 12 — Deep pass over untouched core (current session) — executed

Wave 12 covered the remaining core: `audio/tts.py`, `video.py`, `websearch.py` (remaining SSRF), `chat/`, `auth/`, `ingest/`, `store/`, `retrieval/`. All HIGH/MED fixed below; LOW hygiene remains **open** where noted. Latency kept flat (capped walks, model cache).

| ID | Sev | Location | Description | Status | Fix | Tests |
|----|-----|----------|-------------|--------|-----|-------|
| L133 | LOW | `prompts.py:1` | Entire module dead (duplicates `chat/prompt.py`) | **fixed** | Delete `opennote/prompts.py` | `py -m py_compile` |
| L134 | LOW | `chat/ask.py:9` | Dead top imports `SYSTEM_TEMPLATE/build_context/build_user_message` + `ChatError` | **fixed** | Remove imports; keep local `build_tagged_context` import | — |
| L135 | MED | `chat/prompt.py:60` | `build_tagged_context` missing `[{idx}] {citation}` — grounding mismatch with `loop:_tool_content` | **fixed** | Emit `[{idx}] {citation}` inside `<source>` | `test_chat_prompt` |
| L136 | MED | `websearch.py:64` | Still sent `tone` (unknown field → Tavily 400); `max_results` uncapped | **fixed** | Drop `tone` param, cap `max_results` 1..20, validate `topic` | `test_websearch` |
| L137 | MED | `websearch.py:104` | `web_search` no validation vs `tools:_web_search` clamped 1..25 | **fixed** | Validate `query` non-empty, `top_k` 1..25 | — |
| L138 | MED | `websearch.py:136` | `enrich_fetches` counted successes not attempts → 10 failing fetches bypass `_MAX_ENRICH_FETCHES=3` | **fixed** | Increment before fetch | — |
| L139 | MED | `websearch.py:166` | `r.get("content", "")[:_MAX` on `content:null` → `TypeError` | **fixed** | Coerce `None` → `""` via `raw_content = r.get("content") or ""` | — |
| L140 | MED | `websearch.py:200` | Hex IP `0x7f...` bypasses `is_private_ip` | **fixed** | Block `0x` in host | — |
| L141 | MED | `video.py:106` | `_wrap_text` width `bbox[2]` not `bbox[2]-bbox[0]` → overflow | **fixed** | `w = bbox[2]-bbox[0]` | `test_video` |
| L142 | MED | `video.py:205` | `audio_map` globbed `*.mp3` only, wav from Gemini missed → mux "No audio" | **fixed** | Glob `*.mp3` + `*.wav` | — |
| L143 | MED | `video.py:382` | No slide cap → 200 slides → OOM | **fixed** | `_MAX_SLIDES=20`, cap before render | — |
| L144 | MED | `ingest/pipeline.py:70` | `rglob("*")` uncapped, no hidden-dir skip — large ingest hangs | **fixed** | `_MAX_INGEST_FILES=500`, skip `_SKIP_DIRS`, warn on cap | — |
| L145 | MED | `store/vectors.py:44` | `SentenceTransformer` reload per `Retriever()` (~90s) — no cache | **fixed** | Module `_MODEL_CACHE` keyed by `(model_name, device)` | — |
| L146 | LOW | repo hygiene | `prompts.py` dead import scan remaining: `artifacts:time`, `fsutil:Dict`, `security:Path`, etc. | **open** | Tracked, low, no latency | — |
| L147 | MED | `chat.py:_run_video` + `video.py:489` | `/video` passed the raw topic as slide JSON → `explain_video` always rejected it; error branch returned a `video-error.md` path that was never written (dangling) | **fixed** | LLM-grounded slide builder (`_slides_script_json`, fence-tolerant) + chunk-based `_fallback_slides_json`; error branch materializes the file | `tests/test_studio_modes.py` (4 new) + live `notebook-1` run → `slideshow.mp4` 1.2 MB |
| L148 | LOW | providers 2026-09-22 | google `3.5-flash` 503×2, `2.5-flash` 404 retired (API points at `3.6-flash`), `3.6-flash` 503; groq `qwen3.8-27b` grounded OK | working default = groq | Live runs on groq; google default model moved to `3.6-flash` (AuthConfig state, not repo) | live probe + notebook-1 8/8 studio run |
| L149 | LOW | groq OTPM rate limit | briefing first attempt 429 (Limit 1000, Requested 513) saved as `LLM error` body | retry-once | Retry → READY 12s; stale error-body artifact deleted | live run |
| L150 | LOW | data | `kkkk` notebook + Kimi mindmap artifact vanished from `.opennote/` (not code-caused; no delete ran) | re-ran on `notebook-1` | Full 8-kind studio validation redone on `notebook-1`/kimi (373 chunks) | live run |
Wave 11 dead-code sweep is partial — remaining LOW items are tracked above as **open** and do not affect correctness/latency/website.

## Wave 13 - Security audit run-1 remediation: source-block delimiting (executed)

Remediates the two injection leads that survived the run-1 calibration bar
(`REPORT.md` §5: no boundary crossed + control not load-bearing). These survive
on the *opposite* reasoning: `security/scan.py:1` self-documents as "telemetry,
not a gate", so `<source>` delimiting is the only deterministic data/instruction
separator, and defeating it reclassifies document bytes as model instructions
while the model still holds `read_page` / `web_search` / `memory_search`.

| ID | Sev | Location | Description | Status | Fix | Tests |
|----|-----|----------|-------------|--------|-----|-------|
| L151 | HIGH | `agents/loop.py:118` + `chat/prompt.py:107` + `chat/context_budget.py:93` | `r.citation` interpolated into the `<source>` block **unescaped** while only `r.content` was escaped. `citations.py:44` builds the locator from a document-authored DOCX/HTML heading (`docx.py:56,87`, `html.py:50,88`), a Tavily `title`/`url` (`citations.py:55`), or supermemory hit metadata - so `</source>` in a heading closed the block early. Deterministic: the renderer emitted its own literal close tag. | **fixed** | New `opennote/security/delimit.py`; all three renderers call `render_source_block`, which escapes content **and** citation, and escapes the `page` attribute | `tests/security/test_source_delimiting.py` (new, 33 cases) |
| L152 | MED | same three sites + `transcript.py:104` | Delimiter escape was a case-sensitive exact-substring pair, so `</SOURCE>`, `</Source>`, `</source `, `</source\n>`, `< source>` and a bare `</source` (left by truncation) all survived. The same pair was duplicated in 4 places, and `transcript.py:104` escaped only the closer. | **fixed** | One regex, case-insensitive, whitespace/NUL tolerant, with `\b` so `<sources>`/`<sourceful>` prose is not mangled; legacy byte-identical output preserved for the two pinned expectations | `tests/security/test_source_delimiting.py::test_no_live_tag_survives_in_content` (13 spellings) + `test_similar_words_are_not_over_escaped` |
| L153 | LOW | `chat/context_budget.py:39` vs `:93` | Budget estimate `_block_len` hand-builds the same block format, so it drifts from the real renderer. Not a security issue (estimate only). | **open** | Left as-is: re-syncing it would shift every budget decision | - |

Ordering note (L151/L152): `render_source_block` truncates **then** escapes, so
exactly the bytes the model sees are the bytes that were scanned. Escaping first
would let a cut land inside an escape and re-expose the tag - pinned by
`test_truncation_cannot_re_expose_a_tag`.

`opennote/chat/prompt.py:escape_source_content` is kept as a thin alias because
`tests/test_chat_prompt.py:20` and `tests/security/test_injection_gate.py:9`
import it from there.

Still open from run-1: L43-class enrichment SSRF (lead 4.2 + 2.4 + 2.5),
ungated plugin load (4.1), and the claim-text grounding gap (2.3).

## Wave 14 - Security audit run-1 remediation: SSRF guard bound to the primitive (executed)

Closes validated lead 4.2 (`websearch.enrichment-fetch-bypasses-is-safe-url`) and
unvalidated leads 2.4 (`is-safe-url-host-encoding-canonicalization-gap`) and 2.5
(`ssrf-guard-prefetch-only-unbound-redirect`) with a single chokepoint.

Both 2.4 and 2.5 were confirmed empirically before the fix, no sandbox required -
they are pure-function / library-default facts:

    before:  ACCEPT http://localhost./   ACCEPT http://127.0.0.1./
             ACCEPT http://metadata.google.internal./   ACCEPT http://127.1/
             ACCEPT http://0177.0.0.1/
    after:   reject all five; https://example.com/ still accepted

| ID | Sev | Location | Description | Status | Fix | Tests |
|----|-----|----------|-------------|--------|-----|-------|
| L154 | HIGH | `websearch.py:149` (enrichment) | Tavily `url` is a third-party response field read verbatim at `:128` and passed to `trafilatura.fetch_url` with **no** `_is_safe_url`; the only gate was the `enrich_fetches` attempt counter at `:144`. `_is_safe_url` had exactly one production call site (`read_page`). | **fixed** | Both fetch paths now call `_fetch_guarded(url)` | `test_enrichment_fetch_goes_through_the_guard`, `test_enrichment_fetch_of_public_url_is_still_attempted` |
| L155 | HIGH | `websearch.py:229-230` | `except ValueError: return False` failed **open**. `ipaddress.ip_address` declines `127.1` and `0177.0.0.1`, which the Windows resolver still maps to `127.0.0.1`. The suffix tuple could not match a name with a root label and `_PRIVATE_HOSTNAMES` is exact-membership, so `localhost.` and `metadata.google.internal.` passed too. | **fixed** | Fail closed on a numeric-looking host `ipaddress` cannot parse (`_NUMERIC_HOST`); strip the root label before the membership/suffix tests | `test_is_safe_url_rejects_canonicalization_bypasses` (8 URLs), `test_non_canonical_numeric_host_is_the_fail_closed_branch` |
| L156 | HIGH | `websearch.py:270` | The guard was a pre-request string check only. trafilatura 2.2.0 `_send_urllib_request` calls `urllib3.PoolManager.request(...)` with `redirect=MAX_REDIRECTS` (**2**), so redirects are followed *inside one call* and `response.geturl()` is only read afterwards - a 302 into loopback is requested before any check could run. The decisive fact is no longer an assumption. | **fixed** | `_fetch_guarded` walks the chain itself (`follow_redirects=False`, headers-only read), validating scheme + host + resolved IP on **every** hop, capped at 5, refusing non-http(s) hops | `test_fetch_guarded_refuses_redirect_into_private_range`, `_follows_a_public_chain`, `_refuses_non_http_redirect`, `_caps_redirect_hops` |
| L157 | MED | `websearch.py:251` | The textual filter is best-effort by design: a public hostname can resolve into a private range. Nothing checked DNS. | **fixed** | `_host_resolves_public` resolves and rejects any private/loopback/link-local/reserved answer; unresolvable hosts pass through so the fetcher reports the real error | `test_host_resolving_private_is_refused`, `test_unresolvable_host_is_left_to_the_fetcher` |
| L158 | MED | `ingest/parsers/html.py:123` | `opennote ingest <url>` fetched with no guard at all. Typing the URL is consent to fetch *that* host, not consent to be redirected onto link-local metadata. | **fixed** | Routed through `_fetch_guarded` (lazy import - `websearch` imports this module) | covered by the chokepoint tests |
| L159 | LOW | `websearch.py` fetch path | A third `trafilatura.fetch_url` could be added and silently skip the guard. | **fixed** | `read_page` no longer checks at the call site; the guard is owned by the primitive only, so there is nothing to forget | - |

**Accepted residual (owner decision, recorded in `engg_choices.md:E19`):** DNS
rebinding between our `getaddrinfo` and trafilatura's own resolution, plus the
second-request window (we probe, then trafilatura re-requests the validated final
URL). Both require a stateful, ephemeral redirect from a live third-party host -
materially beyond the "web publisher" / "document author" threat model. Pinning
the resolved IP was considered and rejected as disproportionate for a local tool.

## Wave 15 - Security audit run-1 remediation: grounding binds the claim text (executed)

Unvalidated lead 2.3 (`grounding-binds-quote-span-not-claim-text`). The run-1
rejection reasoning does **not** transfer here: it rested on
`validate_freeform_answer` being a self-documented legacy heuristic, but
`filter_grounded_answer` is a different function whose module docstring promises
"any claim that doesn't verifiably trace to real source text gets dropped" - a
promise `validate_claim` did not keep.

| ID | Sev | Location | Description | Status | Fix | Tests |
|----|-----|----------|-------------|--------|-----|-------|
| L160 | HIGH | `validation/citation.py:68-75` | `validate_claim` compared **only** `quote_span`. `Claim.text` - the sentence actually rendered to the operator - was never compared to anything. Since the document author writes the text being quoted, any quote from their document scores 1.0, so tier 1 certified that a span was *copied*, not that the claim is *grounded*. | **fixed** | Second required condition: `claim.text` must be carried by the same chunk at `_TEXT_SUPPORT_THRESHOLD=0.6` (content-word coverage, prefix-tolerant) | `test_real_quote_with_arbitrary_claim_text_is_dropped` (the lead verbatim), `test_grounded_claim_text_still_passes` |
| L161 | MED | `validation/citation.py:87` | `summary` was retained whenever *any* claim survived, so a fabricated overview rode in on one genuine quote and was rendered as part of the grounded answer. | **fixed** | Summary held to the kept claims' own chunk text; dropped (and logged) when unsupported | `test_fabricated_summary_is_dropped_even_when_a_claim_survives`, `test_supported_summary_is_retained` |
| L162 | LOW | `validation/citation.py` | Sub-threshold drops were silent, so a mis-tuned threshold would look like "the model got worse". | **fixed** | Every text-support and summary drop logs source id, coverage, threshold and an excerpt | - |

This is the **containment layer** for L151/L152: even if a delimiter spelling
somehow survives, an injected instruction that becomes a `Claim.text` no longer
reaches the answer. Both waves are load-bearing together.

Known tradeoff, accepted by the owner: genuine paraphrase with different
vocabulary can score below 0.6 and be dropped. Every drop is logged with its
coverage so the threshold can be tuned from real traffic - `0.6` is a starting
point, not a settled value. Matching is prefix-tolerant (`cost`/`costs`) but
deliberately **not** a stemmer: `strong`/`strength` share only `str`, and a rule
loose enough to merge those would merge unrelated words too.

## Wave 16 - Security audit run-1 remediation: plugin loading is opt-in (executed)

Validated lead 4.1 (`opennote/plugins/loader.py:exec_module-ungated-plugin-load`).
Owner decision: **warn once, then require the env var** - the first skipped load
names the directories and the variable, then fails closed silently.

| ID | Sev | Location | Description | Status | Fix | Tests |
|----|-----|----------|-------------|--------|-----|-------|
| L163 | HIGH | `plugins/loader.py:106` `load()` | `_plugin_dirs` maps **every** worktree ancestor to `<ancestor>/.opennote/plugins` (`fsutil.py:68` appends before the `.git` break at `:70-71`), plus `default_home()/plugins`, which is `<cwd>/.opennote/plugins` whenever `OPENNOTE_HOME` is unset - even outside any git repo. Any `.py` there was imported and executed with no operator decision, on TUI mount (`tui/screens/chat.py:416`), on `opennote capabilities` (`capabilities.py:99`), on `opennote artifacts check` (`cli.py:813`), and twice per agent turn (`agents/loop.py:193`, `:211-212`). | **fixed** | `OPENNOTE_ALLOW_PLUGINS` opt-in checked at the single choke point every caller passes through, covering the entry-point branch (`:137-146`) that bypasses `_import_file` entirely. Built-ins exempt (in-repo; supermemory already keyed). Skipped paths recorded on `loader.skipped` + `Capabilities.plugins_skipped` | `test_file_plugin_is_not_executed_without_opt_in`, `test_file_plugin_loads_with_opt_in`, `test_plugins_allowed_reads_the_env_var`, `test_builtin_loads_without_the_opt_in` |
| L164 | MED | `capabilities.py:41-42` | `Capabilities` had `plugins_loaded` and **no** `plugins_allowed` - the asymmetry with `skill_scripts_allowed` (`:48`). `_probe()` called `loader.load()` unconditionally, so a diagnostic print executed code. | **fixed** | Added `plugins_allowed` + `plugins_skipped`; probe now safe because the gate is inside `load()`; both printed by the `__main__` block | `test_capabilities_reports_plugins_allowed_and_skipped` |
| L165 | MED | `cli.py:668` | `opennote plugins list` executed every plugin module and only then printed the placement hint at `:673` - post-execution notice, not consent. | **fixed** | Opt-in state and skipped paths printed before the listing | - |
| L166 | MED | `plugins/loader.py:121`, `capabilities.py:101` | `_import_file` re-raises `BaseException` (`:91-93`) but `load()` caught `Exception`, so a plugin calling `sys.exit()` at import aborted the whole CLI/TUI - contradicting `load()`'s own docstring. Same at `register()`. | **fixed** | `except KeyboardInterrupt: raise` / `except BaseException:` - a plugin cannot abort the process; the operator's Ctrl-C still propagates | `test_plugin_calling_sys_exit_at_import_does_not_abort_the_process`, `test_isolated_module_is_removed_from_sys_modules_on_failure` |
| L167 | MED | `tests/` | **Zero** coverage for the plugin loader - no test file referenced plugins, so a trust-decision fix here would land with no harness. | **fixed** | New `tests/test_plugins_loader.py` (13 tests): the gate, admission rules, discovery surface, isolation contract, builtin exemption | 13 new |

Owner trust-model note (unchanged from run-1): if executing
working-tree-adjacent `.opennote/plugins/*.py` is *intended*, close the finding
against that decision instead of re-auditing it - the opt-in is the mechanism,
not the argument.

## Wave 17 - run-1 consistency + hardening items (executed)

The cheaper items from `REPORT.md` §5 and §7, plus the dead-code removals that
would otherwise let the Wave 13/14 gaps reappear.

| ID | Sev | Location | Description | Status | Fix | Tests |
|----|-----|----------|-------------|--------|-----|-------|
| L168 | LOW | `agents/loop.py:353-356` | The Gemini fallback's validator verdict was discarded by a **bare `pass`** - dead code that reads like an unfinished thought and invited a re-audit (the run-1 candidate). | **fixed** | Replaced with a `logger.info` + a comment stating the divergence from the no-tool-call branch is deliberate (`engg_choices.md:E22`) | existing fallback tests |
| L169 | MED | `plugins/builtin/supermemory.py:146-150` | Notebook-scoped `containerTag` was **dead**: it read `result.notebook.name`, but `AskResult` had no `notebook` attribute, so all notebooks shared one container. Surfaced by the run-1 Phase 3 verifier. | **fixed** | Added `AskResult.notebook`, populated at all 3 construction sites; `_notebook_name()` normalises both shapes (`ToolContext.notebook` is a Notebook, `AskResult.notebook` is a string) | - (covered by supermemory tests) |
| L170 | MED | `context_meter.py:207` | The only non-atomic state write in the tree (`p.write_text`), while its own recovery path exists precisely because a torn write leaves corrupt JSON. | **fixed** | `fsutil.atomic_write_text` (tmp + `os.replace`) | `test_record_spent_write_failure_returns_persisted` (retargeted at the new write path), `test_record_spent_is_atomic` |
| L171 | MED | `auth/config.py:69` | Corrupt-config backup used a **fixed** `.corrupt` name, so a second corruption overwrote the copy it was written to preserve. | **fixed** | Timestamped name **plus a collision counter** - a bare `int(time.time())` still collides within one second, which the new regression test caught | `test_corrupt_file_backed_up` (retargeted), `test_second_corruption_does_not_overwrite_the_first_backup` |
| L172 | LOW | `agents/tools.py:511` | A plugin schema could overwrite a core schema for argument validation while `execute_tool` dispatched the plugin handler first - so a plugin named `search` substituted the implementation while the model still saw the core schema. | **fixed** | `_get_dynamic_schemas` skips any name in `TOOL_SCHEMAS`, matching the existing strip at `loop.py:241-242` | `test_plugin_tool_cannot_shadow_a_core_tool` |
| L173 | LOW | `chat/prompt.py:86-91`, `agents/tools.py:608` | Two **dead** renderers interpolated `r.citation` unescaped: `build_context` (no `<source>` framing at all) and `render_tool_results` (superseded by `_tool_content` after L16). Reachable only from their own tests and the package re-exports. | **fixed** | Deleted, with `__init__` exports and their tests updated. Kept as live booby traps: a renderer with the same shape as the vulnerable ones, one `git blame` from being wired back in | `test_build_tagged_context_wraps_every_chunk_in_source_tags` |

Deliberately **not** changed, with reasons:
- `context_budget.py:39` `_block_len` hand-builds the block format for its length
  estimate and now drifts slightly from the real renderer. Re-syncing it would
  shift every budget decision for a non-security gain (tracked as L153).
- `context_meter.load_spent`'s quarantine uses `os.rename` to a
  `usage.corrupt.<ts>.json` name. Unlike `auth/config.py`'s `shutil.copy2`, a
  rename onto an existing path fails rather than silently overwriting, so the
  same-second collision cannot lose data there.
- `SYSTEM_TEMPLATE` / `build_user_message` stay: they are plain concatenation
  with no delimiter involvement, so they are not booby traps.

Lint: the 3 new files are ruff-clean. The 17 pre-existing touched files go from
388 to 394 findings, and the delta is entirely `UP006`/`UP045`/`BLE001` in code
written to match file-local `List[]`/`Optional[]`/`except Exception` style, as
`AGENTS.md` requires. The four findings that were *not* style-matching (a dead
import, an alias-import, and two simplifications) were fixed.

## Wave 18 - Turning the run-1 verification plan into automated guards (executed)

Automates the manual verification that Waves 13-17 left to the operator. All
offline: no API key, no egress. Loopback-only where a real socket is needed.

| ID | Sev | Location | Description | Status | Fix | Tests |
|----|-----|----------|-------------|--------|-----|-------|
| L174 | HIGH | `websearch.py:161,162,167,169` | `c.meta` does not exist on `DocumentChunk` (field is `metadata`). The Tavily **enrichment fetch had therefore never succeeded** - every call raised `AttributeError` at `:161`, was swallowed by the `except Exception` at `:173`, and fell through to the bare Tavily snippet. The only evidence was a `logger.warning` routed to a `NullHandler` under the TUI, so a permanently dead path looked exactly like a working one. | **fixed** | `c.meta` -> `c.metadata` (4 sites) | `test_enrichment_content_reaches_the_result`, `test_enrichment_populates_the_citation_from_the_page` - both fail without the fix |
| L175 | MED | `validation/citation.py` `_TEXT_SUPPORT_THRESHOLD=0.6` | **Measured: the shipped threshold is wrong.** On a labelled corpus, whole-sentence word coverage cannot separate grounded paraphrase from fabrication - the bands overlap (grounded min 0.40, reject max 0.50). A sweep of 0.20..0.60 shows every setting either drops legitimate paraphrase or admits a fabrication: 0.6 keeps 3/6 grounded, 0.40 keeps 6/6 but admits 2/6. | **open - owner decision** | Not changed unilaterally. Two `xfail(strict=False)` tests record the gap and will report XPASS when fixed; `test_report_coverage_distribution` prints the evidence | `test_no_false_drops_on_grounded_corpus` (xfail), `test_threshold_separates_the_corpora` (xfail) |
| L176 | LOW | test coverage | The plugin loader, capability probe, `opennote plugins list`, the supermemory container tags, the real ingest parsers' heading provenance, and the web fetch result path had no regression guards for the behaviour introduced in Waves 13-17. | **fixed** | New: `test_supermemory_scoping.py` (10), `test_websearch_fetch.py` (12), `security/test_grounding_calibration.py` (7); extended: `test_plugins_loader.py` (+5), `security/test_source_delimiting.py` (+3); new `loopback_http_server` fixture in `conftest.py` | 615 passed, 2 xfailed |

### L175 - the measured result, and the two options

The grounding validator is a **vocabulary** check, so it cannot distinguish an
abstractive paraphrase (wholly different words, legitimate) from a fabrication
that *blends* a real clause with a novel instruction clause (partly grounded,
illegitimate). Blending averages out to ~0.4-0.5, which is exactly where the
paraphrases sit.

Evidence from `pytest tests/security/test_grounding_calibration.py -k report -s`:

| metric | t=0.30 | t=0.40 | t=0.60 |
|---|---|---|---|
| whole-sentence (shipped) | 6/6 grounded, **2/6 fabrications admitted** | 6/6, **2/6 admitted** | 3/6, 0/6 |
| per-clause minimum | 5/6 grounded, 0/6 | 5/6, 0/6 | 3/6, 0/6 |

Option A - **per-clause minimum coverage, threshold ~0.30.** Split the claim on
sentence and conjunction boundaries, require *every* clause to clear the bar. A
blended fabrication has one clause scoring ~0, so it drops regardless of
threshold. Yields a 0.25-0.40 safe band where whole-sentence coverage had none.
Costs: a lower numeric bar, so it is a visible loosening; the remaining 1/6
failure is a stem artifact (`degrade` vs `degradation`).

Option B - **keep 0.6 whole-sentence** and accept that abstractive paraphrase is
dropped. Honest and strict, but the measurement says it drops 3 of 6 legitimate
paraphrases, which users would read as "it refuses to answer about my document".

Not chosen here: a semantic/LLM-based support check. Out of proportion for a
local tool and unmeasurable offline.

### L175 companion - a boundary worth not rediscovering

An injection that is **verbatim in the chunk being cited** scores 1.0 and is
kept. That is correct, not a bug: the text genuinely is in the source, so quoting
it *is* grounded, and a validator that refused to quote the operator's own
document would be broken. Prompt injection living inside ingested text is the
**delimiter's** job (E18), not the grounding validator's (E20). Pinned by
`test_injection_inside_the_cited_chunk_is_grounded_by_design` so it is a recorded
boundary rather than a future "new finding".

### L174 note - run-1's impact statement needs one correction

Run-1 lead 4.2 claimed the fetched body was "returned to the model" as context.
Because of L174 the body was fetched and then discarded, so that specific
consequence never materialised. The **network request still issued from the
operator's position** - which is the actual boundary crossing - so the lead and
its fix stand unchanged. Only the content-relay detail was wrong.

## Wave 19 - Measured L175 against real model output (executed)

`scripts/measure_grounding.py` runs real agent turns, records every claim that
reaches the tier-2 check, and reports the coverage distribution. It changes no
behaviour -- it wraps `_text_coverage`, `filter_grounded_answer` and
`validate_freeform_answer` to record their inputs and returns.

**This overturns the Wave 18 conclusion.** The synthetic corpus said 0.6 was
measurably wrong; real model output says 0.6 is roughly right and the threshold
should NOT move.

| run | questions | completed | structured | claims | kept | dropped | median cov | min cov |
|---|---|---|---|---|---|---|---|---|
| 1 | 5 (kimi.tsv) | 5 | 60% | 6 | 5 | 1 | 0.85 | 0.50 |
| 2 | 12 (mixed factoid/explanatory/comparative) | 5 (7 x 429) | 60% of 5 | 12 | 12 | 0 | 0.87 | 0.62 |

Combined: **18 claims, 17 kept, 1 dropped (~6%)**, and **no claim fell below 0.5**
except the single drop. Threshold table on run 2 (n=16 evaluations): 0 drops at
0.30/0.40/0.50/**0.60**; 3 drops only at 0.70.

| ID | Sev | Location | Description | Status | Fix | Tests |
|----|-----|----------|-------------|--------|-----|-------|
| L177 | MED | `validation/citation.py:_content_words` | The only observed false drop was **not** paraphrase. The model wrote the same fact two ways -- "activates 104.2 **billion** parameters" scored 0.50 and was kept; "activates 104.2**B** parameters" scored 0.57 and was **dropped**. Single-letter unit abbreviations are single tokens against a spelled-out word, so the metric penalises abbreviation rather than ungroundedness. | **open - recommended** | Normalise unit abbreviations in `_content_words` (B/billion, T/trillion, M/million, K/k, and similar) so both spellings score alike. Keep the threshold at 0.6 | - |
| L178 | LOW | `scripts/measure_grounding.py` report | The first version counted failed turns as "answered in prose", inflating the prose rate and understating the structured rate. | **fixed** | Rates now computed over completed turns only, with failures reported separately | - |

**Revised conclusion for L175:** do *not* lower the threshold and do *not*
implement per-clause scoring. The data does not support either:

- Real claims cluster at median 0.85-0.87 with a mean of 0.84 -- nowhere near the
  0.40-0.58 band the synthetic corpus suggested. Lowering the bar to 0.30 to
  "rescue" paraphrases would admit fabrications to fix a problem that real
  output does not exhibit.
- Per-clause scoring was motivated by the synthetic overlap. No blended
  fabrication appeared in either run, so there is no evidence for it.
- The one real defect is L177, and it is a metric bug with a small, targeted fix.

**The synthetic corpus overstated the risk.** Its 0.40-0.44 "heavy paraphrase"
and "multi-fact paraphrase" cases were written to probe the boundary, not sampled
from usage. They are adversarial probes and should be labelled as such; they are
not a false-drop rate. This is recorded so the next reader does not treat L175 as
an open product bug.

**Measurement limits, stated plainly:** 18 claims from one document
(`kimi.pdf`), one model (groq `qwen3.8-27b`), two runs; run 2 lost 7 of 12 turns
to groq's OTPM rate limit (the L149 issue, unrelated to this work). A model that
paraphrases more aggressively, or a different corpus, would score lower. Re-run
`py scripts/measure_grounding.py --notebook <nb> --provider groq` after changing
`_TEXT_SUPPORT_THRESHOLD` before trusting any future number.

## Wave 20 - Close out the measurement: fix what it found, correct what it disproved (executed)

Follows Wave 19. Four items; the first two exist because the measurement
overturned a conclusion the previous commit had baked into a test.

| ID | Sev | Location | Description | Status | Fix | Tests |
|----|-----|----------|-------------|--------|-----|-------|
| L177 | MED | `validation/citation.py:_content_words` | The only real false drop the measurement surfaced. The same fact written "activates 104.2 **billion** parameters" scored 0.50 and was kept, while "activates 104.2**B** parameters" scored 0.57 and was **dropped** -- a single-letter token loses to a spelled-out word, so the metric penalised abbreviation rather than ungroundedness. | **fixed** | `_normalize_units` expands a digit-adjacent `B`/`T`/`M`/`K` to its long form, applied to **both** sides of `_text_coverage` so either spelling can abbreviate. Uppercase-only, and the trailing `\b` keeps `MPa` intact; lowercase single letters are ambiguous (`m` is metres as often as million) and are left alone | `test_unit_abbreviations_score_the_same_as_long_form` (0.57 -> 0.75 on the measured pair), `test_unit_normalisation_is_symmetric`, `test_compound_units_are_not_mangled`, `test_unit_normalisation_does_not_loosen_rejection` (fabrications still 0.14 / 0.09) |
| L181 | MED | `tests/security/test_grounding_calibration.py` | The two committed `xfail` tests asserted "no threshold works" and recommended per-clause scoring. Wave 19 disproved both. A wrong test is worse than no test, and this one would have been read as a pending product bug. | **fixed** | Corpus split into `GROUNDED` (representative, must survive) and `ADVERSARIAL_PARAPHRASE` (probe-only, currently dropped, **accepted**). Both xfails deleted and replaced with three passing guards | `test_representative_claims_survive`, `test_adversarial_paraphrase_is_currently_dropped`, `test_representative_and_reject_bands_do_not_meet` |
| L179 | MED | `prompt_templates/worker.jinja`, `synthesizer.jinja` | The multihop path carried **no** "data not instructions" rule. `security/scan.py` is telemetry, not a gate, so the delimiter plus an explicit instruction is the whole defense -- and the instruction half was missing from exactly the two templates that consume retrieved text. Mitigating: the chunks *are* `<source>`-tagged there (`ask.py:140,143,176,178`), so Wave 13's escaping applied. | **fixed** | Ported the `ask_post.jinja` reminder into both, worded for their role | `tests/security/test_prompt_injection_rule.py` enumerates the templates, so a fifth cannot be added without the rule; also asserts `ask_system.jinja` is correctly *excluded* (it receives no retrieved text) |
| L180 | MED | `agents/loop.py:413`, `chat/ask.py:AskResult`, `tui/screens/chat.py:on_turn_result` | A partial grounding drop was **silent**: `loop.py:405-408` renders only surviving claims, and the sole trace was a `logger.info` on a logger the TUI pins to `WARNING`. The user saw a confident, incomplete answer with no signal. Measured: 1 of 18 claims vanished this way. | **fixed** | `AskResult.dropped_claims` carries the count out; the TUI shows `N claim(s) omitted -- not sufficiently supported by the cited source.` as a transcript info line, mirroring the existing "Skill applied:" line. Answer text stays clean, per the owner's choice. CLI `ask` is unaffected -- it uses `validate_freeform_answer` and never drops | `tests/test_tui_grounding_notice.py` (4, headless, no idle-polling), `test_grounded_answer_reports_dropped_claim_count`, `test_grounded_answer_reports_no_drops_when_all_claims_survive`, `test_askresult_dropped_claims_defaults_to_zero` |

**Wave 20 item 3 was not run.** The planned live re-measure
(`scripts/measure_grounding.py`) was started and then cancelled. The offline
controls stand in: the measured pair moved 0.57 -> 0.75, and rejection is
unchanged at 0.14 / 0.09. Because the normaliser is uppercase-only and applies
symmetrically to both sides, it can only widen coverage on numeric-unit tokens,
never narrow it -- so the offline evidence is sufficient for this specific
change. Re-run the harness before making any *future* threshold change.

**Noted, not fixed:** `chat/ask.py:179` hands the synthesizer each worker's raw
model output as unframed `--- Answer N ---` blocks. That is a model-output
channel rather than a document channel, so the risk is weaker, but it is an
unframed handoff between two model stages and deserves its own look.

Suite: **634 passed** (was 615 + 2 xfailed; the xfails are now real guards).
New and changed test files are ruff-clean; the four touched source files are
208 -> 208 findings, all pre-existing house style.
