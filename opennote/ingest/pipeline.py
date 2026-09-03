"""Ingest pipeline orchestration: discovery, parsing, embedding, indexing."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple, Union

from opennote.ingest.chunking import (
    ChunkSpec,
    DocumentChunk,
    compute_file_hash,
)
from opennote.ingest.parsers.base import SourceParser
from opennote.ingest.parsers.docx import DocxParser
from opennote.ingest.parsers.html import HtmlParser, parse_url
from opennote.ingest.parsers.pdf_docling import DoclingParser
from opennote.ingest.parsers.pdf_fallback import FallbackPDFParser
from opennote.ingest.parsers.text import TextParser
from opennote.notebooks import Notebook
from opennote.store.vectors import VectorStoreManager

logger = logging.getLogger("opennote.ingest.pipeline")

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".txt",
    ".md",
    ".markdown",
    ".docx",
    ".html",
    ".htm",
}

_TEXT_EXTENSIONS = {".txt", ".md", ".markdown"}
_HTML_EXTENSIONS = {".html", ".htm"}


def get_pdf_parser(preferred: str, do_ocr: bool = False) -> SourceParser:
    """Instantiate a PDF parser based on a strategy and package availability."""
    if preferred == "docling":
        parser = DoclingParser(do_ocr=do_ocr)
        if parser.available:
            return parser
        logger.info("Switching to FallbackPDFParser...")
        return FallbackPDFParser()
    if preferred == "fallback":
        return FallbackPDFParser()
    parser = DoclingParser(do_ocr=do_ocr)
    if parser.available:
        return parser
    return FallbackPDFParser()


def get_parser_for_file(
    path: Path, pdf_strategy: str = "auto", ocr: bool = False
) -> Optional[SourceParser]:
    """Return a parser for ``path``'s extension, or None if unsupported."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        return get_pdf_parser(pdf_strategy, ocr)
    if ext in _TEXT_EXTENSIONS:
        return TextParser()
    if ext == ".docx":
        return DocxParser()
    if ext in _HTML_EXTENSIONS:
        return HtmlParser()
    return None


