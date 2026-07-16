"""Small eval harness: run the review pipeline against fixture diffs with
known planted issues, and check whether it actually catches them.

This is deliberately small -- four fixtures, one check each -- because the
point isn't statistical rigor. It's demonstrating the pattern: don't ship a
prompt change without a repeatable way to check it didn't regress. Run this
after touching PASS_PROMPTS in reviewer.py, before trusting the change.

Usage:
    export ANTHROPIC_API_KEY=sk-...
    python eval/run_evals.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.reviewer import ReviewAgent  # noqa: E402

FIXTURES_DIR = Path(__file__).parent / "fixtures"
EXPECTED_PATH = Path(__file__).parent / "expected.json"


def run() -> None:
    expected = json.loads(EXPECTED_PATH.read_text())
    agent = ReviewAgent()

    passed = 0
    total = len(expected)

    for fixture_name, spec in expected.items():
        diff = (FIXTURES_DIR / fixture_name).read_text()
        result = agent.review(diff)

        expect_category = spec["expect_category"]
        expect_file = spec["expect_file"]

        if expect_category is None:
            ok = len(result.issues) == 0
            detail = f"expected no issues, got {len(result.issues)}"
        else:
            matches = [
                i for i in result.issues
                if i.category == expect_category and i.file == expect_file
            ]
            ok = len(matches) > 0
            detail = f"expected a '{expect_category}' issue in {expect_file}"

        print(f"[{'PASS' if ok else 'FAIL'}] {fixture_name} -- {detail}")
        passed += ok

    print(f"\n{passed}/{total} fixtures passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    run()
