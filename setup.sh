#!/usr/bin/env bash
# One-command setup for TITAN. Safe to re-run.
set -euo pipefail

cd "$(dirname "$0")"

BOLD=$(tput bold 2>/dev/null || true)
DIM=$(tput dim 2>/dev/null || true)
RESET=$(tput sgr0 2>/dev/null || true)

step() { printf "\n%s==> %s%s\n" "$BOLD" "$1" "$RESET"; }
note() { printf "%s    %s%s\n" "$DIM" "$1" "$RESET"; }

# --- python ------------------------------------------------------------------
step "Checking Python"
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      PYTHON="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  echo "Python 3.10 or newer is required but was not found." >&2
  echo "Install it from https://www.python.org/downloads/ and re-run this script." >&2
  exit 1
fi
note "using $($PYTHON --version) at $(command -v $PYTHON)"

# --- venv --------------------------------------------------------------------
step "Creating virtual environment (.venv)"
if [ ! -d .venv ]; then
  "$PYTHON" -m venv .venv
  note "created .venv"
else
  note ".venv already exists, reusing it"
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# --- install -----------------------------------------------------------------
step "Installing TITAN"
python -m pip install --upgrade pip --quiet
pip install -e ".[dev]" --quiet
note "installed"

# --- verify ------------------------------------------------------------------
step "Running tests"
python -m pytest -q

step "Scaffolding memory"
titan init

step "Configuration"
titan doctor || true

# --- next steps --------------------------------------------------------------
printf "\n%s==> Setup complete%s\n\n" "$BOLD" "$RESET"

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  cat <<'EOF'
One thing left, and only you can do it: TITAN needs an Anthropic API key.

  1. Create one at https://console.anthropic.com/settings/keys
  2. Add it to your shell profile so it persists:

       echo 'export ANTHROPIC_API_KEY=sk-ant-...' >> ~/.zshrc   # or ~/.bashrc
       source ~/.zshrc

  3. Start TITAN:

       source .venv/bin/activate
       titan

EOF
else
  cat <<'EOF'
API key detected. Start TITAN with:

    source .venv/bin/activate
    titan

EOF
fi
