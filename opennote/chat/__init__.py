"""Chat layer: grounded, cited Q&A over a notebook (BYOK LLM providers)."""
from opennote.chat.ask import AskResult, ask
from opennote.chat.citations import used_sources
from opennote.chat.client import (
    AnthropicClient,
    ChatError,
    LLMClient,
    OpenAICompatClient,
    default_provider,
    get_client,
)
from opennote.chat.prompt import SYSTEM_TEMPLATE, build_context, build_user_message

__all__ = [
    "AskResult",
    "ask",
    "used_sources",
    "AnthropicClient",
    "ChatError",
    "LLMClient",
    "OpenAICompatClient",
    "default_provider",
    "get_client",
    "SYSTEM_TEMPLATE",
    "build_context",
    "build_user_message",
]