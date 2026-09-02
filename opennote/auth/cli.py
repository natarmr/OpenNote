"""``opennote auth`` sub-commands: BYOK key management."""
from __future__ import annotations

import os
from typing import Optional

import typer

from opennote.auth.config import AuthConfig
from opennote.auth.keychain import KeychainError, delete_key, get_key, mask_key, resolve_key, set_key
from opennote.auth.models import rank_models, select_default
from opennote.auth.registry import all_providers, get_provider
from opennote.auth.validate import validate_key

auth_app = typer.Typer(help="Manage BYOK provider keys and model selection.", no_args_is_help=True)


def _print_error(message: str):
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(1)


@auth_app.command("add")
def auth_add(
    provider_id: str = typer.Argument(..., help="Provider id (e.g. anthropic, openai, groq)."),
    no_verify: bool = typer.Option(False, "--no-verify", help="Store without validating."),
):
    """Add a provider key (validated on entry) and auto-select a model."""
    try:
        provider = get_provider(provider_id)
    except ValueError as e:
        _print_error(str(e))

    if provider_id == "local":
        _print_error(
            "Local models don't use API keys.  Run ``opennote local add <path>`` instead."
        )

    existing = resolve_key(provider.id)
    if existing:
        typer.echo(f"Note: {provider.label} is already configured — overwriting.")
        typer.echo(f"  existing key (hidden, from {'keychain' if get_key(provider.id) else 'env'})")

    api_key = typer.prompt(
        f"Paste your {provider.label} API key (get one at {provider.console_url})",
        hide_input=True,
    ).strip()
    if not api_key:
        _print_error("No key entered.")

    config = AuthConfig()

    if no_verify:
        try:
            set_key(provider.id, api_key)
        except KeychainError as e:
            _print_error(str(e))
        config.mark_added(provider.id)
        typer.echo(
            f"Stored {provider.label} key (unvalidated). "
            f"Run 'opennote auth verify {provider.id}' when online."
        )
        return

    result = validate_key(provider, api_key)
    if result.error == "invalid-key":
        _print_error(f"{provider.label} rejected the key (401/403). Double-check it at {provider.console_url}.")
    elif result.error == "network":
        _print_error(
            f"Could not reach {provider.label} ({provider.models_url}). "
            "Check connectivity, or use --no-verify to store the key now and validate later."
        )
    elif not result.ok:
        _print_error(f"{provider.label} responded with HTTP error {result.error}.")

    try:
        set_key(provider.id, api_key)
    except KeychainError as e:
        _print_error(str(e))
    config.mark_added(provider.id)
    config.mark_validated(provider.id)

    default = select_default(provider, result.models)
    if default:
        config.set_model(provider.id, default)
    typer.echo(f"[ok] {provider.label} key validated ({len(result.models)} models available).")
    if default:
        typer.echo(f"  Auto-selected model: {default}")
    else:
        typer.echo("  No chat model found; run 'opennote auth models <provider>' to pick one.")


@auth_app.command("list")
def auth_list():
    """List configured providers, key source, and selected models."""
    config = AuthConfig()
    providers = config.providers()
    if not providers:
        typer.echo("No providers configured. Run 'opennote auth add <provider>'.")
        return

    for pid in sorted(providers):
        try:
            provider = get_provider(pid)
        except ValueError:
            continue
        settings = providers[pid]
        key = resolve_key(pid)
        if key:
            source = "keychain" if get_key(pid) else f"env:{provider.env_var}"
            key_text = mask_key(key)
        else:
            source = "none"
            key_text = "-"
        model = settings.model or "(auto-pick)"
        validated = settings.last_validated_at or "never"
        typer.echo(
            f"  {provider.id:<10} {provider.label:<14} key={key_text:<14} "
            f"source={source:<18} model={model:<28} validated={validated}"
        )


@auth_app.command("models")
def auth_models(
    provider_id: str = typer.Argument(..., help="Provider id."),
    set_model: Optional[str] = typer.Option(None, "--set", help="Set the model for this provider."),
):
    """List a provider's live chat models; set the default with --set."""
    try:
        provider = get_provider(provider_id)
    except ValueError as e:
        _print_error(str(e))

    api_key = resolve_key(provider.id)
    if not api_key:
        _print_error(
            f"No key for {provider.label}. Run 'opennote auth add {provider.id}', "
            f"or set {provider.env_var}."
        )

    result = validate_key(provider, api_key)
    if not result.ok:
        _print_error(f"Validation failed for {provider.label} ({result.error}). "
                     "Your key may have been revoked — run 'opennote auth remove' / 'auth add'.")

    config = AuthConfig()
    current = None
    settings = config.get(provider.id)
    if settings and settings.model:
        current = settings.model

    if set_model is not None:
        if set_model not in result.models:
            _print_error(f"'{set_model}' is not in {provider.label}'s live model list.")
        config.set_model(provider.id, set_model)
        typer.echo(f"[ok] Set {provider.label} model to {set_model}.")
        return

    ranked = rank_models(provider, result.models)
    typer.echo(f"{provider.label} ({len(result.models)} live models, {len(ranked)} chat-usable):")
    for idx, model in enumerate(ranked, start=1):
        mark = "*" if model == current else " "
        typer.echo(f"  {mark} {idx:3}. {model}")
    if ranked:
        typer.echo("\n  * = current default. Change with: opennote auth models <provider> --set <id>")


@auth_app.command("verify")
def auth_verify(
    provider_id: Optional[str] = typer.Argument(None, help="Provider id (default: all configured)."),
):
    """Re-validate stored keys against their providers."""
    config = AuthConfig()
    if provider_id:
        try:
            get_provider(provider_id)
        except ValueError as e:
            _print_error(str(e))
        targets = [provider_id]
    else:
        targets = sorted(config.providers())
        if not targets:
            typer.echo("No providers configured. Run 'opennote auth add <provider>'.")
            return

    for pid in targets:
        try:
            provider = get_provider(pid)
        except ValueError as e:
            _print_error(str(e))
        api_key = resolve_key(pid)
        if not api_key:
            typer.echo(f"  {provider.label:<14} no key configured")
            continue
        result = validate_key(provider, api_key)
        if result.ok:
            config.mark_validated(pid)
            typer.echo(f"  [ok] {provider.label:<14} valid ({len(result.models)} models)")
        elif result.error == "invalid-key":
            typer.echo(f"  [!!] {provider.label:<14} INVALID KEY (revoked?) — run 'opennote auth remove'")
        else:
            typer.echo(f"  [!!] {provider.label:<14} {result.error}")


@auth_app.command("remove")
def auth_remove(
    provider_id: str = typer.Argument(..., help="Provider id."),
):
    """Remove a provider's key from the keychain and its config."""
    try:
        provider = get_provider(provider_id)
    except ValueError as e:
        _print_error(str(e))

    config = AuthConfig()
    key_removed = delete_key(provider.id)
    config_removed = config.remove(provider.id)
    if key_removed or config_removed:
        typer.echo(f"Removed {provider.label} key and config.")
    else:
        typer.echo(f"Nothing to remove for {provider.label}.")


def provider_ids() -> str:
    return ", ".join(p.id for p in all_providers())