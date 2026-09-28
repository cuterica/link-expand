#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if command -v uv >/dev/null 2>&1; then
  uv sync --locked
  uv run python -m linkexpand.setup_browser
  exec uv run --locked link-expand "$@"
fi
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m linkexpand.setup_browser
exec .venv/bin/python -m linkexpand.server "$@"
