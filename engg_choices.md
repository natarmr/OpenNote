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

## E18. One shared source-block renderer (delimiting is a primitive, not a template)
- **Rule:** every `<source>` block the model sees is built by `security/delimit.render_source_block()`. It escapes **both** attacker-reachable slots - `content` *and* `citation` - plus the `page` attribute, and it truncates **before** escaping. `chat/prompt.escape_source_content` survives as a thin alias for two existing test imports.
- **Why both slots:** the escape was always described as protecting chunk *bodies*, which made the citation slot look out of scope. But `retrieval/citations.py:44` builds the locator from `meta['heading']` (an author-controlled DOCX paragraph or entity-decoded `<h1>`-`<h6>`), and a Tavily `title`/`url`, and a supermemory hit's metadata reach the same field. Escaping one field of an object while its sibling goes in raw is the same defect twice.
- **Why truncate-then-escape:** the reverse order lets a cut land between `<` and its inserted backslash, re-exposing the tag at exactly the truncation boundary. Pinned by `test_truncation_cannot_re_expose_a_tag`.
- **Why `\b` in the pattern:** the historical `<source` replace was a blind prefix, so `<sources>` and `<sourceful>` were mangled. A word boundary defuses the delimiter variants without corrupting ordinary prose. Two pinned expectations (`a </source> b`, `a <source b`) stay byte-identical.
- **Why this is load-bearing:** `security/scan.py:1` says it is "telemetry, not a gate", so the delimiter is the *only* deterministic data/instruction separator. Losing it is not a cosmetic rendering bug - it reclassifies document bytes as instructions for the rest of the turn while the model still holds `read_page` / `web_search` / `memory_search`, whose effects (network egress, a durable supermemory write) land outside the operator's own view. This is the reasoning that distinguishes these leads from the run-1 rejection, which rested on a control that was self-documented as non-load-bearing.
- **Status:** implemented (L151-L153). Full suite **533 passed**. Ruff unchanged at 61 pre-existing findings on the touched files (zero new).

## E19. SSRF guard is bound to the fetch primitive, not to call sites
- **Rule:** every network fetch of untrusted URL input goes through `websearch._fetch_guarded(url)`, which (1) canonicalizes the host, (2) resolves it and rejects any private/loopback/link-local/reserved answer, (3) walks the redirect chain itself with `follow_redirects=False` and re-runs (1)+(2) on every hop, capped at 5, refusing non-http(s) hops. It returns the final validated URL; `trafilatura` still does the real fetch, and `trafilatura.extract` is deliberately not used so extraction behaviour is unchanged.
- **Why a chokepoint and not a call-site check:** the L43 asymmetry was that `read_page` checked and `web_search` did not, with a third path (`ingest <url>`) checking nothing. A guard that lives at a call site is one `git blame` away from being bypassed by the next fetch path; one that lives on the primitive cannot be skipped without deliberately reaching around it. `read_page`'s call-site check was removed so there is exactly one gate.
- **Why fail closed on numeric hosts:** the old `except ValueError: return False` failed *open* on exactly the inputs an attacker would use. `ipaddress` declines `127.1` and `0177.0.0.1`, but the platform resolver accepts both. Failing closed on a digits-and-dots host that `ipaddress` cannot parse costs nothing for real hostnames, which are never numeric.
- **Why strip the root label:** `localhost.` and `metadata.google.internal.` are absolute-name spellings that resolve normally, and they defeated both the exact-membership set and the suffix tuple. Stripping can only make the guard stricter.
- **Why we probe instead of fetching ourselves:** trafilatura 2.2.0 follows redirects inside a single `urllib3.PoolManager.request()` (`redirect=MAX_REDIRECTS`, default 2) and only reads `response.geturl()` afterwards, so there is no way to validate a hop before requesting it without replacing the fetcher. The probe reads only the status line and headers and closes the connection, so no body is transferred twice; the extra request is irrelevant next to the LLM call that motivated the fetch.
- **Accepted residual:** (a) DNS rebinding between our `getaddrinfo` and trafilatura's own resolution; (b) a stateful server that serves a different redirect on the second request. Both need a live third-party host behaving ephemerally, well beyond the web-publisher / document-author threat model. Pinning the validated IP into a custom transport was considered and rejected: it needs manual `Host`/SNI handling and is disproportionate for a local single-user tool.
- **Verified:** 8 canonicalization bypasses rejected (5 of which the pre-fix guard accepted, confirmed empirically), public URLs unaffected, redirect chain validated per hop; full suite **559 passed**.

