# How OpenNote thinks

Just a handful of ideas explain almost everything the tool does.

## 1. A notebook is one topic

Everything for a topic — sources, search index, artifacts, conversation — lives in `.opennote/notebooks/<name>/` under your project. Keep one notebook per subject: a single giant notebook blurs topics and weakens citations.

## 2. Sources become citable chunks

`opennote ingest` reads PDFs (Docling, with a fallback), DOCX, HTML/URLs, and TXT/MD. It splits them into ~800-character pieces that remember where they came from (page, heading, paragraph), embeds them locally with `BAAI/bge-small-en-v1.5`, and stores them in a local ChromaDB. Five sources per notebook; re-ingesting an unchanged file is skipped unless you pass `--force`.

## 3. Search is hybrid and local — no key needed

Every query combines BM25 keywords with vector similarity (`--bm25-alpha` tunes the blend, `--no-bm25` disables it). The number of results adapts to corpus size. `opennote search` is the LLM-free way to check what's in your sources; `opennote golden` measures recall against a labelled set.

## 4. Answers are grounded, not guessed

- `ask` — one shot: retrieve, answer, check citations.
- `chat` — a multi-round agent: the model calls `search` / `list_sources` / skills when it needs evidence, and must finish through `submit_grounded_answer`. Claims that don't match a cited passage get dropped.
- Providers are bring-your-own-key: Anthropic, OpenAI, OpenCode, Cerebras, Groq, Google, Meta, plus offline GGUF via `opennote local`. Keys live in the OS keychain (or an env var); models are validated live against `GET /models`.

## 5. Studio turns sources into study material

Six generators write Markdown into the notebook's `artifacts/` folder: mind map (also viewable as a tree inside the terminal), study guide, FAQ, briefing, timeline, and suggested questions. There's also narrated audio (TTS chain) and narrated video (Pillow slides → TTS → ffmpeg), capped and honest: if a backend isn't available, you get a script or transcript instead of a fake success.

## 6. Nothing about cost or context is hidden

The TUI shows tokens used, percentage of the model window, and spend per session. A 12k-character context budget keeps prompts bounded, and `/context` gives the full picture.

## 7. You decide the defaults

`opennote.json` is the one config file (global → project → project-local, last one wins). It holds your default model, theme, keybinds, retrieval tuning, permissions, and minimal custom OpenAI-compatible providers. The provider you last used is remembered globally, so reopening OpenNote puts you back where you left off — but if that key is gone, OpenNote says "no provider connected" rather than showing a phantom selection. `opennote config --effective` shows the resolved picture.

## The big picture

Your documents stay on your machine. The only thing that reaches a cloud provider is the question and the retrieved context you asked it to see — and only through a provider you connected yourself.
