# Features — How OpenNote Thinks

Six mental models explain the whole system. Understand these and every command becomes obvious.

## 1. Notebooks — scoped research containers

A notebook is one topic of research. Everything (sources, index, artifacts, transcript) lives under `.opennote/notebooks/<name>/` in your project directory. Separate notebooks per topic keeps retrieval sharp — one giant notebook mixes contexts and dilutes citations.

## 2. Sources → chunks — ingest once, cite forever

`opennote ingest` parses PDF (Docling, with built-in fallback), DOCX, HTML/URL, TXT/MD into ~800-char chunks with heading/paragraph locators. Each chunk is embedded (`BAAI/bge-small-en-v1.5` by default) into a local ChromaDB store. Cap: 5 sources per notebook; re-ingests are hash-skipped unless `--force`.

## 3. Retrieval — hybrid, always local, no key needed

Every query runs **BM25 + vectors** (blend with `--bm25-alpha`, disable with `--no-bm25`) over an adaptive `top_k` (5→8→12 by corpus size). Results render as `[n] filename + pages` citations. `opennote search` is the LLM-free half of RAG; `opennote golden` measures recall@k against a TSV set.

## 4. Grounded Q&A — `ask` vs `chat`

- **`ask`** — single turn: retrieve → answer → validate citations. Fast, deterministic.
- **`chat`** — multi-round agent loop: the model decides when to search (`search`, `list_sources`), load skills (`skill`), run skill scripts, and must finish via `submit_grounded_answer`. Every added sentence needs a citation; uncited claims are rejected.
- **BYOK** — Anthropic, OpenAI, OpenCode, Cerebras, Groq, Google, plus offline GGUF via `opennote local`. Keys in the OS keychain; models validated live via `GET /models`.

## 5. Studio artifacts — from sources to study material

Six text generators turn retrieved context into saved Markdown under `notebook/artifacts/`: **mind-map** (parsed to a tree — `parse_mindmap` → in-terminal `Rich Tree` + scrollable viewer, `artifacts show --tree` on CLI), **study guide**, **FAQ**, **briefing**, **timeline**, **suggested questions** — plus **narrated audio** (TTS chain groq → openai → gemini → edge-tts, capped at 5,000 chars) and **narrated video** (Pillow slides → per-slide TTS → ffmpeg mux, max 20 slides). Every stage degrades honestly: no backend or no `ffmpeg`, no fake output — you get a transcript/script instead (`artifacts check` is the fast live self-check).

## 6. Context & cost — nothing hidden

The TUI's context meter shows exact or estimated tokens per turn, session totals, and spend (`usage.json`, `/context` panel). A 12k-char context budget keeps prompts bounded. You always see what the model saw.

## The big picture

Your documents never leave your machine except as prompt context to the provider **you** chose. Local embeddings, local index, local artifacts — the cloud only ever sees the question, never the library.
