"""Assembly of the cached system prompt."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from .config import Config
from .memory import MemoryStore

# Lives inside the package so it ships in a wheel, not just an editable install.
_PACKAGED_SPEC = Path(__file__).resolve().parent / "TITAN.md"


def load_spec(config: Config) -> str:
    """Load the operating spec, preferring a user override in TITAN_HOME."""
    override = config.spec_path
    if override.is_file():
        return override.read_text(encoding="utf-8")
    if _PACKAGED_SPEC.is_file():
        return _PACKAGED_SPEC.read_text(encoding="utf-8")
    raise FileNotFoundError(
        f"Operating spec not found at {override} or {_PACKAGED_SPEC}. "
        "TITAN.md must be present."
    )


def build_system(config: Config, store: MemoryStore) -> list[dict[str, Any]]:
    """Build the system blocks.

    Order matters for prompt caching, which is a prefix match: the spec is frozen
    and goes first with the cache breakpoint on it, so it is reused across every
    session. The volatile block — today's date, the memory index — goes *after*
    the breakpoint, where changing it costs nothing.
    """
    spec = load_spec(config)

    volatile = (
        f"Today's date is {date.today().isoformat()}.\n"
        f"Memory root: {store.root}\n\n"
        f"{store.overview()}"
    )

    return [
        {
            "type": "text",
            "text": spec,
            # Caches the tool definitions and the spec together: tools render
            # before system, so a breakpoint here covers both.
            "cache_control": {"type": "ephemeral"},
        },
        {"type": "text", "text": volatile},
    ]
