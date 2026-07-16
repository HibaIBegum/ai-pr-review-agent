"""Core review pipeline: fan out to specialized passes, fan back in to one
merged, structured result.

Why multiple focused passes instead of one big "review this PR" prompt: a
single broad prompt tends to skim -- it mentions a couple of things from
each category and calls it done. Splitting into passes that are each told to
ignore everything except one category gets the model to actually look for
that thing, instead of pattern-matching the most obvious issue and stopping.
"""
from __future__ import annotations

import json
import os

from anthropic import Anthropic

from .schema import Issue, PassResult, ReviewResult

MODEL = "claude-sonnet-4-6"

PASS_PROMPTS: dict[str, str] = {
    "bug": (
        "You are reviewing a code diff for logic errors and bugs only. "
        "Ignore style, security, and missing tests -- other passes cover those. "
        "Look for: off-by-one errors, null/undefined handling, incorrect "
        "conditionals, race conditions, resource leaks, and incorrect API usage."
    ),
    "security": (
        "You are reviewing a code diff for security issues only. Ignore style, "
        "general bugs, and missing tests. Look for: injection (SQL, command, "
        "template), missing input validation, secrets in code, broken auth "
        "checks, and unsafe deserialization."
    ),
    "test-coverage": (
        "You are reviewing a code diff for test coverage gaps only. Ignore "
        "style, bugs, and security. Identify new logic branches, edge cases, "
        "or error paths introduced in this diff that don't appear to have a "
        "corresponding test in the diff itself."
    ),
    "style": (
        "You are reviewing a code diff for style and convention issues only. "
        "Ignore bugs, security, and tests. Flag naming inconsistencies, dead "
        "code, overly long functions, and deviations from idiomatic style for "
        "the language in question. Keep this pass light -- only flag things "
        "that would genuinely slow a human reviewer down."
    ),
}

RESPONSE_SCHEMA_NOTE = (
    "Respond with ONLY raw JSON (no markdown fences, no preamble) matching this "
    "shape:\n"
    '{"issues": [{"file": "path", "line": 12, "severity": "high|medium|low", '
    '"category": "bug|security|test-coverage|style", "message": "...", '
    '"suggestion": "..."}]}\n'
    "If you find nothing in this category, return an empty issues list. "
    "Never invent a file or line that isn't in the diff."
)


class ReviewAgent:
    def __init__(self, api_key: str | None = None):
        self.client = Anthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])

    def _call(self, prompt: str) -> str:
        response = self.client.messages.create(
            model=MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in response.content if block.type == "text")

    def _run_pass(self, category: str, diff: str) -> PassResult:
        prompt = f"{PASS_PROMPTS[category]}\n\n{RESPONSE_SCHEMA_NOTE}\n\nDiff:\n{diff}"
        text = self._call(prompt)
        cleaned = text.replace("```json", "").replace("```", "").strip()

        try:
            return PassResult(**json.loads(cleaned))
        except (json.JSONDecodeError, TypeError, ValueError):
            # Malformed JSON is the single most common failure mode when an
            # LLM is asked for structured output -- one corrective retry is
            # worth it before letting the pass fail outright.
            retry_prompt = (
                f"{prompt}\n\nYour previous response was not valid JSON matching "
                "the schema. Return ONLY the corrected JSON, nothing else."
            )
            text = self._call(retry_prompt)
            cleaned = text.replace("```json", "").replace("```", "").strip()
            return PassResult(**json.loads(cleaned))  # let this raise if it still fails

    def review(self, diff: str, passes: tuple[str, ...] = tuple(PASS_PROMPTS)) -> ReviewResult:
        all_issues: list[Issue] = []
        failed_passes: list[str] = []

        for category in passes:
            try:
                result = self._run_pass(category, diff)
                all_issues.extend(result.issues)
            except Exception:
                # A failed pass shouldn't take down the whole review -- report
                # what we have plus a note, rather than posting nothing.
                failed_passes.append(category)

        return ReviewResult(
            summary=self._summarize(all_issues, failed_passes),
            issues=all_issues,
            verdict=self._verdict_from_issues(all_issues),
        )

    @staticmethod
    def _verdict_from_issues(issues: list[Issue]) -> str:
        if any(i.severity == "high" for i in issues):
            return "request_changes"
        if issues:
            return "comment"
        return "approve"

    @staticmethod
    def _summarize(issues: list[Issue], failed_passes: list[str]) -> str:
        by_category: dict[str, int] = {}
        for issue in issues:
            by_category[issue.category] = by_category.get(issue.category, 0) + 1

        parts = [f"{count} {cat}" for cat, count in by_category.items()] or ["no issues found"]
        summary = "Review found " + ", ".join(parts) + "."
        if failed_passes:
            summary += f" (Note: {', '.join(failed_passes)} pass failed and was skipped.)"
        return summary
