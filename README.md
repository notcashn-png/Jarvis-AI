# Jarvis-AI — Project TITAN

An autonomous executive assistant with persistent memory, built on the Claude API.

TITAN is two things: an **operating spec** ([`titan/TITAN.md`](titan/TITAN.md)) that defines
how it thinks and communicates, and an **agent** that runs that spec with tools
and a memory directory that survives between sessions. The memory is the part
that matters — it is what makes the difference between an assistant you re-brief
every morning and one that gets more useful every week.

## Quick start

```bash
pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...      # or run `ant auth login`

titan doctor                             # verify configuration
titan                                    # interactive session
titan ask "Where is my time going and what should I automate first?"
```

## What it can do

| Capability | How |
|---|---|
| Remember you between sessions | Six memory tools over a sandboxed notes directory |
| Research with current information | Server-side web search and fetch with dynamic filtering |
| Track goals, projects, decisions | Structured memory layout the spec tells it to maintain |
| Learn your preferences | Corrections are written to `preferences.md` as they happen |

## Memory

Memory lives in `~/.titan/memory` (override with `TITAN_HOME`). The spec directs
TITAN to maintain:

```
profile.md              who you are, constraints, working style
goals.md                active goals, targets, status
preferences.md          tone, formats, conventions you've corrected it on
projects/<name>.md      one file per project: state, metrics, blockers, next actions
decisions/<date>-<slug>.md   options considered, choice, reasoning
log/<date>.md           running record of work and lessons
```

Inspect it from the shell without starting a session:

```bash
titan memory list
titan memory read profile.md
titan memory search "pricing"
titan memory path
```

Memory is plain markdown — read it, edit it, put it under version control, or
delete a file TITAN got wrong. It is your data.

**Nothing secret goes in memory.** The spec forbids writing credentials, and the
store is sandboxed against path traversal, but memory is plaintext on disk — keep
keys in a password manager or environment variables.

## Configuration

Everything is environment variables, all optional:

| Variable | Default | Notes |
|---|---|---|
| `TITAN_MODEL` | `claude-opus-5` | |
| `TITAN_EFFORT` | `high` | `low`/`medium`/`high`/`xhigh`/`max`. The main cost and latency lever — `low` and `medium` are unusually strong on Opus 5, so sweep downward before assuming you need `high`. |
| `TITAN_MAX_TOKENS` | `16000` | Requests are non-streaming; much above this risks HTTP timeouts. |
| `TITAN_HOME` | `~/.titan` | Memory and spec override live here. |
| `TITAN_WEB` | `true` | Set false to disable web search and fetch. |
| `TITAN_SHOW_THINKING` | `false` | Show summarized reasoning. |
| `TITAN_FALLBACKS` | `true` | Server-side fallback on a policy refusal. |

Flags override env for one run: `titan --effort xhigh --show-tools ask "..."`.

## Customizing behavior

`titan/TITAN.md` **is** the system prompt — there is no behavior hidden in the code
that the spec does not describe. To change how TITAN operates, edit it. To keep
your changes separate from the repo, copy it to `~/.titan/TITAN.md`; that
override wins.

The spec is loaded into a cached prompt block, so edits take effect on the next
run at no extra cost after the first request.

## Architecture

```
titan/TITAN.md    the operating spec — system prompt, cached prefix
titan/config.py   environment-driven configuration
titan/memory.py   sandboxed file store; _resolve is the security boundary
titan/tools.py    six memory tools + server-side web tools
titan/prompt.py   system block assembly, ordered for prompt caching
titan/agent.py    the tool loop: pause_turn restarts, refusal handling, usage
titan/cli.py      REPL, one-shot ask, memory inspection, doctor
```

Two details worth knowing if you extend it:

- **Prompt caching is a prefix match.** The frozen spec is the first system block
  and carries the cache breakpoint; today's date and the memory index go *after*
  it. Putting anything volatile ahead of the breakpoint would void every cache
  hit on every turn.
- **The Python tool runner does not auto-resume `pause_turn`.** A long web-search
  turn can stop paused, and the runner returns it as a normal final message with
  no error. `Agent._run` mirrors history and restarts the runner, bounded by
  `TITAN_MAX_RESTARTS`, so a paused turn does not silently truncate the answer.

## Tests

```bash
pip install -e ".[dev]"
python -m pytest
```

90 tests, no network and no API key required — the agent tests run against a
fake client. The heaviest coverage is on `MemoryStore` path handling, since every
path it sees comes from model output.

## Status

Working foundation. Not yet built: scheduled/proactive runs without a human at
the prompt, calendar and email integration, and multi-project dashboards.
