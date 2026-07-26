"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from .config import VALID_EFFORTS, Config
from .memory import MemoryError_, MemoryStore

BANNER = """TITAN — autonomous executive assistant
model {model} | effort {effort} | memory {memory}
Commands: /reset  /usage  /memory  /help  /exit
"""


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr)


def _make_printer(show_tools: bool):
    def on_event(kind: str, payload: str) -> None:
        if kind == "text":
            print(payload)
        elif kind == "thinking":
            print(f"\033[2m[thinking] {payload}\033[0m")
        elif kind == "tool" and show_tools:
            print(f"\033[2m  · {payload}\033[0m")
        elif kind == "notice":
            _stderr(f"note: {payload}")

    return on_event


def _report(turn: Any) -> None:
    """Print anything the event stream did not already cover."""
    if turn.refused:
        category = turn.refusal_category or "unspecified"
        _stderr(
            f"\nRequest was declined by safety classifiers (category: {category}). "
            "The exchange was dropped from history; rephrasing or narrowing the "
            "request usually resolves it."
        )
    elif turn.stop_reason == "max_tokens":
        _stderr(
            "\nResponse hit the output cap and was cut off. "
            "Raise TITAN_MAX_TOKENS or ask for a narrower answer."
        )
    elif not turn.text:
        _stderr("\n(no text returned)")


def _build_agent(config: Config, show_tools: bool):
    # Imported lazily so `titan memory` and `titan doctor` work without the SDK
    # resolving credentials.
    from .agent import Agent

    return Agent(config=config, on_event=_make_printer(show_tools))


def cmd_ask(args: argparse.Namespace, config: Config) -> int:
    agent = _build_agent(config, args.show_tools)
    turn = agent.send(" ".join(args.prompt))
    _report(turn)
    if args.usage:
        _stderr(f"\nusage: {agent.usage.summary()}")
    return 1 if turn.refused else 0


def cmd_repl(args: argparse.Namespace, config: Config) -> int:
    agent = _build_agent(config, args.show_tools)
    print(
        BANNER.format(
            model=config.model, effort=config.effort, memory=agent.store.root
        )
    )

    while True:
        try:
            line = input("\n\033[1m›\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not line:
            continue
        if line in ("/exit", "/quit"):
            return 0
        if line == "/help":
            print(BANNER.format(model=config.model, effort=config.effort,
                                memory=agent.store.root))
            continue
        if line == "/reset":
            agent.reset()
            print("Session history cleared. Persistent memory is untouched.")
            continue
        if line == "/usage":
            print(agent.usage.summary())
            continue
        if line == "/memory":
            print(agent.store.overview())
            continue

        try:
            turn = agent.send(line)
        except KeyboardInterrupt:
            _stderr("\ninterrupted")
            continue
        except Exception as exc:  # keep the REPL alive on transient failures
            _stderr(f"\nerror: {type(exc).__name__}: {exc}")
            continue
        _report(turn)


def cmd_memory(args: argparse.Namespace, config: Config) -> int:
    store = MemoryStore(config.memory_root)
    try:
        if args.memory_command == "list":
            paths = store.list(args.prefix or "")
            print("\n".join(paths) if paths else "Memory is empty.")
        elif args.memory_command == "read":
            print(store.read(args.path))
        elif args.memory_command == "search":
            hits = store.search(args.query)
            if not hits:
                print(f"No matches for {args.query!r}.")
            else:
                for hit in hits:
                    print(f"{hit.path}:{hit.line_number}: {hit.line}")
        elif args.memory_command == "path":
            print(store.root)
    except MemoryError_ as exc:
        _stderr(f"error: {exc}")
        return 1
    return 0


def cmd_init(args: argparse.Namespace, config: Config) -> int:
    store = MemoryStore(config.memory_root)
    created = store.scaffold()

    print(f"Memory ready at {store.root}")
    if created:
        for path in created:
            print(f"  created {path}")
    else:
        print("  already scaffolded — nothing to do")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "\nNext: set your API key, then start a session.\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...\n"
            "  titan"
        )
    else:
        print(
            "\nNext: start a session and tell TITAN about yourself so it can "
            "fill in your profile.\n  titan"
        )
    return 0


