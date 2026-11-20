"""Key validation via each provider's ``GET /models`` endpoint.

Uses plain ``httpx`` (no vendor SDKs) so every provider rides one uniform,
easily mockable flow. Classification: valid/invalid-key/network/http-error.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import httpx

from opennote.auth.registry import Provider

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_TIMEOUT = 15.0


@dataclass
class ValidationResult:
    ok: bool
    models: List[str] = field(default_factory=list)
    error: Optional[str] = None  # "invalid-key" | "network" | "http"

    @classmethod
    def success(cls, models: List[str]) -> "ValidationResult":
        return cls(ok=True, models=models)

    @classmethod
    def invalid_key(cls) -> "ValidationResult":
        return cls(ok=False, error="invalid-key")

    @classmethod
    def network(cls) -> "ValidationResult":
        return cls(ok=False, error="network")

    @classmethod
    def http(cls, status: int) -> "ValidationResult":
        return cls(ok=False, error=f"http-{status}")


def _headers(provider: Provider, api_key: str) -> dict:
    if provider.flavor == "anthropic":
        return {
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }
    return {"Authorization": f"Bearer {api_key}"}


def _model_ids(payload) -> List[str]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            ids = []
            for item in data:
                if isinstance(item, dict):
                    mid = item.get("id")
                    if isinstance(mid, str):
                        ids.append(mid)
            return ids
    return []


def _effective_models_url(provider: Provider) -> str:
    """Return the provider's models URL, honouring an endpoint override."""
    try:
        from opennote.auth.config import AuthConfig

        override = AuthConfig().get(provider.id)
        base = override.base_url_override if override and override.base_url_override else None
        if base:
            return base.rstrip("/") + "/models"
    except Exception:
        pass
    return provider.models_url


_MAX_REDIRECTS = 5
_REDIRECT_CODES = (301, 302, 303, 307, 308)


def validate_key(
    provider: Provider,
    api_key: str,
    transport: Optional[httpx.BaseTransport] = None,
    timeout: float = DEFAULT_TIMEOUT,
    models_url: Optional[str] = None,
) -> ValidationResult:
    """Validate ``api_key`` against ``provider``.

    ``transport`` is injectable for tests (``httpx.MockTransport``); when None
    a real connection is made. ``models_url`` overrides the provider's
    ``models_url`` (used for tunnel endpoints).

    Redirects are walked manually so the API key is stripped the moment a
    response sends us to a different host — a custom endpoint or a compromised
    gateway must never be able to bounce the key to a third party.
    """
    from urllib.parse import urljoin, urlparse

    headers = _headers(provider, api_key.strip())
    secret_headers = set(headers)
    target_url = models_url or _effective_models_url(provider)
    try:
        origin = urlparse(target_url).netloc.lower()
        if urlparse(target_url).scheme not in ("http", "https"):
            return ValidationResult.network()
        with httpx.Client(transport=transport, timeout=timeout, follow_redirects=False) as client:
            for _ in range(_MAX_REDIRECTS + 1):
                response = client.get(target_url, headers=headers)
                location = response.headers.get("location")
                if (
                    response.status_code in _REDIRECT_CODES
                    and location
                ):
                    next_url = urljoin(target_url, location)
                    next_origin = urlparse(next_url).netloc.lower()
                    if urlparse(next_url).scheme not in ("http", "https"):
                        return ValidationResult.network()
                    if next_origin != origin:
                        headers = {k: v for k, v in headers.items() if k not in secret_headers}
                        origin = next_origin
                    target_url = next_url
                    continue
                break
    except httpx.HTTPError:
        return ValidationResult.network()

    if response.status_code == 200:
        try:
            payload = response.json()
        except ValueError:
            return ValidationResult.http(200)
        return ValidationResult.success(_model_ids(payload))
    if response.status_code in (401, 403):
        return ValidationResult.invalid_key()
    return ValidationResult.http(response.status_code)