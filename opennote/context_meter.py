"""Context meter: tokens, % of window used, and $ spent per turn.

Resolution order (no tiktoken, per user decision): provider-reported
``response.usage`` (exact) → chars/4 estimate (marked ``~``). Prices are
approximate public rates (USD per 1K tokens); unknown models cost $0.00
rather than a wrong number. Fill and spend are separate contracts
(opencode-style): per-turn fill never replaces session totals.
See engg_choices.md:E12.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("opennote.context_meter")

CHARS_PER_TOKEN = 4

# Context window sizes (tokens) by model substring match; fallback default.
CONTEXT_LIMITS: Dict[str, int] = {
    "gpt-oss-120b": 131072,
    "gpt-4o-mini": 128000,
    "gpt-4o": 128000,
    "llama-3.3-70b": 131072,
    "llama-3.1-8b": 131072,
    "deepseek-r1": 131072,
    "qwen": 131072,
    "gemini": 1000000,
    "claude": 200000,
}
DEFAULT_LIMIT = 128000

# (input $/1K, output $/1K) — approximate public rates.
PRICES: Dict[str, tuple[float, float]] = {
    "gpt-oss-120b": (0.00015, 0.0006),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4o": (0.005, 0.015),
    "llama-3.3-70b": (0.00035, 0.0004),
    "llama-3.1-8b": (0.0001, 0.0001),
    "deepseek-r1": (0.00055, 0.00219),
    "gemini": (0.00125, 0.005),
    "claude": (0.003, 0.015),
}


def estimate_tokens(text: str) -> int:
    """Heuristic token estimate: chars/4. Documented approximation, not exact."""
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


def context_limit_for(model: str) -> int:
    m = (model or "").lower()
    for key, limit in CONTEXT_LIMITS.items():
        if key in m:
            return limit
    return DEFAULT_LIMIT


def cost_for(model: str, in_tokens: int, out_tokens: int) -> float:
    m = (model or "").lower()
    for key, (pi, po) in PRICES.items():
        if key in m:
            return in_tokens / 1000 * pi + out_tokens / 1000 * po
    return 0.0


@dataclass
class ContextUsage:
    """Per-turn context accounting (opencode-style: fill and spend separate)."""

    input_tokens: int = 0
    output_tokens: int = 0
    limit: int = DEFAULT_LIMIT
    cost: float = 0.0
    chunks: int = 0
    model: str = ""
    # True when counts came from provider usage; False when estimated.
    exact: bool = False
    # cumulative session spend (loaded + updated per turn)
    session_spent: float = 0.0
    # cumulative session tokens (opencode-style: summed across turns)
    session_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def pct_used(self) -> float:
        return (self.total / self.limit * 100) if self.limit else 0.0

    def __add__(self, other: "ContextUsage") -> "ContextUsage":
        return ContextUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            limit=self.limit,
            cost=self.cost + other.cost,
            chunks=self.chunks + other.chunks,
            model=self.model or other.model,
            exact=self.exact and other.exact,
            session_spent=self.session_spent,
            session_tokens=self.session_tokens,
        )

    def render(self) -> str:
        prefix = "" if self.exact else "~"
        return (
            f"Context\n"
            f"{prefix}{self.total:,} tokens\n"
            f"{self.pct_used:.0f}% used\n"
            f"${self.session_spent:.2f} spent"
        )

    def render_compact(self) -> str:
        """One-line persistent readout for the prompt bar (opencode-style)."""
        prefix = "" if self.exact else "~"
        if self.limit:
            return (
                f"ctx {prefix}{self.total:,}/{self.limit:,} "
                f"{self.pct_used:.0f}% ${self.session_spent:.2f}"
            )
        return f"ctx {prefix}{self.total:,} ${self.session_spent:.2f}"


def summarize(
    system: str,
    messages: List[Dict],
    answer: str,
    model: str = "",
    chunks: int = 0,
    provider_usage: Optional["TokenUsage"] = None,
) -> ContextUsage:
    """Build a ContextUsage for a turn.

    Provider-reported ``TokenUsage`` wins (exact=True); otherwise falls back
    to the chars/4 estimate (exact=False, rendered with ``~``).
    """
    if provider_usage is not None and (provider_usage.prompt_tokens or provider_usage.completion_tokens):
        in_tokens = provider_usage.prompt_tokens
        out_tokens = provider_usage.completion_tokens
        exact = True
    else:
        in_text = system or ""
        for m in messages or []:
            c = m.get("content", "") if isinstance(m, dict) else str(m)
            if isinstance(c, str):
                in_text += "\n" + c
        in_tokens = estimate_tokens(in_text)
        out_tokens = estimate_tokens(answer or "")
        exact = False
    limit = context_limit_for(model)
    cost = cost_for(model, in_tokens, out_tokens)
    return ContextUsage(
        input_tokens=in_tokens,
        output_tokens=out_tokens,
        limit=limit,
        cost=cost,
        chunks=chunks,
        model=model,
        exact=exact,
    )


def _usage_file(notebook_dir: Optional[Path] = None) -> Path:
    if notebook_dir is not None:
        return Path(notebook_dir) / "usage.json"
    from opennote.notebooks import resolve_home

    return resolve_home() / "usage.json"


def load_spent(notebook_dir: Optional[Path] = None) -> float:
    try:
        p = _usage_file(notebook_dir)
    except Exception:
        return 0.0
    try:
        if p.exists():
            return float(json.loads(p.read_text(encoding="utf-8")).get("spent", 0.0))
    except (ValueError, OSError, AttributeError) as exc:
        # Corrupt spend file: quarantine it instead of silently resetting to $0.00.
        try:
            import time as _time

            backup = p.with_name(f"{p.stem}.corrupt.{int(_time.time())}.json")
            p.rename(backup)
            logger.warning("Spend file '%s' corrupt (%s); moved to '%s'.", p, exc, backup.name)
        except OSError:
            logger.warning("Spend file '%s' corrupt (%s).", p, exc)
    except Exception:
        pass
    return 0.0


def record_spent(amount: float, notebook_dir: Optional[Path] = None) -> float:
    """Add amount to cumulative spend file; returns new total."""
    prev = load_spent(notebook_dir)
    total = prev + max(0.0, amount)
    try:
        p = _usage_file(notebook_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"spent": total}), encoding="utf-8")
    except Exception as exc:
        # Return what is actually persisted, not an inflated in-memory total.
        logger.debug("usage record failed: %s", exc)
        return prev
    return total


def log_turn(usage: ContextUsage, where: str = "ask") -> None:
    logger.info(
        "context [%s] model=%s in=%d out=%d total=%d (%.1f%% of %d) chunks=%d cost=$%.4f session=$%.2f",
        where,
        usage.model,
        usage.input_tokens,
        usage.output_tokens,
        usage.total,
        usage.pct_used,
        usage.limit,
        usage.chunks,
        usage.cost,
        usage.session_spent,
    )
