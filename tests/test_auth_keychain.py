import pytest
import keyring
from keyring.backend import KeyringBackend

from opennote.auth import keychain
from opennote.auth.keychain import (
    KeychainError,
    delete_key,
    get_key,
    has_key,
    mask_key,
    resolve_key,
    set_key,
)


class FakeKeyringBackend(KeyringBackend):
    """Minimal keyring backend protocol for tests."""

    def __init__(self):
        super().__init__()
        self._store = {}

    def get_password(self, service, username):
        return self._store.get((service, username))

    def set_password(self, service, username, password):
        self._store[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self._store:
            raise keyring.errors.PasswordDeleteError("no password")
        del self._store[(service, username)]


@pytest.fixture
def fake_keyring():
    backend = FakeKeyringBackend()
    real_backend = keyring.get_keyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(real_backend)


def test_set_and_get_roundtrip(fake_keyring):
    set_key("openai", "sk-secret-123")
    assert get_key("openai") == "sk-secret-123"
    assert has_key("openai")


def test_keys_scoped_by_provider(fake_keyring):
    set_key("openai", "sk-a")
    set_key("groq", "gsk-b")
    assert get_key("openai") == "sk-a"
    assert get_key("groq") == "gsk-b"


def test_delete_returns_true_when_present(fake_keyring):
    set_key("openai", "sk-a")
    assert delete_key("openai") is True
    assert get_key("openai") is None
    assert has_key("openai") is False


def test_delete_noop_when_absent(fake_keyring):
    assert delete_key("openai") is False


def test_refuses_when_no_backend(fake_keyring, monkeypatch):
    monkeypatch.setattr(keychain, "_backend_available", lambda: False)
    with pytest.raises(KeychainError, match="environment variable OPENAI_API_KEY"):
        set_key("openai", "sk-a")


def test_unknown_provider_raises(fake_keyring):
    with pytest.raises(ValueError, match="Unknown provider"):
        set_key("nope", "sk-a")


def test_set_key_surfaces_backend_failure_as_keychainerror(fake_keyring, monkeypatch):
    def boom(service, username, password):
        raise RuntimeError("no backend")

    monkeypatch.setattr(keyring, "set_password", boom)
    with pytest.raises(KeychainError, match="no backend"):
        set_key("openai", "sk-a")


def test_get_key_swallows_backend_failure(fake_keyring, monkeypatch):
    def boom(service, username):
        raise RuntimeError("backend broke")

    monkeypatch.setattr(keyring, "get_password", boom)
    assert get_key("openai") is None


def test_delete_key_survives_backend_failure(fake_keyring, monkeypatch):
    def boom(service, username):
        raise RuntimeError("backend broke")

    monkeypatch.setattr(keyring, "get_password", boom)
    assert delete_key("openai") is False


def test_resolve_key_keychain_wins_over_env(fake_keyring, monkeypatch):
    set_key("openai", "sk-keychain")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    assert resolve_key("openai") == "sk-keychain"


def test_resolve_key_env_fallback(fake_keyring, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    assert resolve_key("openai") == "sk-env"


def test_resolve_key_none(fake_keyring, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert resolve_key("openai") is None


def test_mask_key():
    assert mask_key("sk-abcdef1234567890") == "sk-a…7890"
    assert mask_key("x") == "…"
    assert mask_key("  sk-x  ") == "…"
    # Short keys never reveal a tail: too many chars would leak.
    assert mask_key("sk-abcdef") == "sk-a…"
    assert mask_key("sk-abcdefgh") == "sk-a…"