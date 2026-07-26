"""Tool definitions handed to the model.

Tool descriptions state *when* to call the tool, not just what it does. Opus-tier
models are conservative about reaching for memory and search unless the trigger
condition is written into the description itself.
"""

from __future__ import annotations

from typing import Any

from anthropic import beta_tool

from .memory import MemoryError_, MemoryStore

# Dynamic-filtering variants: the model filters results with server-side code
# before they reach the context window. Do not additionally declare
# `code_execution` alongside these — a second execution environment confuses the
# model.
WEB_SEARCH_TOOL: dict[str, Any] = {"type": "web_search_20260209", "name": "web_search"}
WEB_FETCH_TOOL: dict[str, Any] = {"type": "web_fetch_20260209", "name": "web_fetch"}


def build_memory_tools(store: MemoryStore) -> list[Any]:
    """Build the memory toolset bound to `store`."""

    @beta_tool
    def memory_list(prefix: str = "") -> str:
        """List the paths of stored memory notes.

        Call this at the start of any non-trivial task, before asking the
        principal for context you may already have on disk.

        Args:
            prefix: Optional directory to list, e.g. "projects". Empty lists everything.
        """
        try:
            paths = store.list(prefix)
        except MemoryError_ as exc:
            return f"Error: {exc}"
        if not paths:
            return "No memory files found."
        return "\n".join(paths)

    @beta_tool
    def memory_read(path: str) -> str:
        """Read the full contents of one memory note.

        Call this whenever memory_list or memory_search shows a note that could
        bear on the current task.

        Args:
            path: Path relative to the memory root, e.g. "projects/newsletter.md".
        """
        try:
            return store.read(path)
        except MemoryError_ as exc:
            return f"Error: {exc}"

    @beta_tool
    def memory_write(path: str, content: str) -> str:
        """Create or overwrite a memory note with the full new contents.

        Call this when you learn something durable about the principal, their
        goals, projects, preferences, or decisions. Prefer updating an existing
        note over creating a near-duplicate. Never store credentials or secrets.

        Args:
            path: Path relative to the memory root, e.g. "profile.md".
            content: The complete new contents. This replaces the file.
        """
        try:
            written = store.write(path, content)
        except MemoryError_ as exc:
            return f"Error: {exc}"
        return f"Wrote {written}."

    @beta_tool
    def memory_append(path: str, text: str) -> str:
        """Append a line or block to the end of a memory note, creating it if absent.

        Use this for running records — the daily log, a project's decision
        history — where the existing contents should be preserved.

        Args:
            path: Path relative to the memory root, e.g. "log/2026-07-26.md".
            text: The text to append. A trailing newline is added for you.
        """
        try:
            written = store.append(path, text)
        except MemoryError_ as exc:
            return f"Error: {exc}"
        return f"Appended to {written}."

    @beta_tool
    def memory_search(query: str) -> str:
        """Search all memory notes for a substring, case-insensitively.

        Use this when you need context but do not know which note holds it —
        it is cheaper than reading many files.

        Args:
            query: The text to look for.
        """
        try:
            hits = store.search(query)
        except MemoryError_ as exc:
            return f"Error: {exc}"
        if not hits:
            return f"No matches for {query!r}."
        return "\n".join(f"{h.path}:{h.line_number}: {h.line}" for h in hits)

    @beta_tool
    def memory_delete(path: str) -> str:
        """Delete a memory note that has turned out to be wrong or obsolete.

        Args:
            path: Path relative to the memory root.
        """
        try:
            removed = store.delete(path)
        except MemoryError_ as exc:
            return f"Error: {exc}"
        return f"Deleted {removed}."

    return [
        memory_list,
        memory_read,
        memory_write,
        memory_append,
        memory_search,
        memory_delete,
    ]


def build_tools(store: MemoryStore, web_enabled: bool = True) -> list[Any]:
    """The full toolset: memory plus, optionally, the server-side web tools."""
    tools = build_memory_tools(store)
    if web_enabled:
        tools.extend([WEB_SEARCH_TOOL, WEB_FETCH_TOOL])
    return tools
