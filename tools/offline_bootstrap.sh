#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WHEELDIR="$ROOT/vendor/wheelhouse-linux"

# Reassemble any split wheels (e.g., PySide6_Addons)
shopt -s nullglob
for aa in "$WHEELDIR"/*.whl.part-aa; do
  base="${aa%.part-*}"
  echo "Reassembling $(basename "$base")"
  cat "$base".part-* > "$base"
done

# Fresh venv for a clean, isolated run
python -m venv "$ROOT/.venv"
. "$ROOT/.venv/bin/activate"

# Force offline installs from vendored wheels only
export PIP_NO_INDEX=1
export PIP_FIND_LINKS="$WHEELDIR"
export PIP_DISABLE_PIP_VERSION_CHECK=1

# Keep Qt headless/quiet in CI-like environments
export QT_QPA_PLATFORM=offscreen
export QT_OPENGL=software

# Install deps strictly from vendor/
[ -f "$ROOT/tests/requirements.txt" ] && python -m pip install -r "$ROOT/tests/requirements.txt"
[ -f "$ROOT/requirements.txt" ] && python -m pip install -r "$ROOT/requirements.txt"

# Run unit tests via your Makefile (still offline)
make -C "$ROOT" ONLINE=1 PIP_NO_INDEX=1 PIP_FIND_LINKS="$WHEELDIR" test_unit
