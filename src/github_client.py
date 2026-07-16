"""Thin wrapper around the GitHub REST API for the two things this agent
needs: pulling a PR's diff and posting a comment back onto it.

Deliberately built on plain `requests` rather than PyGithub, so the whole
agent has a small, auditable network surface -- useful to point to directly
if an interviewer asks "what does this actually call over the wire."
"""
from __future__ import annotations

import requests

GITHUB_API = "https://api.github.com"


class GitHubClient:
    def __init__(self, token: str, repo: str):
        """`repo` is "owner/name", matching GITHUB_REPOSITORY in Actions."""
        self.repo = repo
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    def get_pr_diff(self, pr_number: int) -> str:
        """Returns the unified diff for a PR as plain text."""
        url = f"{GITHUB_API}/repos/{self.repo}/pulls/{pr_number}"
        resp = self.session.get(url, headers={"Accept": "application/vnd.github.diff"})
        resp.raise_for_status()
        return resp.text

    def post_review_comment(self, pr_number: int, body: str) -> None:
        """Posts a single top-level comment on the PR.

        Uses the issue-comment endpoint rather than the inline-review-comment
        endpoint on purpose -- inline comments require exact commit SHAs and
        diff positions per line, which is a lot of fragile bookkeeping for a
        v1. One clear, well-formatted comment is more reliable and just as
        useful to a human reviewer.
        """
        url = f"{GITHUB_API}/repos/{self.repo}/issues/{pr_number}/comments"
        resp = self.session.post(url, json={"body": body})
        resp.raise_for_status()
