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


def validate_key(
    provider: Provider,
    api_key: str,
    transport: Optional[httpx.BaseTransport] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> ValidationResult:
    """Validate ``api_key`` against ``provider``.

    ``transport`` is injectable for tests (``httpx.MockTransport``); when None
    a real connection is made.
    """
    headers = _headers(provider, api_key.strip())
    try:
        with httpx.Client(
            transport=transport, timeout=timeout, follow_redirects=True
        ) as client:
            response = client.get(provider.models_url, headers=headers)
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