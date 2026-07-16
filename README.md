# AI PR Review Agent

A GitHub Action that reviews pull requests with Claude and posts a
structured comment back on the PR — bugs, security issues, missing test
coverage, and style, each checked in its own focused pass.

Works on any repo, any language. It reads a diff and writes a comment;
nothing about it is tied to a specific domain or industry.

## Why it's built this way

**Multiple focused passes, not one big prompt.** A single "review this PR"
prompt tends to skim — a couple of things from each category, then it stops.
Splitting into passes that are each told to ignore everything except one
category (see `PASS_PROMPTS` in `src/reviewer.py`) gets the model to actually
look for that thing instead of pattern-matching the most obvious issue.

**Structured output, validated on the way back in.** Every pass is asked for
JSON matching a fixed schema (`src/schema.py`, built on pydantic). If a
response doesn't parse, there's one corrective retry before that pass is
marked failed — a failed pass doesn't take down the whole review, it's just
noted in the summary.

**An eval harness, not just a demo.** `eval/` has a handful of diffs with
known planted issues (a SQL injection, a missing null check, a function with
no test, and one clean diff to check for false positives) and a script that
checks whether the pipeline actually catches each one. Run it after touching
a prompt, before trusting the change:

```bash
export ANTHROPIC_API_KEY=sk-...
python eval/run_evals.py
```

**Small, auditable network surface.** `src/github_client.py` is plain
`requests` against two REST endpoints — no SDK abstracting away what's
actually happening over the wire.

## Architecture

```
GitHub PR event
      │
      ▼
GitHubClient.get_pr_diff()          (src/github_client.py)
      │
      ▼
ReviewAgent.review(diff)            (src/reviewer.py)
      │
      ├── bug pass ─────────┐
      ├── security pass ────┤
      ├── test-coverage ────┼──▶ merge + verdict ──▶ ReviewResult
      └── style pass ───────┘
      │
      ▼
format_comment(result)              (src/main.py)
      │
      ▼
GitHubClient.post_review_comment()
```

## Using it on your own repo

1. Add `ANTHROPIC_API_KEY` as a repo secret (Settings → Secrets and
   variables → Actions).
2. Copy `.github/workflows/example-usage.yml` into your repo (or reference
   this repo directly once it's published).
3. Open a PR. The agent comments within a minute or two of the workflow
   running.

## Local development

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-...
python eval/run_evals.py
```

There's no way to run `src/main.py` fully locally — it expects to be
invoked inside a GitHub Actions run, since it reads `GITHUB_EVENT_PATH` and
`GITHUB_TOKEN` from the environment Actions provides automatically. The eval
harness exercises the same `ReviewAgent` class against static fixtures
instead, so the core logic is testable without needing a live PR.

## What this doesn't do (on purpose, for v1)

- **Inline comments on specific lines.** Posts one top-level comment instead
  of using GitHub's inline review API, which needs exact commit SHAs and
  diff positions per line — more fragile than it's worth for a v1.
- **Parallel pass execution.** Passes run sequentially for simplicity and
  predictable rate-limit behavior. Straightforward extension: run them
  concurrently with `asyncio` or a thread pool once volume justifies it.
- **Cross-pass deduplication via a merge LLM call.** Issues are just
  concatenated; a genuinely duplicate finding from two passes will show up
  twice. A cheap fix worth doing next: dedupe by (file, line-proximity,
  category) in code before formatting the comment.
