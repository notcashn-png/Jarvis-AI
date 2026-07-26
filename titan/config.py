"""Runtime configuration for TITAN, resolved from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Opus 5 is the default: thinking is on by default, the full effort ladder is
# available, and the prompt-cache minimum is 512 tokens (half of Opus 4.8's).
DEFAULT_MODEL = "claude-opus-5"

# Non-streaming requests risk SDK HTTP timeouts much above this. The agent runs
# non-streaming so the tool loop stays simple; raise this only alongside a move
# to streaming.
DEFAULT_MAX_TOKENS = 16000

# "high" is the documented default and the right balance for advisory work.
# "xhigh" is worth it for long agentic coding sessions; "low"/"medium" are
# unusually strong on Opus 5 and are the primary latency and cost lever.
DEFAULT_EFFORT = "high"

VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max")

# Server-side fallback: on a policy refusal the API re-runs the request on a
# recommended model in the same call, routed by refusal category.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return value


@dataclass(frozen=True)
class Config:
    """Everything the agent needs to know that isn't in the prompt."""

    model: str = DEFAULT_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    effort: str = DEFAULT_EFFORT
    home: Path = field(default_factory=lambda: Path.home() / ".titan")
    web_enabled: bool = True
    show_thinking: bool = False
    fallbacks_enabled: bool = True
    max_iterations: int = 40
    max_restarts: int = 5

    @property
    def memory_root(self) -> Path:
        return self.home / "memory"

    @property
    def spec_path(self) -> Path:
        """Where a user-supplied override of TITAN.md would live."""
        return self.home / "TITAN.md"

    @classmethod
    def from_env(cls) -> "Config":
        # Path("") is PosixPath("."), whose str() is truthy — so test the raw
        # string, not the Path, or an unset TITAN_HOME silently resolves memory
        # to the current working directory.
        raw_home = os.environ.get("TITAN_HOME", "").strip()
        home = Path(raw_home).expanduser() if raw_home else Path.home() / ".titan"

        effort = os.environ.get("TITAN_EFFORT", DEFAULT_EFFORT).strip().lower()
        if effort not in VALID_EFFORTS:
            raise ValueError(
                f"TITAN_EFFORT must be one of {', '.join(VALID_EFFORTS)}, got {effort!r}"
            )

        return cls(
            model=os.environ.get("TITAN_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            max_tokens=_env_int("TITAN_MAX_TOKENS", DEFAULT_MAX_TOKENS),
            effort=effort,
            home=home,
            web_enabled=_env_flag("TITAN_WEB", True),
            show_thinking=_env_flag("TITAN_SHOW_THINKING", False),
            fallbacks_enabled=_env_flag("TITAN_FALLBACKS", True),
            max_iterations=_env_int("TITAN_MAX_ITERATIONS", 40),
            max_restarts=_env_int("TITAN_MAX_RESTARTS", 5),
        )