def cmd_doctor(args: argparse.Namespace, config: Config) -> int:
    store = MemoryStore(config.memory_root)
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))

    print(f"model           {config.model}")
    print(f"effort          {config.effort}")
    print(f"max_tokens      {config.max_tokens}")
    print(f"web tools       {'on' if config.web_enabled else 'off'}")
    print(f"fallbacks       {'on' if config.fallbacks_enabled else 'off'}")
    print(f"home            {config.home}")
    print(f"memory          {store.root} ({len(store.list())} file(s))")
    print(f"ANTHROPIC_API_KEY {'set' if has_key else 'NOT SET'}")

    try:
        from .prompt import load_spec

        spec = load_spec(config)
        print(f"spec            loaded, {len(spec):,} chars")
    except Exception as exc:
        print(f"spec            ERROR: {exc}")
        return 1

    if not has_key:
        print(
            "\nNo API key found. The SDK also accepts an `ant auth login` profile; "
            "run `ant auth status` to check."
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="titan", description="TITAN — autonomous executive assistant"
    )
    parser.add_argument("--model", help="override TITAN_MODEL")
    parser.add_argument(
        "--effort", choices=VALID_EFFORTS, help="override TITAN_EFFORT"
    )
    parser.add_argument(
        "--no-web", action="store_true", help="disable web search and fetch"
    )
    parser.add_argument(
        "--show-tools", action="store_true", help="print tool calls as they happen"
    )
    parser.add_argument(
        "--thinking", action="store_true", help="show summarized reasoning"
    )

    sub = parser.add_subparsers(dest="command")

    ask = sub.add_parser("ask", help="one-shot question")
    ask.add_argument("prompt", nargs="+")
    ask.add_argument("--usage", action="store_true", help="print token usage after")

    mem = sub.add_parser("memory", help="inspect persistent memory")
    mem_sub = mem.add_subparsers(dest="memory_command", required=True)
    ls = mem_sub.add_parser("list")
    ls.add_argument("prefix", nargs="?", default="")
    rd = mem_sub.add_parser("read")
    rd.add_argument("path")
    se = mem_sub.add_parser("search")
    se.add_argument("query")
    mem_sub.add_parser("path")

    sub.add_parser("init", help="scaffold the memory directory")
    sub.add_parser("doctor", help="show resolved configuration")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = Config.from_env()
    except ValueError as exc:
        _stderr(f"configuration error: {exc}")
        return 2

    overrides: dict[str, Any] = {}
    if args.model:
        overrides["model"] = args.model
    if args.effort:
        overrides["effort"] = args.effort
    if args.no_web:
        overrides["web_enabled"] = False
    if args.thinking:
        overrides["show_thinking"] = True
    if overrides:
        config = replace_config(config, overrides)

    # Commands that never touch the API run regardless of credentials.
    if args.command == "memory":
        return cmd_memory(args, config)
    if args.command == "init":
        return cmd_init(args, config)
    if args.command == "doctor":
        return cmd_doctor(args, config)

    if not _has_credentials():
        _stderr(
            "No API credentials found. Set a key and try again:\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...\n"
            "Get one at https://console.anthropic.com/settings/keys\n"
            "(An `ant auth login` profile also works — run `titan doctor` to check.)"
        )
        return 2

    if args.command == "ask":
        return cmd_ask(args, config)
    return cmd_repl(args, config)


def _has_credentials() -> bool:
    """Mirror the SDK's resolution order closely enough to fail helpfully.

    The SDK also accepts an `ant auth login` profile, so an unset env var alone
    is not proof there are no credentials.
    """
    from pathlib import Path

    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    config_dir = os.environ.get("ANTHROPIC_CONFIG_DIR")
    base = Path(config_dir) if config_dir else Path.home() / ".config" / "anthropic"
    return (base / "credentials").is_dir()


def replace_config(config: Config, overrides: dict[str, Any]) -> Config:
    import dataclasses

    return dataclasses.replace(config, **overrides)


if __name__ == "__main__":
    raise SystemExit(main())
