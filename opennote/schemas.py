"""Structured output schemas for grounded answers (injection defense)."""

from __future__ import annotations

from typing import List, Optional

try:
    from pydantic import BaseModel, Field
except ImportError:
    # Fallback minimal stub if pydantic not installed (tests can still import)
    class BaseModel:  # type: ignore
        def model_dump(self): return self.__dict__
        @classmethod
        def model_json_schema(cls): return {}
    Field = lambda *a, **kw: None  # type: ignore


class Claim(BaseModel):
    text: str = Field(description="Single grounded claim, one sentence")
    source_ids: List[str] = Field(description="List of source ids (strings like '1','2') that support this claim")
    quote_span: str = Field(description="Exact substring from the source that supports the claim")


class GroundedAnswer(BaseModel):
    claims: List[Claim] = Field(default_factory=list, description="List of grounded claims")
    summary: Optional[str] = Field(default=None, description="Optional summary, must be grounded in claims")
