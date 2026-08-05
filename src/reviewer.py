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

from groq import BadRequestError, Groq
from .schema import Issue, PassResult, ReviewResult
from .diff_chunker import parse_diff, pack_chunks

# Diffs at or under this size skip chunking entirely -- same behavior as
# today, zero extra API calls for the common case.
CHUNK_THRESHOLD_CHARS = 8000
CHUNK_BUDGET_CHARS = 6000

# GPT-OSS 120B: Groq's flagship open-weight model, chosen over the Llama
# options for its reasoning capability on multi-category structured output --
# and it's cheaper per token than llama-3.3-70b-versatile at time of writing.
# Swap this string if Groq deprecates it -- check console.groq.com/docs/models
# first, since availability here changes faster than most APIs.
MODEL = "openai/gpt-oss-120b"

# Bumped from 1500: chunked diffs can contain several files' worth of change,
# and categories that tend to find many small issues (bug, test-coverage,
# style) were getting truncated mid-JSON on larger chunks, which Groq's
# JSON-mode validator rejects outright as a BadRequestError before the
# content ever reaches our own retry logic.
MAX_COMPLETION_TOKENS = 3000

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
        self.client = Groq(api_key=api_key or os.environ["GROQ_API_KEY"])

    def _call(self, prompt: str) -> str:
        # response_format={"type": "json_object"} turns on Groq's JSON mode --
        # every pass prompt already includes the word "JSON", which Groq
        # requires when this mode is on. This isn't a guarantee the schema is
        # followed exactly (that needs strict json_schema mode, which not all
        # Groq models support yet), but it substantially cuts down on stray
        # prose wrapping the JSON, on top of the fence-stripping below.
        response = self.client.chat.completions.create(
            model=MODEL,
            max_completion_tokens=MAX_COMPLETION_TOKENS,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content

    def _run_pass(self, category: str, diff: str) -> PassResult:
        prompt = f"{PASS_PROMPTS[category]}\n\n{RESPONSE_SCHEMA_NOTE}\n\nDiff:\n{diff}"

        try:
            text = self._call(prompt)
            cleaned = text.replace("```json", "").replace("```", "").strip()
            return PassResult(**json.loads(cleaned))
        except (json.JSONDecodeError, TypeError, ValueError, BadRequestError):
            # Two distinct failure modes land here, treated the same way: the
            # model returned prose instead of JSON (JSONDecodeError/ValueError),
            # or Groq's own JSON-mode validator rejected a truncated response
            # before it even got back to us (BadRequestError). The whole first
            # _call() is now inside this try -- previously BadRequestError
            # happened before any parsing step and skipped retry entirely.
            retry_prompt = (
                f"{prompt}\n\nYour previous response was cut off or was not "
                "valid JSON. Return ONLY valid JSON matching the schema. If "
                "there are many issues, include only the 5 most important to "
                "keep the response short."
            )
            text = self._call(retry_prompt)
            cleaned = text.replace("```json", "").replace("```", "").strip()
            return PassResult(**json.loads(cleaned))  # let this raise if it still fails

    def review(self, diff: str, passes: tuple[str, ...] = tuple(PASS_PROMPTS)) -> ReviewResult:
        if len(diff) <= CHUNK_THRESHOLD_CHARS:
            return self._review_chunk(diff, passes)

        files = parse_diff(diff)
        chunks = pack_chunks(files, budget_chars=CHUNK_BUDGET_CHARS)

        all_issues: list[Issue] = []
        failed_categories: set[str] = set()

        for chunk in chunks:
            result = self._review_chunk(chunk, passes)
            all_issues.extend(result.issues)
            failed_categories.update(result.failed_passes)

        failed_passes = sorted(failed_categories)
        return ReviewResult(
            summary=self._summarize(all_issues, failed_passes),
            issues=all_issues,
            verdict=self._verdict_from_issues(all_issues, failed_passes),
            failed_passes=failed_passes,
        )

    def _review_chunk(self, diff: str, passes: tuple[str, ...]) -> ReviewResult:
        """Runs all passes against one diff (or diff chunk) and merges them."""
        all_issues: list[Issue] = []
        failed_passes: list[str] = []

        for category in passes:
            try:
                result = self._run_pass(category, diff)
                all_issues.extend(result.issues)
            except Exception as exc:
                failed_passes.append(category)
                print(f"[review] '{category}' pass failed: {type(exc).__name__}")

        return ReviewResult(
            summary=self._summarize(all_issues, failed_passes),
            issues=all_issues,
            verdict=self._verdict_from_issues(all_issues, failed_passes),
            failed_passes=failed_passes,
        )

    @staticmethod
    def _verdict_from_issues(issues: list[Issue], failed_passes: list[str]) -> str:
        if failed_passes:
            return "incomplete"
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