# OpenNote

A terminal-based, NotebookLM-style tool: ingest heterogeneous sources, ask
grounded questions, and get cited answers — all from the CLI.

**Core principle (BYOK):** you bring your own LLM key; answers stay grounded in
ingested sources with explicit citations back to source location (page / heading /
timestamp), not the model's general knowledge.

## Status

- **Phase 0 — package restructure (done):** monolithic `ingest.py` absorbed into an
  installable `opennote` package with a Typer CLI, a notebook model, and a pytest suite.
- **Phase 1 — PDF pipeline hardened (done):** chroma-version-agnostic store, read-only
  search, Docling-fallback page attribution, and a synthetic-PDF regression suite.
- **Phase 2 — retrieval + citation layer (done):** `Retriever`, citation formatting
  (`[file, p.4-5]`), and a recall@k eval harness. This is the LLM-free "R" of RAG;
  the BYOK generation layer (Phase 4) consumes these `SearchResult` objects.
- **Phase 3 — multi-format ingestion (done):** txt/md (line citations), docx
  (heading/paragraph locators), and HTML/URL (section-heading citations), all behind
  the `SourceParser` protocol. Embedding loads are now local-first
  (`local_files_only`, fallback to download) so CLI invocations skip the slow
  HuggingFace freshness checks.
- Later: images (OCR), then agent shell + BYOK LLM, UI.

## Install

```bash
pip install -e ".[dev]"
```

> Note: if you already have an unrelated npm package named `opennote` installed
> globally, it shadows this CLI on PATH. Use `py -m opennote.cli <cmd>` as an
> unambiguous alternative.

## Concepts

A **notebook** is one self-contained folder on disk:

```
~/.opennote/notebooks/<name>/
    notebook.json          # name, embedding model identity, created, sources
    chroma/                # ChromaDB vector store + per-collection manifest
```

The embedding model identity is stored per-notebook so vector spaces are never
silently mixed. Point `OPENNOTE_HOME` elsewhere to relocate the store.

## Usage

```bash
# Manage notebooks
opennote create <name> [--model BAAI/bge-small-en-v1.5]
opennote list
opennote rename <old> <new>
opennote delete <name>

# Ingest a source file, directory, or URL (pdf, txt, md, docx, html, htm)
opennote ingest [path-or-url] --notebook <name> [--parser auto|docling|fallback] [--ocr] [--force]

# Vector search a notebook (LLM-free retrieval, cited)
opennote search "<query>" --notebook <name> --top-k 3 [--source file.pdf]

# Evaluate retrieval recall@k against a golden set (TSV: query, source, pages)
opennote golden golden.tsv --notebook <name> --top-k 5
```

Ingestion skips files whose SHA256 content hash is unchanged; re-ingesting a
changed file replaces its old chunks rather than appending duplicates.

`opennote search` is the retrieval half of RAG — it returns top chunks with
citations (`[file.pdf, p.4-5]`), using the local embedding model and no LLM key.
The BYOK generation layer plugs into these `SearchResult` objects.

## Layout

```
opennote/
    cli.py              # Typer CLI (create/list/rename/delete/ingest/search/golden/version)
    notebooks.py        # notebook manager (one folder per notebook)
    ingest/
        chunking.py     # DocumentChunk, sliding-window chunking, hashing
        pipeline.py     # discovery, parser selection, orchestration
        parsers/        # SourceParser protocol + pdf_docling + pdf_fallback
    store/
        manifest.py     # file-hash change detection
        vectors.py      # SentenceTransformer + ChromaDB, model-mismatch guard
    retrieval/
        retriever.py    # Retriever -> SearchResult objects (the RAG seam)
        citations.py    # [file, p.4-5] citation formatting
        eval.py         # recall@k evaluation harness
tests/                  # pytest suite
```