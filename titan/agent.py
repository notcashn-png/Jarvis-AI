"""The TITAN agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import anthropic

from .config import FALLBACK_BETA, Config
from .memory import MemoryStore
from .prompt import build_system
from .tools import build_tools


@dataclass
class Usage:
    """Running token totals for a session."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, usage: Any) -> None:
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0

    def summary(self) -> str:
        return (
            f"in {self.input_tokens:,} | out {self.output_tokens:,} | "
            f"cache read {self.cache_read_tokens:,} | cache write {self.cache_write_tokens:,}"
        )


@dataclass
class Turn:
    """The outcome of one exchange."""

    text: str
    stop_reason: str | None = None
    refusal_category: str | None = None
    tool_calls: list[str] = field(default_factory=list)

    @property
    def refused(self) -> bool:
        return self.stop_reason == "refusal"


class PauseLimitExceeded(RuntimeError):
    """A server-tool turn stayed paused past the restart budget."""


def _text_of(content: Iterable[Any]) -> str:
    parts = [
        block.text
        for block in content
        if getattr(block, "type", None) == "text" and getattr(block, "text", "")
    ]
    return "\n".join(parts).strip()


def _thinking_of(content: Iterable[Any]) -> str:
    parts = [
        block.thinking
        for block in content
        if getattr(block, "type", None) == "thinking" and getattr(block, "thinking", "")
    ]
    return "\n".join(parts).strip()


def _tool_names(content: Iterable[Any]) -> list[str]:
    names = []
    for block in content:
        kind = getattr(block, "type", None)
        if kind in ("tool_use", "server_tool_use", "mcp_tool_use"):
            name = getattr(block, "name", None)
            if name:
                names.append(name)
    return names


class Agent:
    """Stateful conversation with TITAN.

    Holds the message history across turns so memory of the current session is
    the model's own context; anything that must outlive the session goes to the
    memory store via tools.
    """

    def __init__(
        self,
        config: Config | None = None,
        client: anthropic.Anthropic | None = None,
        store: MemoryStore | None = None,
        on_event: Callable[[str, str], None] | None = None,
    ) -> None:
        self.config = config or Config.from_env()
        self.store = store or MemoryStore(self.config.memory_root)
        self.client = client or anthropic.Anthropic()
        self.messages: list[dict[str, Any]] = []
        self.usage = Usage()
        self._fallbacks_enabled = self.config.fallbacks_enabled
        # on_event(kind, payload) where kind is "text", "thinking", or "tool".
        self._on_event = on_event or (lambda kind, payload: None)

        self._system = build_system(self.config, self.store)
        self._tools = build_tools(self.store, self.config.web_enabled)

    # ---- request assembly --------------------------------------------------

    def _request_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "system": self._system,
            "tools": self._tools,
            # Adaptive is Opus 5's default, but state it so behavior does not
            # shift if TITAN_MODEL points at a model where omitting means off.
            "thinking": {
                "type": "adaptive",
                "display": "summarized" if self.config.show_thinking else "omitted",
            },
            "output_config": {"effort": self.config.effort},
            "max_iterations": self.config.max_iterations,
        }
        if self._fallbacks_enabled:
            # On a policy refusal the API re-runs on a recommended model in the
            # same call, routed by refusal category.
            params["fallbacks"] = "default"
            params["betas"] = [FALLBACK_BETA]
        return params

    # ---- the loop ----------------------------------------------------------

    def send(self, user_input: str) -> Turn:
        """Send one user message and run the tool loop until the model is done."""
        if not user_input or not user_input.strip():
            raise ValueError("user_input must be a non-empty string")

        checkpoint = len(self.messages)
        self.messages.append({"role": "user", "content": user_input})

        try:
            return self._run(checkpoint)
        except anthropic.BadRequestError as exc:
            # Most likely cause: the server-side fallback beta is not enabled for
            # this account. Drop it once and retry the turn from the checkpoint
            # so the mirrored history has no partial assistant turns in it.
            if not self._fallbacks_enabled or not _is_fallback_error(exc):
                raise
            self._fallbacks_enabled = False
            self._on_event(
                "notice",
                "Server-side fallbacks unavailable on this account; continuing without them.",
            )
            del self.messages[checkpoint:]
            self.messages.append({"role": "user", "content": user_input})
            return self._run(checkpoint)

    def _run(self, checkpoint: int) -> Turn:
        params = self._request_params()
        restarts = 0
        collected: list[str] = []
        tool_calls: list[str] = []
        last: Any = None

        while True:
            # Snapshot the history: we append to `self.messages` while iterating,
            # and the runner must not observe those mutations mid-flight.
            runner = self.client.beta.messages.tool_runner(
                messages=list(self.messages), **params
            )

            for message in runner:
                last = message
                self.usage.add(message.usage)

                thinking = _thinking_of(message.content)
                if thinking:
                    self._on_event("thinking", thinking)

                text = _text_of(message.content)
                if text:
                    collected.append(text)
                    self._on_event("text", text)

                for name in _tool_names(message.content):
                    tool_calls.append(name)
                    self._on_event("tool", name)

                # Mirror the history: the runner keeps its own copy and does not
                # expose it, and we need ours to restart on pause_turn and to
                # carry the conversation into the next turn.
                self.messages.append(
                    {"role": "assistant", "content": message.content}
                )
                tool_response = runner.generate_tool_call_response()
                if tool_response is not None:
                    self.messages.append(tool_response)

            if last is None or last.stop_reason != "pause_turn":
                break

            # A server tool hit the per-turn iteration limit. The runner exits
            # rather than resuming, so restart it — `self.messages` already ends
            # with the paused assistant turn, which is what resumes it.
            restarts += 1
            if restarts > self.config.max_restarts:
                raise PauseLimitExceeded(
                    f"turn still paused after {self.config.max_restarts} restarts"
                )

        stop_reason = getattr(last, "stop_reason", None)
        category = None
        if stop_reason == "refusal":
            details = getattr(last, "stop_details", None)
            category = getattr(details, "category", None)
            # Leave the refused exchange out of the history so the next turn is
            # not anchored to it.
            del self.messages[checkpoint:]

        return Turn(
            text="\n".join(collected).strip(),
            stop_reason=stop_reason,
            refusal_category=category,
            tool_calls=tool_calls,
        )

    def reset(self) -> None:
        """Clear the in-session history. Persistent memory is untouched."""
        self.messages.clear()


def _is_fallback_error(exc: anthropic.BadRequestError) -> bool:
    blob = str(getattr(exc, "message", "") or exc).lower()
    return "fallback" in blob or "beta" in blob
