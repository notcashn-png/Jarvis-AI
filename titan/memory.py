"""Persistent, file-backed memory.

Every path here originates from model output, so `_resolve` is the security
boundary for the whole module: it canonicalizes the path and refuses anything
that lands outside the memory root. No other function in this file may touch
the filesystem without going through it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# One memory note should be small enough to read whole. Bigger than this and the
# model should be splitting it into separate files instead.
MAX_FILE_BYTES = 256 * 1024

# Bounds the blast radius of a runaway write loop.
MAX_FILES = 5000

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")

# Starting notes written by `titan init`. Deliberately sparse: TITAN fills these
# in as it learns, and an over-specified template invites it to invent facts.
_SCAFFOLD: dict[str, str] = {
    "profile.md": """# Profile

Who the principal is, their situation, constraints, and how they like to work.
TITAN maintains this — correct it directly if it gets something wrong.

## Situation
_Not yet known._

## Constraints
_Not yet known._

## Working style
_Not yet known._
""",
    "goals.md": """# Goals

Active goals with targets and status. Reviewed at the start of a session.

## Active
_None recorded yet._

## Someday
_None recorded yet._
""",
    "preferences.md": """# Preferences

How the principal wants TITAN to work. Whenever they correct TITAN's style,
format, or approach, it gets recorded here so the correction sticks.

- _None recorded yet._
""",
}


class MemoryError_(Exception):
    """Raised for any rejected memory operation. Surfaced to the model as text."""


@dataclass(frozen=True)
class SearchHit:
    path: str
    line_number: int
    line: str


class MemoryStore:
    """A sandboxed directory of markdown notes."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        # Resolve after creation so symlinked roots (e.g. /tmp on macOS) compare
        # equal to the resolved children we validate against them.
        self.root = self.root.resolve()

    # ---- security boundary -------------------------------------------------

    def _resolve(self, raw: str) -> Path:
        """Map a model-supplied path to a real path inside the root, or reject it.

        Rejects absolute paths, traversal, empty and reserved segments, and
        anything whose resolved form escapes the root (which covers symlinks
        pointing outward).
        """
        if not isinstance(raw, str) or not raw.strip():
            raise MemoryError_("path must be a non-empty string")

        cleaned = raw.strip().replace("\\", "/")
        if cleaned.startswith("/"):
            raise MemoryError_(
                f"path must be relative to the memory root, got absolute {raw!r}"
            )
        if "\x00" in cleaned:
            raise MemoryError_("path must not contain null bytes")

        segments = [s for s in cleaned.split("/") if s != ""]
        if not segments:
            raise MemoryError_("path must name a file")

        for segment in segments:
            if segment in (".", ".."):
                raise MemoryError_(f"path must not contain '{segment}' segments: {raw!r}")
            if not _SAFE_SEGMENT.match(segment):
                raise MemoryError_(
                    f"path segment {segment!r} is not allowed; use letters, digits, "
                    "'.', '_' and '-' only"
                )

        candidate = self.root.joinpath(*segments)

        # strict=False: the file need not exist yet (writes), but every existing
        # component is resolved, so an outward-pointing symlink is caught here.
        resolved = candidate.resolve(strict=False)
        if resolved != self.root and self.root not in resolved.parents:
            raise MemoryError_(f"path escapes the memory root: {raw!r}")

        return resolved

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    # ---- operations --------------------------------------------------------

    def list(self, prefix: str = "") -> list[str]:
        """Return every note path under `prefix`, sorted."""
        if prefix.strip():
            base = self._resolve(prefix)
        else:
            base = self.root

        if not base.exists():
            return []
        if base.is_file():
            return [self._relative(base)]

        out = [
            self._relative(p)
            for p in base.rglob("*")
            if p.is_file() and not p.is_symlink()
        ]
        return sorted(out)

    def read(self, path: str) -> str:
        target = self._resolve(path)
        if not target.exists():
            raise MemoryError_(f"no such memory file: {path}")
        if not target.is_file():
            raise MemoryError_(f"not a file: {path}")
        return target.read_text(encoding="utf-8", errors="replace")

    def write(self, path: str, content: str) -> str:
        """Create or overwrite a note. Returns its canonical relative path."""
        if not isinstance(content, str):
            raise MemoryError_("content must be a string")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            raise MemoryError_(
                f"content is {len(encoded)} bytes; limit is {MAX_FILE_BYTES}. "
                "Split this into several smaller notes."
            )

        target = self._resolve(path)
        if not target.exists() and self._count_files() >= MAX_FILES:
            raise MemoryError_(
                f"memory holds {MAX_FILES} files already; prune before adding more"
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-replace so a crash mid-write cannot truncate an existing note.
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
        return self._relative(target)

    def append(self, path: str, text: str) -> str:
        existing = ""
        try:
            existing = self.read(path)
        except MemoryError_:
            existing = ""
        joiner = "" if (not existing or existing.endswith("\n")) else "\n"
        return self.write(path, f"{existing}{joiner}{text}\n")

    def delete(self, path: str) -> str:
        target = self._resolve(path)
        if not target.exists():
            raise MemoryError_(f"no such memory file: {path}")
        if not target.is_file():
            raise MemoryError_(f"not a file: {path}")
        target.unlink()
        return self._relative(target)

    def search(self, query: str, limit: int = 40) -> list[SearchHit]:
        """Case-insensitive substring search across all notes."""
        if not query or not query.strip():
            raise MemoryError_("query must be a non-empty string")
        needle = query.strip().lower()

        hits: list[SearchHit] = []
        for rel in self.list():
            try:
                content = self.read(rel)
            except MemoryError_:
                continue
            for i, line in enumerate(content.splitlines(), start=1):
                if needle in line.lower():
                    hits.append(SearchHit(rel, i, line.strip()[:300]))
                    if len(hits) >= limit:
                        return hits
        return hits

    def _count_files(self) -> int:
        return sum(1 for p in self.root.rglob("*") if p.is_file())

    # ---- convenience -------------------------------------------------------

    def today_log_path(self) -> str:
        return f"log/{date.today().isoformat()}.md"

    def scaffold(self) -> list[str]:
        """Seed the starting notes. Existing files are never overwritten.

        A cold empty directory makes the first session worse: TITAN has nowhere
        to put what it learns and no shape to follow. These stubs give it one.
        Returns the paths actually created.
        """
        created = []
        for path, body in _SCAFFOLD.items():
            try:
                self.read(path)
                continue  # already there — leave the principal's content alone
            except MemoryError_:
                pass
            self.write(path, body)
            created.append(path)
        return created

    def overview(self, max_entries: int = 60) -> str:
        """A compact index of memory, injected into the first turn of a session."""
        paths = self.list()
        if not paths:
            return "Memory is empty. This is a new principal — build the profile as you learn."
        shown = paths[:max_entries]
        more = len(paths) - len(shown)
        lines = "\n".join(f"- {p}" for p in shown)
        suffix = f"\n- ...and {more} more" if more > 0 else ""
        return f"Memory contains {len(paths)} file(s):\n{lines}{suffix}"
