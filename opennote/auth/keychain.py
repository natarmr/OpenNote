"""OS-keychain-backed secret storage via ``keyring``.

Secrets live only in the OS keychain (Windows Credential Manager, macOS
Keychain, SecretService). If no backend is available we refuse to store —
the user falls back to environment variables (see ``resolve_key``). Secrets
never touch a plaintext file.
"""
from __future__ import annotations

import os
from typing import Optional

import keyring

from opennote.auth.registry import get_provider

SERVICE = "opennote"


class KeychainError(RuntimeError):
    """Raised when the OS keychain is unavailable or a store fails."""


#: Last backend read/delete failure. A warning routed to ``opennote``'s logger
#: is invisible under the TUI (NullHandler), so callers can surface this on the
#: result instead of silently reporting "no key" when the backend is broken.
_last_backend_error: Optional[str] = None


def last_backend_error() -> Optional[str]:
    return _last_backend_error


def _backend_available() -> bool:
    try:
        keyring.get_keyring()
        return True
    except Exception:
        return False


def set_key(provider_id: str, api_key: str) -> None:
    provider_id = _canonical_id(provider_id)
    provider = get_provider(provider_id)
    if not _backend_available():
        raise KeychainError(
            f"No OS keychain backend available. Cannot store the {provider.label} "
            f"key securely. Set the environment variable {provider.env_var} instead."
        )
    try:
        keyring.set_password(SERVICE, provider_id, api_key)
    except Exception as exc:  # noqa: BLE001 - any keyring failure is fatal here
        raise KeychainError(
            f"Failed to store the {provider.label} key in the OS keychain: {exc}"
        )


def get_key(provider_id: str) -> Optional[str]:
    global _last_backend_error
    try:
        value = keyring.get_password(SERVICE, provider_id)
        _last_backend_error = None
        return value
    except Exception as exc:  # noqa: BLE001
        _last_backend_error = str(exc)
        import logging

        logging.getLogger("opennote.auth.keychain").warning(
            "Keychain read failed for '%s': %s", provider_id, exc
        )
        return None


def delete_key(provider_id: str) -> bool:
    """Remove a stored key. Returns True if a key was present."""
    try:
        existing = keyring.get_password(SERVICE, provider_id)
        if existing is not None:
            keyring.delete_password(SERVICE, provider_id)
            return True
    except Exception as exc:  # noqa: BLE001
        import logging

        logging.getLogger("opennote.auth.keychain").warning(
            "Failed to delete key for '%s' from the keychain: %s", provider_id, exc
        )
    return False


def has_key(provider_id: str) -> bool:
    return get_key(provider_id) is not None


def _canonical_id(provider_id: str) -> str:
    try:
        from opennote.auth.registry import _ALIASES  # type: ignore[attr-defined]

        return _ALIASES.get(provider_id, provider_id)
    except Exception:
        return provider_id


def resolve_key(provider_id: str) -> Optional[str]:
    """Resolve a provider key: keychain first, then environment variable."""
    cid = _canonical_id(provider_id)
    provider = get_provider(cid)
    # Check keychain for canonical id and for the raw alias (so either storage works).
    for pid in (cid, provider_id) if cid != provider_id else (cid,):
        if (val := get_key(pid)):
            return val
    if provider.env_var and (val := os.environ.get(provider.env_var)):
        return val
    # Meta's docs use MODEL_API_KEY but users often set META_API_KEY or
    # MUSE_API_KEY — accept either without requiring a separate provider.
    if cid == "meta":
        for alt in ("META_API_KEY", "MUSE_API_KEY"):
            if alt != provider.env_var and (val := os.environ.get(alt)):
                return val
    return None


def mask_key(api_key: str, keep: int = 4) -> str:
    """Mask a key for display, e.g. ``sk-…9f2a``.

    The tail is only shown when the key is long enough that revealing
    ``keep`` trailing chars still hides most of it (avoids leaking an
    entire short key).
    """
    key = api_key.strip()
    if len(key) <= keep:
        return "…"
    if len(key) <= 4 + keep:
        return f"{key[:4]}…"
    if len(key) <= 2 * keep + 4:
        return f"{key[:4]}…"
    return f"{key[:4]}…{key[-keep:]}"