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
| L21 | LOW | `loop.py:84` | Hard-coded `max_tokens=1024` truncates long answers with no continuation | **fixed** | `max_tokens` parameter on `agent_turn` (default kept) | — |
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
| L36 | LOW | `cli.py` /sessions | `/sessions` deserializes every full session just to print 4 fields (slow with many long sessions) | **documented** | — | (documented, not fixed) |
| L37 | LOW | `cli.py` slash parsing | `/model<TAB>groq` mis-parses (partition on single space) | **fixed** | Split on whitespace (`re.split`) | `test_cli.py::test_chat_slash_model_tab_parsed` |
| L38 | LOW | `notebooks.py` list | One corrupt `notebook.json` makes `opennote list` crash entirely | **fixed** | Skip + log corrupt entries in `list()` | `test_notebooks.py::test_list_skips_corrupt_notebooks` |

## Previously fixed (Phase 5 agent-loop debugging)

| ID | Sev | Location | Description | Status | Fix | Tests |
|----|-----|----------|-------------|--------|-----|-------|
| L39 | HIGH | `agents/tools.py` | Tool called `retriever.search(..., where_filter=...)` but the real signature is `search(query, top_k, source)` → every search errored, model burned all rounds and gave up | **fixed** | Pass `source=`; updated fake retrievers to match signature | `test_agents_tools.py`, `test_agents_loop.py` |
| L40 | HIGH | `agents/loop.py` | Provider rejects a model-invented tool (`open_file`) with a 400; loop crashed instead of recovering | **fixed** | Catch provider rejection, inject corrective message listing only available tools, cost a round; add tool names to system prompt | `test_agents_loop.py::test_provider_rejection_corrects_and_retries` |
| L41 | HIGH | `cli.py` chat | `from agents.session import new_session` **shadowed the `--new` flag** → `not new_session` always False → CLI always started a fresh session, never resumed | **fixed** | Import as `create_new_session`; resume verified live | `test_cli.py::test_chat_resumes_most_recent_session` |

## Test status

Full suite: **211 passed** (was 153). The 58 additions are the regression
guards listed above plus the `trim_messages` boundary tests. Every entry above
except L36 (documented, deferred) and L21 (parameter added, no direct test)
has a dedicated regression test; several LOW items are covered as part of the
parametrized validation tests they share (L27/L28, L01/L02/L32).