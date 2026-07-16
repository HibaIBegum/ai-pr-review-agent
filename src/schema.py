"""Structured schemas for review issues and pass results.

Using pydantic buys us two things: (1) a shape we can describe to Claude in
the prompt so it knows exactly what's expected back, and (2) validation on
the way in -- if a response doesn't match, we know immediately instead of
silently posting garbage to a real pull request.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Severity = Literal["high", "medium", "low"]
Category = Literal["bug", "security", "test-coverage", "style"]
Verdict = Literal["approve", "comment", "request_changes"]


class Issue(BaseModel):
    file: str
    line: Optional[int] = None
    severity: Severity
    category: Category
    message: str
    suggestion: Optional[str] = None


class PassResult(BaseModel):
    """Output of a single, focused review pass (one category)."""

    issues: list[Issue] = Field(default_factory=list)


class ReviewResult(BaseModel):
    """Final, merged review ready to post as a PR comment."""

    summary: str
    issues: list[Issue]
    verdict: Verdict