_MAX_INGEST_FILES = 500
_SKIP_DIRS = {".git", ".opennote", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules", "dist", "build"}


def find_source_files(target_path: Optional[Path]) -> List[Path]:
    """Collect supported source files from a file path or directory recursively."""
    if target_path is None or target_path == Path("."):
        target_path = Path.cwd()

    if target_path.is_file():
        if target_path.suffix.lower() in SUPPORTED_EXTENSIONS:
            return [target_path]
        logger.error(f"Unsupported file type: '{target_path}'")
        return []
    if target_path.is_dir():
        found: List[Path] = []
        for p in target_path.rglob("*"):
            # Skip hidden/build dirs for latency
            if any(part in _SKIP_DIRS or part.startswith(".") for part in p.parts):
                # Allow the target dir itself if it starts with dot (e.g. /tmp/.agents) — only skip nested hidden
                if p != target_path and any(seg.startswith(".") for seg in p.relative_to(target_path).parts[:-1]):
                    continue
                # Fallback: check any parent dir name is in skip list
                if any(seg in _SKIP_DIRS for seg in p.parts):
                    continue
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS:
                found.append(p)
                if len(found) >= _MAX_INGEST_FILES:
                    logger.warning("Ingest capped at %d files (more exist under %s)", _MAX_INGEST_FILES, target_path)
                    break
        return sorted(set(found))
    logger.error(f"Path does not exist: '{target_path}'")
    return []


Source = Union[Path, str]


def _index_chunks(
    vector_mgr: VectorStoreManager,
    notebook: Notebook,
    source: str,
    chunks: List[DocumentChunk],
    batch_size: int,
) -> int:
    """Delete old chunks for ``source``, index new ones, and mark manifest."""
    # Telemetry scan (defense 6) — log but do not block
    try:
        from opennote.security.scan import scan_and_log

        scan_and_log(notebook, chunks)
    except Exception:
        pass
    # Always clear stale chunks first — even when nothing was extracted — so
    # a file that was truncated/re-ingested doesn't keep serving old chunks.
    vector_mgr.delete_source(source)
    if not chunks:
        if source in notebook.sources:
            notebook.sources.remove(source)
            notebook.save()
        return 0
    count = vector_mgr.add_chunks(chunks, batch_size=batch_size)
    _record_source(notebook, source)
    return count


def ingest(
    notebook: Notebook,
    target: Source,
    parser: str = "auto",
    ocr: bool = False,
    chunk_size: int = 800,
    chunk_overlap: int = 120,
    batch_size: int = 32,
    device: Optional[str] = None,
    force: bool = False,
) -> int:
    """Ingest supported sources in ``target`` into ``notebook``.

    ``target`` is a file path, a directory, or an ``http(s)`` URL. Returns the
    number of chunks indexed. Local files whose content hash is unchanged are
    skipped (unless ``force``); re-ingesting a changed file replaces its old
    chunks rather than appending duplicates.
    """
    spec = ChunkSpec(size=chunk_size, overlap=chunk_overlap)
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1.")
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size.")
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1.")
    vector_mgr = VectorStoreManager(
        collection_name="documents",
        store_dir=notebook.store_dir,
        model_name=notebook.embed_model,
        device=device,
        force_reindex=force,
    )

    # --- URL source (no local hash caching) ---
    if isinstance(target, str) and target.startswith(("http://", "https://")):
        from opennote.notebooks import MAX_SOURCES

        if target not in notebook.sources and len(notebook.sources) >= MAX_SOURCES:
            raise ValueError(
                f"Notebook '{notebook.name}' already has {MAX_SOURCES} sources (limit). "
                f"Remove a source or create a new notebook."
            )
        try:
            chunks = parse_url(target, spec)
            if not chunks:
                logger.warning(f"No content extracted from '{target}'.")
                return 0
            count = _index_chunks(vector_mgr, notebook, target, chunks, batch_size)
            logger.info(f"Indexed {count} chunk(s) from URL '{target}'.")
            return count
        except ValueError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to ingest URL '{target}': {e}", exc_info=True)
            return 0

    files = find_source_files(Path(target))
    if not files:
        logger.warning(f"No supported source files found in '{target}'.")
        return 0

    files_to_process: List[Tuple[Path, str]] = []
    for f in files:
        f_hash = compute_file_hash(f)
        source = str(f.resolve())
        if not force and vector_mgr.manifest.is_indexed(source, f_hash):
            logger.info(f"Skipping '{f.name}' (content hash unchanged).")
        else:
            files_to_process.append((f, f_hash))

    if not files_to_process:
        logger.info("All discovered files are already up-to-date in the vector store.")
        return 0

    # Enforce 5-source cap upfront (atomic rejection)
    from opennote.notebooks import MAX_SOURCES

    new_distinct = [s for _, s in [(f, str(f.resolve())) for f, _ in files_to_process] if s not in notebook.sources]
    # Deduplicate
    new_distinct = list(dict.fromkeys(new_distinct))
    if len(notebook.sources) + len(new_distinct) > MAX_SOURCES:
        raise ValueError(
            f"Notebook '{notebook.name}' has {len(notebook.sources)} source(s); "
            f"ingesting {len(new_distinct)} new source(s) would exceed the {MAX_SOURCES}-source limit. "
            f"Remove a source or create a new notebook."
        )

    total_indexed = 0
    for f, f_hash in files_to_process:
        source = str(f.resolve())
        try:
            doc_parser = get_parser_for_file(f, parser, ocr)
            if doc_parser is None:
                logger.warning(f"No parser for '{f.name}'; skipping.")
                continue
            chunks = doc_parser.parse(f, spec)
            if not chunks:
                # The file exists but yields nothing (empty/truncated). Drop any
                # previously indexed chunks so stale data isn't searchable, and
                # record the hash so we don't re-parse it on every run.
                logger.warning(f"No chunks extracted from '{f.name}'.")
                _index_chunks(vector_mgr, notebook, source, [], batch_size)
                vector_mgr.manifest.mark_indexed(source, f_hash)
                continue
            logger.info(f"Extracted {len(chunks)} chunk(s) from '{f.name}'.")
            count = _index_chunks(vector_mgr, notebook, source, chunks, batch_size)
            total_indexed += count
            vector_mgr.manifest.mark_indexed(source, f_hash)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to parse '{f.name}': {e}", exc_info=True)

    return total_indexed


def _record_source(notebook: Notebook, source: str):
    if source not in notebook.sources:
        from opennote.notebooks import MAX_SOURCES

        if len(notebook.sources) >= MAX_SOURCES:
            raise ValueError(
                f"Notebook '{notebook.name}' already has {MAX_SOURCES} sources (limit). "
                f"Remove a source or create a new notebook."
            )
        notebook.sources.append(source)
        notebook.save()


def remove_source(notebook: Notebook, source: str) -> None:
    """Remove *source* from notebook: vector store, manifest, and sources list."""
    from opennote.store.vectors import VectorStoreManager

    # Remove from vector store
    try:
        vm = VectorStoreManager(
            collection_name="documents",
            store_dir=notebook.store_dir,
            model_name=notebook.embed_model,
        )
        vm.delete_source(source)
        try:
            vm.manifest.remove(source)
        except Exception:
            pass
    except Exception:
        pass
    if source in notebook.sources:
        notebook.sources.remove(source)
        notebook.save()