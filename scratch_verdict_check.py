# scratch_verdict_check.py
from src.schema import ReviewResult, Issue
from src.main import format_comment
from src.diff_chunker import parse_diff, pack_chunks

# Case 1: a pass failed, nothing else found
incomplete = ReviewResult(
    summary="Review found no issues in the passes that ran.",
    issues=[],
    verdict="incomplete",
    failed_passes=["security"],
)
print(format_comment(incomplete))
print("=" * 70)

# Case 2: multiple failed passes -- check the pluralization
incomplete_multi = ReviewResult(
    summary="Review found no issues in the passes that ran.",
    issues=[],
    verdict="incomplete",
    failed_passes=["security", "bug"],
)
print(format_comment(incomplete_multi))
print("=" * 70)

# Case 3: genuinely clean -- confirm the banner does NOT appear here
clean = ReviewResult(
    summary="Review found no issues.",
    issues=[],
    verdict="approve",
    failed_passes=[],
)
print(format_comment(clean))


with open("some_large.diff") as f:
    diff_text = f.read()

files = parse_diff(diff_text)
print(f"Parsed {len(files)} file(s), {sum(len(f.hunks) for f in files)} hunk(s) total")

chunks = pack_chunks(files, budget_chars=8000)
print(f"Packed into {len(chunks)} chunk(s)")
for i, c in enumerate(chunks):
    print(f"  chunk {i}: {len(c)} chars")