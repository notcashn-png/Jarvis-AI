"""TITAN — an autonomous executive assistant built on the Claude API."""

from .config import Config
from .memory import MemoryError_, MemoryStore, SearchHit

__version__ = "0.1.0"

__all__ = ["Config", "MemoryStore", "MemoryError_", "SearchHit", "__version__"]


def __getattr__(name: str):
    # Agent pulls in the SDK; keep it out of the import path for memory-only use.
    if name in ("Agent", "Turn", "Usage"):
        from . import agent

        return getattr(agent, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
