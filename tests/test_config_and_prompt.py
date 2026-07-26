import pytest

from titan.config import DEFAULT_EFFORT, DEFAULT_MAX_TOKENS, DEFAULT_MODEL, Config
from titan.memory import MemoryStore
from titan.prompt import build_system, load_spec


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "TITAN_MODEL",
        "TITAN_EFFORT",
        "TITAN_HOME",
        "TITAN_MAX_TOKENS",
        "TITAN_WEB",
        "TITAN_SHOW_THINKING",
        "TITAN_FALLBACKS",
        "TITAN_MAX_ITERATIONS",
        "TITAN_MAX_RESTARTS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("TITAN_HOME", str(tmp_path))
    config = Config.from_env()
    assert config.model == DEFAULT_MODEL == "claude-opus-5"
    assert config.effort == DEFAULT_EFFORT
    assert config.max_tokens == DEFAULT_MAX_TOKENS
    assert config.web_enabled is True
    assert config.show_thinking is False


def test_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("TITAN_HOME", str(tmp_path))
    monkeypatch.setenv("TITAN_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("TITAN_EFFORT", "xhigh")
    monkeypatch.setenv("TITAN_MAX_TOKENS", "8000")
    monkeypatch.setenv("TITAN_WEB", "false")
    monkeypatch.setenv("TITAN_SHOW_THINKING", "yes")

    config = Config.from_env()
    assert config.model == "claude-sonnet-5"
    assert config.effort == "xhigh"
    assert config.max_tokens == 8000
    assert config.web_enabled is False
    assert config.show_thinking is True


def test_invalid_effort_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("TITAN_HOME", str(tmp_path))
    monkeypatch.setenv("TITAN_EFFORT", "turbo")
    with pytest.raises(ValueError, match="TITAN_EFFORT"):
        Config.from_env()


def test_invalid_max_tokens_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("TITAN_HOME", str(tmp_path))
    monkeypatch.setenv("TITAN_MAX_TOKENS", "-5")
    with pytest.raises(ValueError):
        Config.from_env()


def test_non_numeric_max_tokens_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("TITAN_HOME", str(tmp_path))
    monkeypatch.setenv("TITAN_MAX_TOKENS", "lots")
    with pytest.raises(ValueError):
        Config.from_env()


def test_effort_is_case_insensitive(monkeypatch, tmp_path):
    monkeypatch.setenv("TITAN_HOME", str(tmp_path))
    monkeypatch.setenv("TITAN_EFFORT", "HIGH")
    assert Config.from_env().effort == "high"


def test_memory_root_under_home(tmp_path):
    config = Config(home=tmp_path)
    assert config.memory_root == tmp_path / "memory"


# ---- prompt ----------------------------------------------------------------


def test_spec_loads_from_repo(tmp_path):
    spec = load_spec(Config(home=tmp_path))
    assert "TITAN" in spec
    assert "Primary objective" in spec


def test_user_override_spec_wins(tmp_path):
    (tmp_path).mkdir(exist_ok=True)
    (tmp_path / "TITAN.md").write_text("custom spec", encoding="utf-8")
    assert load_spec(Config(home=tmp_path)) == "custom spec"


def test_system_blocks_put_cache_breakpoint_on_frozen_spec(tmp_path):
    config = Config(home=tmp_path)
    store = MemoryStore(config.memory_root)
    blocks = build_system(config, store)

    assert len(blocks) == 2
    # The cached block must be the stable one, and it must come first: caching is
    # a prefix match, so a volatile block ahead of it would break every hit.
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "Primary objective" in blocks[0]["text"]
    assert "cache_control" not in blocks[1]


def test_volatile_block_carries_date_and_memory_index(tmp_path):
    config = Config(home=tmp_path)
    store = MemoryStore(config.memory_root)
    store.write("profile.md", "x")
    volatile = build_system(config, store)[1]["text"]
    assert "Today's date is" in volatile
    assert "profile.md" in volatile