## E20. Grounding is two-tier: quote verbatim, claim text supported
- **Rule:** a claim survives only if (1) its `quote_span` matches a cited chunk at 0.85 **and** (2) its own prose is carried by that same chunk at 0.6 content-word coverage. `summary` is held to the kept claims' text instead of riding in on any surviving quote.
- **Why two tiers and not one number:** the two conditions answer different questions. Tier 1 asks "was a span copied near-verbatim?" Tier 2 asks "is the sentence built around that span supported?" One threshold cannot serve both, and collapsing them is what let the gap exist: the module docstring promised groundedness while only copying was ever checked.
- **Why 0.6 and not 0.85 for the text:** a claim's prose is a paraphrase of its quote, so it will never match verbatim. 0.85 would abstain on correct answers. 0.6 is an owner-approved starting point to be tuned from logged traffic - not a settled value.
- **Why content-word coverage, not difflib ratio:** `fuzzy_contains` is a max over sliding windows, so a long chunk containing one matching 30-char window passes - that leniency is exactly why `validate_freeform_answer` is weak. Containment ("what fraction of the claim's words does the source carry") is the right shape: a grounded claim reuses the source's vocabulary, an injected instruction introduces words the document never uses.
- **Why not a stemmer:** prefix tolerance handles `cost`/`costs`. `strong`/`strength` share only `str`; a rule loose enough to merge them also merges unrelated words, so stemmer-ish behaviour is left to the threshold instead.
- **Relationship to E18:** this is the answer layer's containment for the delimiter gap. The two waves are load-bearing together - E18 stops document bytes being read as instructions, E20 stops an ungrounded sentence being rendered as a grounded one.
- **Status:** implemented (L160-L162). Full suite **565 passed**.

## E21. Plugin loading is opt-in, warn-once
- **Rule:** `PluginLoader.load()` loads in-repo built-ins unconditionally, and file-based + entry-point plugins only when `OPENNOTE_ALLOW_PLUGINS=1`. When disabled it records what it found on `loader.skipped` / `Capabilities.plugins_skipped` and logs **one** warning per directory, not one per file and not one per capability probe (which runs on every `get_capabilities()` miss).
- **Why at `load()` and not at each caller:** `load()` is the one function every caller passes through - the TUI sidebar render, the capability probe, both agent-turn sites, the two lazy loads in `agents/tools.py`, and the CLI. Gating there also covers the entry-point branch, which never goes through `_import_file` and would be missed by a file-walk-only check.
- **Why warn-once rather than hard-off:** a hard default silently breaks anyone using plugins today, and the failure looks like "my plugin stopped working" rather than "a trust decision changed". One warning naming the directory and the variable converts that into an actionable message. The user chose this over hard-off.
- **Why built-ins are exempt:** they ship in this repository and are therefore not attacker-reachable input; the only one (supermemory) is already gated on `SUPERMEMORY_API_KEY`. Gating them would break the feature with no security gain.
- **Why the probe is now safe:** `opennote capabilities` prints the result of `_probe()`. Before this change, a diagnostic could execute repository-adjacent code as a side effect. The gate being *inside* `load()` (rather than around it) means every caller, including the probe, inherits it without each having to remember.
- **Isolation contract restored:** `_import_file` deliberately re-raises `BaseException` so a half-imported module is removed from `sys.modules`; `load()` was catching only `Exception`, so `sys.exit()` in a plugin killed the process. Now `KeyboardInterrupt` propagates (that is the operator's Ctrl-C) and every other `BaseException` is contained — which is what `load()`'s docstring already promised.
- **Status:** implemented (L163-L167). Full suite **578 passed**.

## E22. The Gemini thought_signature fallback adopts a validator-False answer on purpose
- **Rule:** on a Gemini `thought_signature` error with chunks already retrieved, the fallback's free-form answer is adopted **even when** `validate_freeform_answer` returns False. The discarded verdict is logged, not silently dropped.
- **Why this is not the run-1 finding:** run-1 rejected `gemini-fallback-discards-citation-gate` on two grounds, both of which stand. No boundary is crossed - the answer goes to the operator who ingested the document, into their own gitignored notebook, and to a service they enabled themselves. And the named control is not load-bearing - `validate_freeform_answer` documents itself as "Heuristic for legacy free-form" and passes on any in-range `[n]` or any 30-character overlap, so injected text already reaches the operator as "grounded" via the branch the candidate called correct.
- **Why adopt rather than abstain anyway:** the main path abstains on a False verdict, and symmetry would suggest the same here. But this branch exists *only* because a Gemini `thought_signature` error would otherwise burn all 5 tool rounds and return nothing (the reported symptom was "5 tool calls then not in source"). Abstaining here converts a degraded answer into no answer at all, for a validator that is weak enough to abstain on correct answers.
- **The residual worth knowing:** a False verdict can still pick up a `Sources:` footer, because `used_sources`' `MARKER` also matches the bracket, `【n†` and bare `(n)` forms. So an ungrounded fallback answer can be *presented* as grounded. That is a display-assurance issue, not a security boundary — but it is why the discarded verdict is now logged, so the frequency is observable instead of theoretical.
- **Not settled here:** if the fallback's False-verdict rate turns out to be high in practice, the right fix is to strengthen the validator (E20's direction), not to start abstaining in this branch.
- **Status:** implemented (L168). The `pass` is gone; the decision is now written down and observable.

