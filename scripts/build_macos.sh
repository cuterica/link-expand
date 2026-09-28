#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
build_env="$HOME/Library/Application Support/LinkExpand/build-venv"
if [[ ! -x "$build_env/bin/python" ]]; then python3 -m venv "$build_env"; fi
"$build_env/bin/python" -m pip install -r requirements-build.txt
exec "$build_env/bin/python" scripts/build_macos.py "$@"
