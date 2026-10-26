from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class Answer(BaseModel):
    """Structured answer. Every number in `summary` must come from a tool result; if the tools cannot support an answer, abstain."""
    summary: str = Field(description="Plain-English answer using only figures returned by tools")
    key_facts: dict[str, str | float | bool | list[str]] = Field(default_factory=dict, description="Machine-readable facts copied from tool results")
    root_cause_established: Optional[bool] = Field(default=None, description="True only if a tool result identifies the cause; False if the cause is unexplained")
    abstain: bool = Field(default=False, description="True when no tool result supports an answer")
    abstain_reason: Optional[str] = None
