import numpy as np
import pytest

from opennote.notebooks import NotebookManager


@pytest.fixture
def notebook_manager(tmp_path, monkeypatch):
    home = tmp_path / "opennote_home"
    monkeypatch.setenv("OPENNOTE_HOME", str(home))
    return NotebookManager(home=home)


@pytest.fixture
def stub_embedder(monkeypatch):
    """Replace SentenceTransformer with a tiny deterministic stub.

    Makes store/pipeline tests fast and free of HF model downloads while still
    exercising ChromaDB persistence, manifests, and the mismatch guard.
    """

    class DummySentenceTransformer:
        def __init__(self, model_name, device=None, **kwargs):
            self.model_name = model_name

        def encode(self, texts, **kwargs):
            n = len(texts) if isinstance(texts, list) else 1
            return np.random.RandomState(7).rand(n, 16)

    import sentence_transformers

    monkeypatch.setattr(
        sentence_transformers, "SentenceTransformer", DummySentenceTransformer
    )
    return DummySentenceTransformer