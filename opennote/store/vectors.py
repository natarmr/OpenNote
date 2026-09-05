"""Vector store management: local embeddings + ChromaDB persistence."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from opennote.ingest.chunking import DocumentChunk
from opennote.store.manifest import Manifest

logger = logging.getLogger("opennote.store.vectors")

DEFAULT_EMBED_MODEL = "BAAI/bge-small-en-v1.5"

_MODEL_CACHE: Dict[tuple[str, str], Any] = {}


_OPEN_MANAGERS: dict[str, list["VectorStoreManager"]] = {}


class VectorStoreManager:
    """
    Manages local SentenceTransformer embeddings and ChromaDB storage with
    model mismatch protection and incremental caching.
    """

    def __init__(
        self,
        collection_name: str,
        store_dir: Path,
        model_name: str = DEFAULT_EMBED_MODEL,
        device: Optional[str] = None,
        force_reindex: bool = False,
        read_only: bool = False,
    ):
        self.collection_name = collection_name
        self.store_dir = Path(store_dir)
        self.model_name = model_name
        self.read_only = read_only
        self.manifest_file = self.store_dir / f".{collection_name}_manifest.json"

        # 1. Device selection
        self.device = self._pick_device(device)

        # 2. Load embedding model (local-first: skip HF freshness checks when cached)
        logger.info(
            f"Loading embedding model '{self.model_name}' on device '{self.device}'..."
        )
        self.model = self._load_embedding_model()

        # 3. Initialize ChromaDB persistent client
        import chromadb

        logger.info(f"Initializing ChromaDB vector store at '{self.store_dir}'...")
        self.chroma_client = chromadb.PersistentClient(path=str(self.store_dir))
        self.collection = self._resolve_collection(force_reindex, read_only)

        self.manifest = Manifest(self.manifest_file)
        # Track open managers so NotebookManager.delete can close them on Windows
        try:
            _OPEN_MANAGERS.setdefault(str(self.store_dir.resolve()), []).append(self)
        except Exception:
            pass

    def _load_embedding_model(self):
        """Load the embedding model, preferring a cached local copy."""
        cache_key = (self.model_name, self.device)
        if cache_key in _MODEL_CACHE:
            logger.info(f"Reusing cached embedding model '{self.model_name}' on '{self.device}'")
            return _MODEL_CACHE[cache_key]

        from sentence_transformers import SentenceTransformer

        try:
            model = SentenceTransformer(
                self.model_name, device=self.device, local_files_only=True
            )
        except OSError:
            logger.info(
                f"Embedding model '{self.model_name}' not cached; downloading..."
            )
            model = SentenceTransformer(self.model_name, device=self.device)
        _MODEL_CACHE[cache_key] = model
        return model

    @staticmethod
    def _pick_device(preferred: Optional[str]) -> str:
        if preferred is not None:
            return preferred
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    def _create_collection(self):
        return self.chroma_client.create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine", "model": self.model_name},
        )

    def _resolve_collection(self, force_reindex: bool, read_only: bool = False):
        # Chroma's list_collections() return type has drifted across 0.5 -> 1.x:
        # it may yield Collection objects (.name) or plain name strings. Accept both.
        existing = self.chroma_client.list_collections()
        names = {
            c if isinstance(c, str) else getattr(c, "name", c)
            for c in existing
        }
        exists = self.collection_name in names

        if read_only:
            if not exists:
                raise ValueError(
                    f"Collection '{self.collection_name}' does not exist in "
                    f"'{self.store_dir}'. Ingest sources first."
                )
            return self.chroma_client.get_collection(self.collection_name)

        if not exists:
            return self._create_collection()

        collection = self.chroma_client.get_collection(self.collection_name)
        meta = getattr(collection, "metadata", None) or {}
        stored_model = meta.get("model")

        if stored_model and stored_model != self.model_name:
            if not force_reindex:
                raise ValueError(
                    f"\n[!] Embedding Model Mismatch Guard triggered:\n"
                    f"    Collection '{self.collection_name}' was built with model: "
                    f"'{stored_model}'\n"
                    f"    Current invocation requests model: '{self.model_name}'\n"
                    f"    Mixing distinct embedding models corrupts vector search.\n"
                    f"    Fix: use '--model {stored_model}', a different '--collection', "
                    f"or pass '--force' to reset."
                )
            logger.warning(
                f"Model mismatch detected (stored: '{stored_model}', requested: "
                f"'{self.model_name}'). Re-creating collection because '--force' is set."
            )
            self.chroma_client.delete_collection(self.collection_name)
            self.manifest_file.unlink(missing_ok=True)
            return self._create_collection()

        return collection

    def delete_source(self, source: str):
        """Delete all chunks belonging to a source path (for re-indexing)."""
        self.collection.delete(where={"source": source})
        self.manifest.data.pop(source, None)
        self.manifest.save()

    def add_chunks(self, chunks: List[DocumentChunk], batch_size: int = 64) -> int:
        """Embed and upsert document chunks into ChromaDB."""
        if not chunks:
            logger.warning("No chunks to insert.")
            return 0

        logger.info(
            f"Generating embeddings for {len(chunks)} chunks (batch size: {batch_size})..."
        )
        texts = [c.content for c in chunks]
        metadatas = [c.metadata for c in chunks]
        ids = [c.chunk_id for c in chunks]

        from tqdm import tqdm
        import os as _os
        import sys as _sys

        _disable_tqdm = _os.environ.get("HF_HUB_DISABLE_PROGRESS_BARS") == "1" or not _sys.stderr.isatty()
        total_inserted = 0
        for i in tqdm(range(0, len(texts), batch_size), desc="Embedding & Ingesting", disable=_disable_tqdm):
            batch_texts = texts[i : i + batch_size]
            batch_metas = metadatas[i : i + batch_size]
            batch_ids = ids[i : i + batch_size]

            embeddings = self.model.encode(
                batch_texts,
                batch_size=batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
            ).tolist()

            self.collection.upsert(
                ids=batch_ids,
                documents=batch_texts,
                embeddings=embeddings,
                metadatas=batch_metas,
            )
            total_inserted += len(batch_texts)

        logger.info(
            f"Successfully indexed {total_inserted} vector chunks in collection "
            f"'{self.collection_name}'."
        )
        return total_inserted

    def search(
        self,
        query: str,
        top_k: int = 5,
        where_filter: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Query the vector database for the most similar chunks."""
        truncated = query if len(query) <= 60 else f"{query[:57]}..."
        logger.info(
            f"Searching collection '{self.collection_name}' for: '{truncated}'"
        )
        query_embedding = self.model.encode([query], normalize_embeddings=True).tolist()

        results = self.collection.query(
            query_embeddings=query_embedding,
            n_results=top_k,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )

        formatted = []
        if results and results["documents"]:
            for doc, meta, dist, cid in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
                results["ids"][0],
            ):
                formatted.append(
                    {
                        "id": cid,
                        "similarity": round(1.0 - dist, 4),
                        "metadata": meta,
                        "content": doc,
                    }
                )
        return formatted

    def close(self) -> None:
        """Release ChromaDB resources (important on Windows to avoid WinError 32)."""
        try:
            # Untrack first so delete() won't see us as still-open
            lst = _OPEN_MANAGERS.get(str(self.store_dir.resolve()), [])
            if self in lst:
                lst.remove(self)
        except Exception:
            pass
        try:
            if hasattr(self, "chroma_client") and self.chroma_client is not None:
                c = self.chroma_client
                self.chroma_client = None  # type: ignore[assignment]
                self.collection = None  # type: ignore[assignment]
                if hasattr(c, "close"):
                    c.close()
                elif hasattr(c, "_system") and hasattr(c._system, "stop"):
                    try:
                        c._system.stop()
                    except Exception:
                        pass
        except Exception:
            pass

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def close_all_for_path(store_dir: Path) -> None:
    """Close any VectorStoreManager instances holding *store_dir* (Windows)."""
    try:
        key = str(Path(store_dir).resolve())
    except Exception:
        key = str(store_dir)
    for mgr in list(_OPEN_MANAGERS.get(key, [])):
        try:
            mgr.close()
        except Exception:
            pass
    _OPEN_MANAGERS.pop(key, None)
    # Also force GC to release any transient BM25 clients
    try:
        import gc

        gc.collect()
    except Exception:
        pass