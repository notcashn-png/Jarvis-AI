"""Agent loop tests against a fake client — no network, no API key."""

from __future__ import annotations

from types import SimpleNamespace

import anthropic
import httpx
import pytest

from titan.agent import Agent, PauseLimitExceeded, Usage
from titan.config import Config
from titan.memory import MemoryStore


# ---- fakes -----------------------------------------------------------------


def block(kind: str, **kwargs):
    return SimpleNamespace(type=kind, **kwargs)


def message(
    *,
    text: str | None = None,
    stop_reason: str = "end_turn",
    tool_uses: list[str] | None = None,
    thinking: str | None = None,
    stop_details=None,
    usage=None,
):
    content = []
    if thinking is not None:
        content.append(block("thinking", thinking=thinking))
    if text is not None:
        content.append(block("text", text=text))
    for name in tool_uses or []:
        content.append(block("tool_use", name=name, id=f"toolu_{name}"))
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        stop_details=stop_details,
        usage=usage
        or SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=100,
            cache_creation_input_tokens=0,
        ),
    )


class FakeRunner:
    def __init__(self, messages, tool_response=None):
        self._messages = messages
        self._tool_response = tool_response
        self._index = 0

    def __iter__(self):
        for m in self._messages:
            self._index += 1
            yield m

    def generate_tool_call_response(self):
        # Emit a tool_result turn only after a message that requested tools.
        current = self._messages[self._index - 1]
        if any(b.type == "tool_use" for b in current.content):
            return {"role": "user", "content": [{"type": "tool_result",
                                                 "tool_use_id": "toolu_x",
                                                 "content": "ok"}]}
        return self._tool_response


class FakeMessages:
    def __init__(self, runners, error=None):
        self._runners = list(runners)
        self._error = error
        self.calls: list[dict] = []

    def tool_runner(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            err, self._error = self._error, None
            raise err
        if not self._runners:
            raise AssertionError("tool_runner called more times than expected")
        return self._runners.pop(0)


class FakeClient:
    def __init__(self, runners, error=None):
        self.beta = SimpleNamespace(messages=FakeMessages(runners, error))

    @property
    def calls(self):
        return self.beta.messages.calls


def bad_request(msg: str) -> anthropic.BadRequestError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(400, request=request, json={
        "type": "error", "error": {"type": "invalid_request_error", "message": msg}
    })
    return anthropic.BadRequestError(msg, response=response, body=None)


@pytest.fixture
def config(tmp_path):
    return Config(home=tmp_path)


def make_agent(config, runners, error=None, events=None):
    store = MemoryStore(config.memory_root)
    client = FakeClient(runners, error)
    on_event = (lambda k, p: events.append((k, p))) if events is not None else None
    return Agent(config=config, client=client, store=store, on_event=on_event)


# ---- basics ----------------------------------------------------------------


def test_simple_turn_returns_text(config):
    agent = make_agent(config, [FakeRunner([message(text="Hello.")])])
    turn = agent.send("hi")
    assert turn.text == "Hello."
    assert turn.stop_reason == "end_turn"
    assert not turn.refused


def test_empty_input_rejected(config):
    agent = make_agent(config, [])
    with pytest.raises(ValueError):
        agent.send("   ")


def test_history_accumulates_across_turns(config):
    agent = make_agent(
        config,
        [FakeRunner([message(text="one")]), FakeRunner([message(text="two")])],
    )
    agent.send("first")
    agent.send("second")

    roles = [m["role"] for m in agent.messages]
    assert roles == ["user", "assistant", "user", "assistant"]
    # The second request must carry the first exchange.
    assert len(agent.client.calls[1]["messages"]) == 3


def test_reset_clears_history_only(config):
    agent = make_agent(config, [FakeRunner([message(text="hi")])])
    agent.send("hi")
    agent.store.write("profile.md", "durable")
    agent.reset()
    assert agent.messages == []
    assert agent.store.read("profile.md") == "durable"


def test_tool_results_are_mirrored_into_history(config):
    agent = make_agent(
        config,
        [FakeRunner([message(tool_uses=["memory_read"]), message(text="done")])],
    )
    turn = agent.send("what do you know about me")
    assert turn.tool_calls == ["memory_read"]
    roles = [m["role"] for m in agent.messages]
    assert roles == ["user", "assistant", "user", "assistant"]


# ---- request shape ---------------------------------------------------------


def test_request_carries_adaptive_thinking_and_effort(config):
    agent = make_agent(config, [FakeRunner([message(text="x")])])
    agent.send("hi")
    params = agent.client.calls[0]
    assert params["thinking"]["type"] == "adaptive"
    assert params["thinking"]["display"] == "omitted"
    assert params["output_config"] == {"effort": "high"}
    assert params["model"] == "claude-opus-5"


def test_show_thinking_switches_display(tmp_path):
    config = Config(home=tmp_path, show_thinking=True)
    agent = make_agent(config, [FakeRunner([message(text="x")])])
    agent.send("hi")
    assert agent.client.calls[0]["thinking"]["display"] == "summarized"


def test_fallbacks_are_requested_by_default(config):
    agent = make_agent(config, [FakeRunner([message(text="x")])])
    agent.send("hi")
    params = agent.client.calls[0]
    assert params["fallbacks"] == "default"
    assert params["betas"] == ["server-side-fallback-2026-07-01"]


def test_fallbacks_can_be_disabled(tmp_path):
    config = Config(home=tmp_path, fallbacks_enabled=False)
    agent = make_agent(config, [FakeRunner([message(text="x")])])
    agent.send("hi")
    assert "fallbacks" not in agent.client.calls[0]


def test_system_prompt_is_cached_and_frozen_across_turns(config):
    agent = make_agent(
        config,
        [FakeRunner([message(text="a")]), FakeRunner([message(text="b")])],
    )
    agent.send("one")
    agent.send("two")
    first, second = agent.client.calls
    # A byte change in the cached prefix would void every cache hit.
    assert first["system"][0]["text"] == second["system"][0]["text"]
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}


