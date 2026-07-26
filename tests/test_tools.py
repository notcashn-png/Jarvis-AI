import json

import pytest

from titan.memory import MemoryStore
from titan.tools import WEB_FETCH_TOOL, WEB_SEARCH_TOOL, build_memory_tools, build_tools


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "memory")


@pytest.fixture
def tools(store):
    return {t.name: t for t in build_memory_tools(store)}


def call(tool, **kwargs):
    """Invoke a BetaFunctionTool the way the runner does."""
    return tool.call(json.loads(json.dumps(kwargs)))


def test_all_memory_tools_present(tools):
    assert set(tools) == {
        "memory_list",
        "memory_read",
        "memory_write",
        "memory_append",
        "memory_search",
        "memory_delete",
    }


def test_every_tool_has_a_description_and_schema(tools):
    for name, tool in tools.items():
        assert tool.description, f"{name} has no description"
        schema = tool.input_schema
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False


def test_write_then_read(tools):
    assert "Wrote profile.md" in call(tools["memory_write"], path="profile.md",
                                     content="hello")
    assert call(tools["memory_read"], path="profile.md") == "hello"


def test_list_reflects_writes(tools):
    call(tools["memory_write"], path="a.md", content="1")
    call(tools["memory_write"], path="projects/b.md", content="2")
    assert call(tools["memory_list"]) == "a.md\nprojects/b.md"


def test_list_empty_message(tools):
    assert "No memory files" in call(tools["memory_list"])


def test_append(tools):
    call(tools["memory_append"], path="log/x.md", text="one")
    call(tools["memory_append"], path="log/x.md", text="two")
    assert call(tools["memory_read"], path="log/x.md") == "one\ntwo\n"


def test_search(tools):
    call(tools["memory_write"], path="profile.md", content="runs a newsletter")
    out = call(tools["memory_search"], query="NEWSLETTER")
    assert "profile.md:1:" in out


def test_search_no_match(tools):
    assert "No matches" in call(tools["memory_search"], query="absent")


def test_delete(tools):
    call(tools["memory_write"], path="x.md", content="1")
    assert "Deleted x.md" in call(tools["memory_delete"], path="x.md")
    assert "No memory files" in call(tools["memory_list"])


# Errors must come back as readable strings, never exceptions — an exception
# would break the runner loop instead of letting the model correct itself.
@pytest.mark.parametrize(
    "name,kwargs",
    [
        ("memory_read", {"path": "../escape.md"}),
        ("memory_read", {"path": "missing.md"}),
        ("memory_write", {"path": "/abs.md", "content": "x"}),
        ("memory_append", {"path": "../x.md", "text": "x"}),
        ("memory_delete", {"path": "../x.md"}),
        ("memory_delete", {"path": "missing.md"}),
        ("memory_search", {"query": "  "}),
        ("memory_list", {"prefix": "../"}),
    ],
)
def test_errors_are_returned_as_text(tools, name, kwargs):
    result = call(tools[name], **kwargs)
    assert isinstance(result, str)
    assert result.startswith("Error:")


def test_build_tools_includes_web_by_default(store):
    tools = build_tools(store)
    assert WEB_SEARCH_TOOL in tools
    assert WEB_FETCH_TOOL in tools


def test_build_tools_can_omit_web(store):
    tools = build_tools(store, web_enabled=False)
    assert WEB_SEARCH_TOOL not in tools
    assert all(not isinstance(t, dict) for t in tools)


def test_web_tools_use_dynamic_filtering_variants(store):
    # The _20260209 variants filter results server-side before they reach the
    # context window; declaring code_execution alongside them is a known
    # footgun, so assert it is absent.
    assert WEB_SEARCH_TOOL["type"] == "web_search_20260209"
    assert WEB_FETCH_TOOL["type"] == "web_fetch_20260209"
    types = {t["type"] for t in build_tools(store) if isinstance(t, dict)}
    assert not any(t.startswith("code_execution") for t in types)
