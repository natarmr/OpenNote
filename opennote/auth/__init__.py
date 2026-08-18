"""BYOK auth: provider registry, keychain storage, validation, model selection."""
from opennote.auth.registry import BY_ID, PROVIDERS, Provider, all_providers, get_provider
from opennote.auth.keychain import (
    KeychainError,
    delete_key,
    get_key,
    has_key,
    mask_key,
    resolve_key,
    set_key,
)
from opennote.auth.config import AuthConfig, ProviderSettings
from opennote.auth.validate import ValidationResult, validate_key
from opennote.auth.models import (
    is_chat_model,
    is_deprecated,
    rank_models,
    select_default,
    usable_models,
)

__all__ = [
    "BY_ID",
    "PROVIDERS",
    "Provider",
    "all_providers",
    "get_provider",
    "KeychainError",
    "delete_key",
    "get_key",
    "has_key",
    "mask_key",
    "resolve_key",
    "set_key",
    "AuthConfig",
    "ProviderSettings",
    "ValidationResult",
    "validate_key",
    "is_chat_model",
    "is_deprecated",
    "rank_models",
    "select_default",
    "usable_models",
]