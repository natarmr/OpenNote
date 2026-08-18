"""SourceParser protocol shared by all ingest parsers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List

from opennote.ingest.chunking import ChunkSpec, DocumentChunk


class SourceParser(ABC):
    """A parser that turns one source into a list of DocumentChunks.

    Each parser is responsible for attaching citation metadata (page / heading /
    line / timestamp) appropriate to its source type.
    """

    @abstractmethod
    def parse(self, file_path: Path, spec: ChunkSpec) -> List[DocumentChunk]:
        """Parse ``file_path`` into chunks honoring ``spec``."""
        raise NotImplementedError


class UnsupportedSourceError(ValueError):
    """Raised when no parser can handle a given source type."""