## E23. The calibration corpus is a regression guard, not a benchmark
- **Rule:** `tests/security/test_grounding_calibration.py` holds a labelled corpus of claims with known-correct verdicts. Grounded paraphrase must survive; fabrication and cross-document contamination must drop. The corpus is **inlined** next to the assertions that use it, so a reviewer sees claim and expectation together when tuning.
- **Why inlined and not a JSON fixture:** the set is small and its value is reviewability. A claim and the reason it is labelled the way it is belong in the same diff. If it ever outgrows that, a data file is the right move.
- **Why self-contained fixtures:** the repo's `injection-test-set.*` and `kimi.pdf` are **gitignored and untracked** (`.gitignore:27-28`), so they exist only on this machine. The corpus inlines those sentences as literals and builds DOCX/HTML in `tmp_path`. Nothing new may depend on an untracked file.
- **What it is not:** the corpus is ~12 claims derived from one synthetic document. It guards the *shape* of a grounding failure; it is not statistically representative of a user's corpus and cannot certify that 0.6 is optimal. Growing it from real notebook chunks is a copy-paste job.
- **What it found:** the shipped threshold is measurably wrong - see `ledger.md:L175`. Two tests are `xfail(strict=False)` so the gap is recorded and will report XPASS when fixed, rather than being quietly deleted or asserted away.
- **Status:** implemented (L174-L176). Full suite **615 passed, 2 xfailed**.

## E24. Silent fallbacks are bugs, not graceful degradation
- **Rule:** where a code path has a fallback, the *result* must distinguish success from fallback, and a test must assert on the result - not on "the preferred path was attempted".
- **Why:** `web_search`'s enrichment fetch had a fallback to the bare Tavily snippet guarded by `except Exception` plus a `logger.warning`. That logger is routed to a `NullHandler` under the TUI (`cli.py:53`), so the only evidence of failure was invisible in the primary UI. The path was in fact broken for its entire life (`c.meta` on a `DocumentChunk`, `ledger.md:L174`) and nobody noticed, because "answers are slightly thinner" is not a reportable symptom.
- **How this is applied now:** `test_websearch_fetch.py` asserts that the *fetched page text* reaches the `SearchResult` and the snippet does not, and separately pins the fallback so the two states are distinguishable. A fallback that is intended should be asserted as intended; a fallback that hides a defect should not exist.
- **Generalisation:** any future `except Exception: <degrade>` around a user-visible feature needs a test that fails when the degradation is permanent, not only when it is absent.
