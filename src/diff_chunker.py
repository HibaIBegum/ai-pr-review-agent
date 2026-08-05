# src/diff_chunker.py
"""Splits a unified diff into review-sized chunks along hunk boundaries.

A unified diff is a sequence of per-file sections, each starting with
`diff --git a/... b/...`, followed by a few header lines, followed by one
or more hunks (`@@ -start,count +start,count @@`). We parse that structure,
then greedily pack whole hunks into chunks up to a size budget -- never
splitting a hunk itself, since a partial hunk is missing the context lines
a reviewer needs to understand the change.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Hunk:
    header: str
    body: list[str] = field(default_factory=list)

    def text(self) -> str:
        return self.header + "\n" + "\n".join(self.body)


@dataclass
class FileDiff:
    file_header: list[str] = field(default_factory=list)
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def path(self) -> str:
        for line in self.file_header:
            if line.startswith("+++ ") and line[4:].strip() != "/dev/null":
                return line[4:].strip().removeprefix("b/")
        for line in self.file_header:
            if line.startswith("--- ") and line[4:].strip() != "/dev/null":
                return line[4:].strip().removeprefix("a/")
        return "unknown"


def parse_diff(diff_text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    current_file: FileDiff | None = None
    current_hunk: Hunk | None = None

    def flush_hunk():
        if current_hunk is not None and current_file is not None:
            current_file.hunks.append(current_hunk)

    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            flush_hunk()
            if current_file:
                files.append(current_file)
            current_file = FileDiff(file_header=[line])
            current_hunk = None
        elif current_file is not None and line.startswith(("index ", "--- ", "+++ ")):
            current_file.file_header.append(line)
        elif line.startswith("@@"):
            flush_hunk()
            current_hunk = Hunk(header=line)
        elif current_hunk is not None:
            current_hunk.body.append(line)
        # lines before a file's first hunk (e.g. "Binary files differ") are
        # dropped in v1 -- known limitation, same spirit as your other v1 cuts

    flush_hunk()
    if current_file:
        files.append(current_file)
    return files


def pack_chunks(files: list[FileDiff], budget_chars: int = 8000) -> list[str]:
    chunks: list[str] = []
    parts: list[str] = []
    size = 0
    active_path: str | None = None

    def flush():
        nonlocal parts, size, active_path
        if parts:
            chunks.append("\n".join(parts))
        parts, size, active_path = [], 0, None

    for f in files:
        header_text = "\n".join(f.file_header)
        for hunk in f.hunks:
            hunk_text = hunk.text()
            needs_header = active_path != f.path
            piece_size = len(hunk_text) + (len(header_text) if needs_header else 0)

            if parts and size + piece_size > budget_chars:
                flush()
                needs_header = True  # new chunk always needs its own header

            if needs_header:
                parts.append(header_text)
                size += len(header_text)
                active_path = f.path

            parts.append(hunk_text)
            size += len(hunk_text)

    flush()
    return chunks