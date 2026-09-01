# OpenNote

**Grounded, cited Q&A over your own sources — in the terminal.**

A NotebookLM-style tool for the command line: ingest any document, chat with it, and get answers with explicit citations. Bring your own key (BYOK); every answer roots back to the source page/heading/timestamp, never the model's general knowledge.

---

## ⚡ Quick start

```bash
pip install -e ".[dev]"

opennote create my_notebook
opennote ingest paper.pdf --notebook my_notebook
opennote ask "What does the paper claim about retrieval?" --notebook my_notebook
opennote chat --notebook my_notebook
```

> If you have an unrelated `npm` package named `opennote` installed globally, it shadows this CLI on PATH. Use `py -m opennote.cli <cmd>` as an unambiguous alternative.

---

## 📦 Features

| Category | What you get |
|----------|-------------|
| **Ingestion** | PDF (Docling + fallback), DOCX, HTML/URL, TXT/MD — with heading/paragraph locators and section-heading citations |
| **Retrieval** | Vector search (SentenceTransformer + ChromaDB) + BM25 hybrid; always available, no API key required |
| **BYOK Chat** | Anthropic, OpenAI, OpenCode, Cerebras, Groq, Google — keys in OS keychain or env; live model validation via `GET /models` |
| **Grounded Q&A** | Single-turn `ask` with citation validation; multi-turn `chat` where the model decides when to search |
| **Studio generators** | Mind‑map, study guide, FAQ, briefing, timeline, suggested questions — with audio and narrated video output |
| **TTS chain** | groq → openai → gemini → edge-tts; graceful degradation to markdown transcript |
| **Narrated video** | Per‑slide Pillow images + TTS mp3 + ffmpeg mux to MP4; degrades to script + slides if any stage fails |
| **Textual TUI** | Tab cycles `ask → search → studio`; `/studio` submenu; slash commands `/mindmap /study /faq /briefing /timeline /suggest /audio /video /open /theme /help` |

---

## 🛠 Installation

```bash
pip install -e ".[dev]"
```

*Note:* if you already have an `opennote` npm package installed globally, it shadows the CLI on PATH. Use `py -m opennote.cli <cmd>` as an unambiguous alternative.

---

## 🧭 Usage

### Notebook management

```bash
opennote create <name> [--model BAAI/bge-small-en-v1.5]
opennote list
opennote rename <old> <new>
opennote delete <name>
```

### Ingest sources

```bash
opennote ingest [path-or-url] --notebook <name> [--parser auto|docling|fallback] [--ocr] [--force]
```

### Vector search (LLM‑free, cited)

```bash
opennote search "<query>" --notebook <name> --top-k 3 [--source file.pdf]
```

### Golden-set evaluation

```bash
opennote golden golden.tsv --notebook <name> --top-k 5
```

### BYOK keys

```bash
opennote auth add anthropic        # prompts for key, validates live, auto-picks a model
opennote auth list                 # providers, key source (keychain/env), selected models
opennote auth models openai        # live chat models; --set <id> to change default
opennote auth verify               # re-validate stored keys
opennote auth remove groq
```

Keys stored in the OS keychain when available, else read from `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENCODE_API_KEY`, `CEREBRAS_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`. Validation requires network; `--no-verify` stores without checking.

### Grounded Q&A

```bash
# Single‑turn: retrieve → ground → complete → validate citations
opennote ask "What does the paper claim about retrieval?" --notebook <name> [--provider groq] [--top-k 5]

# Agent chat: model decides when to search, may search several times, answers with citations
opennote chat --notebook <name> [--new | --resume <id>] [--provider <id>]
```

### TUI (terminal UI)

Run bare `opennote` to launch the terminal UI. Tab cycles `ask → search → studio`; `/studio` opens the generator submenu. Slash commands: `/mindmap /study /faq /briefing /timeline /suggest /audio /video /open /theme /help`.

---

## 🧩 Capabilities (runtime‑probed, advertised to the model)

- **Tavily web search** requires `TAVILY_API_KEY`; otherwise degrades gracefully
- **TTS** resolves groq → openai → gemini → edge-tts; falls back to markdown transcript
- **Video** requires TTS + ffmpeg on PATH; each stage degrades independently
- **Retrieval** is always available (local embeddings + ChromaDB)

---

## 📁 Layout (source tree)

```
opennote/
    cli.py              # Typer CLI (create/list/rename/delete/ingest/search/golden/ask/chat/auth/version)
    capabilities.py     # runtime capability probe (web_search / tts / video / retrieval)
    notebooks.py        # notebook manager (one folder per notebook)
    ingest/
        chunking.py     # DocumentChunk, sliding‑window chunking, hashing
        pipeline.py     # discovery, parser selection, orchestration
        parsers/        # SourceParser protocol + pdf_docling + pdf_fallback + text + docx + html
    store/
        manifest.py     # file‑hash change detection
        vectors.py      # SentenceTransformer + ChromaDB, model‑mismatch guard
    retrieval/
        retriever.py    # Retriever -> SearchResult objects (the RAG seam)
        bm25.py         # keyword BM25 retriever; hybrid_search() with vector scores
        citations.py    # [file, p.4-5] citation formatting
        eval.py         # recall@k evaluation harness
    chat/
        client.py       # OpenAI‑compat + Anthropic LLM adapters (BYOK)
        ask.py          # grounded single‑turn ask with validated citations
        prompt.py       # system prompt + context builder
    agents/
        loop.py         # agent turn loop: model‑driven retrieval
        tools.py        # search / list_sources / web_search tool schemas
        session.py      # per‑notebook session persistence + resume
    auth/
        registry.py     # provider registry (6 providers)
        keychain.py     # OS‑keychain/env key resolution
        config.py       # per‑provider model selection
        validate.py     # live key validation via GET /models
        cli.py          # opennote auth sub‑commands
    websearch.py        # Tavily web search + SSRF‑guarded read_page
    artifacts.py        # studio generators + atomic artifact persistence
    audio/tts.py        # TTS chain (groq → openai → gemini → edge‑tts) + transcript fallback
    video.py            # narrated slideshow (per‑slide TTS + ffmpeg concat to .mp4)
    tui/
        app.py          # Textual App entry (bare `opennote`)
        screens/chat.py # chat screen: ask/search/studio modes, slash commands
        commands.py     # slash‑command registry
        theme.py        # light/dark palettes
        widgets/        # transcript, prompt, command popup
tests/                  # pytest suite (336 passing)
```

---

## 🛤 Roadmap (future)

- Images (OCR) support
- Non‑terminal UI surfaces
- More parser backends
- Plugin‑based studio generators

---

## 📄 License

MIT — see `LICENSE` for details.