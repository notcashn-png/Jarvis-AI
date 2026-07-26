"""CLI tests. None of these reach the network."""

from __future__ import annotations

import pytest

from titan import cli
from titan.memory import MemoryStore


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    monkeypatch.setenv("TITAN_HOME", str(tmp_path / "home"))
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_CONFIG_DIR"):
        monkeypatch.delenv(name, raising=False)
    # Point credential discovery at an empty dir so a real profile on the host
    # cannot make these tests pass or fail spuriously.
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(tmp_path / "no-creds"))
    yield


def test_doctor_runs_without_credentials(capsys):
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "claude-opus-5" in out
    assert "NOT SET" in out


def test_init_scaffolds_memory(capsys, tmp_path):
    assert cli.main(["init"]) == 0
    out = capsys.readouterr().out
    assert "created profile.md" in out
    assert "goals.md" in out

    store = MemoryStore(tmp_path / "home" / "memory")
    assert set(store.list()) == {"profile.md", "goals.md", "preferences.md"}


def test_init_is_idempotent_and_preserves_content(capsys, tmp_path):
    cli.main(["init"])
    store = MemoryStore(tmp_path / "home" / "memory")
    store.write("profile.md", "MY REAL PROFILE")

    capsys.readouterr()
    assert cli.main(["init"]) == 0
    assert "nothing to do" in capsys.readouterr().out
    # Re-running init must never clobber the principal's own content.
    assert store.read("profile.md") == "MY REAL PROFILE"


def test_memory_commands_work_without_credentials(capsys, tmp_path):
    cli.main(["init"])
    capsys.readouterr()

    assert cli.main(["memory", "list"]) == 0
    assert "profile.md" in capsys.readouterr().out

    assert cli.main(["memory", "search", "Preferences"]) == 0
    assert "preferences.md" in capsys.readouterr().out

    assert cli.main(["memory", "path"]) == 0
    assert "memory" in capsys.readouterr().out


def test_memory_read_missing_file_exits_nonzero(capsys):
    assert cli.main(["memory", "read", "nope.md"]) == 1
    assert "error:" in capsys.readouterr().err


def test_memory_read_rejects_traversal(capsys):
    assert cli.main(["memory", "read", "../../etc/passwd"]) == 1
    assert "error:" in capsys.readouterr().err


def test_ask_without_credentials_is_a_clean_failure(capsys):
    # Must not raise a raw SDK error at the user.
    assert cli.main(["ask", "hello"]) == 2
    err = capsys.readouterr().err
    assert "No API credentials found" in err
    assert "console.anthropic.com" in err


def test_repl_without_credentials_is_a_clean_failure(capsys):
    assert cli.main([]) == 2
    assert "No API credentials found" in capsys.readouterr().err


def test_env_var_credential_passes_the_check(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert cli._has_credentials() is True


def test_auth_token_credential_passes_the_check(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
    assert cli._has_credentials() is True


def test_profile_directory_counts_as_credentials(monkeypatch, tmp_path):
    config_dir = tmp_path / "anthropic"
    (config_dir / "credentials").mkdir(parents=True)
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(config_dir))
    # An unset env var is not proof there are no credentials.
    assert cli._has_credentials() is True


def test_invalid_effort_env_exits_with_config_error(monkeypatch, capsys):
    monkeypatch.setenv("TITAN_EFFORT", "ludicrous")
    assert cli.main(["doctor"]) == 2
    assert "configuration error" in capsys.readouterr().err


def test_cli_flags_override_config(capsys):
    assert cli.main(["--model", "claude-sonnet-5", "--effort", "low", "doctor"]) == 0
    out = capsys.readouterr().out
    assert "claude-sonnet-5" in out
    assert "effort          low" in out


# ---- API error messages ----------------------------------------------------
#
# These are the ordinary first-run failures. A traceback here reads as "the tool
# is broken" when the fix is usually one step in the console.


def _api_error(cls, message, status=400, body=None):
    import anthropic  # noqa: F401
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request, json=body or {})
    return cls(message, response=response, body=body)


def test_out_of_credits_is_explained_not_traced():
    import anthropic

    exc = _api_error(
        anthropic.BadRequestError,
        "Your credit balance is too low to access the Anthropic API.",
    )
    msg = cli.explain_api_error(exc)
    assert "out of credits" in msg
    assert "key itself is valid" in msg
    assert "billing" in msg


def test_bad_key_is_explained():
    import anthropic

    msg = cli.explain_api_error(
        _api_error(anthropic.AuthenticationError, "invalid x-api-key", status=401)
    )
    assert "rejected" in msg
    assert "settings/keys" in msg


def test_rate_limit_is_explained():
    import anthropic

    msg = cli.explain_api_error(
        _api_error(anthropic.RateLimitError, "slow down", status=429)
    )
    assert "Rate limited" in msg


def test_unknown_model_is_explained():
    import anthropic

    msg = cli.explain_api_error(
        _api_error(anthropic.NotFoundError, "model not found", status=404)
    )
    assert "TITAN_MODEL" in msg


def test_connection_error_is_explained():
    import anthropic
    import httpx

    exc = anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    assert "network" in cli.explain_api_error(exc).lower()


def test_other_bad_request_passes_the_message_through():
    import anthropic

    msg = cli.explain_api_error(
        _api_error(anthropic.BadRequestError, "max_tokens: must be positive")
    )
    assert "max_tokens" in msg


def test_unexpected_exception_still_produces_a_string():
    assert "ValueError" in cli.explain_api_error(ValueError("boom"))


def test_ask_surfaces_api_error_cleanly(monkeypatch, capsys):
    import anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    def boom(self, user_input):
        raise _api_error(
            anthropic.BadRequestError,
            "Your credit balance is too low to access the Anthropic API.",
        )

    from titan.agent import Agent

    monkeypatch.setattr(Agent, "send", boom)

    # The failure must be a clean message and a non-zero exit, not a traceback.
    assert cli.main(["ask", "hello"]) == 1
    assert "out of credits" in capsys.readouterr().err
