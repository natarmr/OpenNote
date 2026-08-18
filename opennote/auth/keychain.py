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


def _backend_available() -> bool:
    try:
        keyring.get_keyring()
        return True
    except Exception:
        return False


def set_key(provider_id: str, api_key: str) -> None:
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
    try:
        return keyring.get_password(SERVICE, provider_id)
    except Exception as exc:  # noqa: BLE001
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


def resolve_key(provider_id: str) -> Optional[str]:
    """Resolve a provider key: keychain first, then environment variable."""
    provider = get_provider(provider_id)
    from_keychain = get_key(provider_id)
    if from_keychain:
        return from_keychain
    return os.environ.get(provider.env_var) or None


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