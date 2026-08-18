import pytest

from opennote.notebooks import Notebook, NotebookManager, validate_notebook_name


def test_create_and_get(notebook_manager):
    nb = notebook_manager.create("alpha", embed_model="model-a")
    assert nb.directory.exists()
    assert (nb.directory / "notebook.json").exists()

    loaded = notebook_manager.get("alpha")
    assert loaded.name == "alpha"
    assert loaded.embed_model == "model-a"


def test_duplicate_create_raises(notebook_manager):
    notebook_manager.create("alpha")
    with pytest.raises(FileExistsError):
        notebook_manager.create("alpha")


def test_list(notebook_manager):
    notebook_manager.create("one")
    notebook_manager.create("two")
    names = [nb.name for nb in notebook_manager.list()]
    assert names == ["one", "two"]


def test_rename_updates_metadata(notebook_manager):
    notebook_manager.create("old")
    renamed = notebook_manager.rename("old", "new")
    assert renamed.name == "new"
    assert notebook_manager.get("new").name == "new"
    with pytest.raises(KeyError):
        notebook_manager.get("old")


def test_delete_removes_directory(notebook_manager):
    notebook_manager.create("gone")
    notebook_manager.delete("gone")
    assert not (notebook_manager.notebooks_dir / "gone").exists()


def test_embed_model_persists_across_saves(notebook_manager):
    nb = notebook_manager.create("m", embed_model="BAAI/bge-small-en-v1.5")
    nb.sources.append("s1")
    nb.save()
    loaded = notebook_manager.get("m")
    assert loaded.embed_model == "BAAI/bge-small-en-v1.5"
    assert loaded.sources == ["s1"]


# --- L01/L02/L32: unsafe name validation (path traversal, reserved) ---

@pytest.mark.parametrize("bad", ["../evil", "a/b", "", "..", ".", "CON", "NUL", "COM1", "PRN"])
def test_validate_rejects_unsafe_names(bad):
    with pytest.raises(ValueError):
        validate_notebook_name(bad)


def test_create_rejects_path_traversal_and_reserved(notebook_manager):
    for bad in ("../evil", "a/b", "", "CON"):
        with pytest.raises(ValueError):
            notebook_manager.create(bad)
    # Nothing escaped the notebooks directory.
    assert not (notebook_manager.notebooks_dir.parent / "evil").exists()


def test_get_rejects_unsafe_names(notebook_manager):
    with pytest.raises(ValueError):
        notebook_manager.get("../secret")


def test_rename_rejects_path_traversal(notebook_manager):
    notebook_manager.create("ok")
    with pytest.raises(ValueError):
        notebook_manager.rename("ok", "../escape")
    with pytest.raises(ValueError):
        notebook_manager.rename("ok", "a/b")


def test_case_insensitive_collisions(notebook_manager):
    notebook_manager.create("beta")
    with pytest.raises(FileExistsError):
        notebook_manager.create("BETA")
    with pytest.raises(FileExistsError):
        notebook_manager.rename("beta", "BETA")


# --- L01/L34: delete requires notebook.json; atomic saves ---

def test_delete_refuses_directory_without_notebook_json(notebook_manager):
    orphan = notebook_manager.notebooks_dir / "orphan"
    orphan.mkdir()
    (orphan / "data.txt").write_text("x", encoding="utf-8")
    with pytest.raises(KeyError):
        notebook_manager.delete("orphan")
    assert orphan.exists(), "an arbitrary directory must never be rmtree'd"


def test_delete_rejects_unsafe_names(notebook_manager):
    for bad in ("../evil", "", "a/b"):
        with pytest.raises(ValueError):
            notebook_manager.delete(bad)


def test_save_leaves_no_tmp_files(notebook_manager):
    nb = notebook_manager.create("clean")
    nb.sources.append("s")
    nb.save()
    leftovers = [p for p in nb.directory.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


# --- L38: list survives corrupt notebooks ---

def test_list_skips_corrupt_notebooks(notebook_manager):
    notebook_manager.create("good")
    bad = notebook_manager.notebooks_dir / "bad"
    bad.mkdir()
    (bad / "notebook.json").write_text("{not json", encoding="utf-8")
    names = [nb.name for nb in notebook_manager.list()]
    assert names == ["good"]