# ---- pause_turn ------------------------------------------------------------


def test_pause_turn_restarts_the_runner(config):
    agent = make_agent(
        config,
        [
            FakeRunner([message(text="partial", stop_reason="pause_turn")]),
            FakeRunner([message(text="finished")]),
        ],
    )
    turn = agent.send("research something")
    assert turn.stop_reason == "end_turn"
    assert turn.text == "partial\nfinished"
    assert len(agent.client.calls) == 2
    # The restart must resume from the paused assistant turn.
    assert agent.client.calls[1]["messages"][-1]["role"] == "assistant"


def test_pause_restart_budget_is_enforced(tmp_path):
    config = Config(home=tmp_path, max_restarts=2)
    runners = [
        FakeRunner([message(text=f"p{i}", stop_reason="pause_turn")]) for i in range(4)
    ]
    agent = make_agent(config, runners)
    with pytest.raises(PauseLimitExceeded):
        agent.send("loop forever")


# ---- refusal ---------------------------------------------------------------


def test_refusal_is_reported_and_dropped_from_history(config):
    refusal = message(
        text=None,
        stop_reason="refusal",
        stop_details=SimpleNamespace(category="cyber", explanation="declined"),
    )
    agent = make_agent(config, [FakeRunner([refusal])])
    turn = agent.send("something disallowed")

    assert turn.refused
    assert turn.refusal_category == "cyber"
    # A refused exchange must not anchor the next turn.
    assert agent.messages == []


def test_refusal_without_stop_details_is_safe(config):
    refusal = message(text=None, stop_reason="refusal", stop_details=None)
    agent = make_agent(config, [FakeRunner([refusal])])
    turn = agent.send("x")
    assert turn.refused
    assert turn.refusal_category is None


# ---- fallback degradation --------------------------------------------------


def test_unsupported_fallback_beta_degrades_and_retries(config):
    agent = make_agent(
        config,
        [FakeRunner([message(text="recovered")])],
        error=bad_request("unsupported beta: server-side-fallback-2026-07-01"),
    )
    turn = agent.send("hi")

    assert turn.text == "recovered"
    assert "fallbacks" not in agent.client.calls[1]
    # The retry must not leave a duplicated user turn behind.
    assert [m["role"] for m in agent.messages] == ["user", "assistant"]


def test_unrelated_bad_request_propagates(config):
    agent = make_agent(config, [], error=bad_request("max_tokens is too large"))
    with pytest.raises(anthropic.BadRequestError):
        agent.send("hi")


# ---- events and usage ------------------------------------------------------


def test_events_are_emitted(config):
    events: list[tuple[str, str]] = []
    agent = make_agent(
        config,
        [FakeRunner([message(text="answer", thinking="pondering",
                             tool_uses=["memory_list"]),
                     message(text="done")])],
        events=events,
    )
    agent.send("hi")
    kinds = [k for k, _ in events]
    assert "thinking" in kinds and "text" in kinds and "tool" in kinds


def test_usage_accumulates(config):
    agent = make_agent(
        config,
        [FakeRunner([message(text="a"), message(text="b")])],
    )
    agent.send("hi")
    assert agent.usage.input_tokens == 20
    assert agent.usage.output_tokens == 10
    assert agent.usage.cache_read_tokens == 200


def test_usage_summary_is_readable():
    usage = Usage(input_tokens=1234, output_tokens=56)
    assert "1,234" in usage.summary()
