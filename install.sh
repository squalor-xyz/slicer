#!/usr/bin/env bash
# Contributor install: an editable install into ./.venv, linked onto PATH.
# Mirrors the "Install" section of AGENTS.md. To just use slicer, see README.md.
#
# Usage: ./install.sh [--bin-dir DIR]   (default DIR: ~/.local/bin)
set -euo pipefail

bin_dir="${HOME}/.local/bin"
while [ $# -gt 0 ]; do
  case "$1" in
    --bin-dir) bin_dir="${2:?--bin-dir needs a directory}"; shift 2 ;;
    -h|--help) sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "install.sh: unknown argument: $1" >&2; exit 2 ;;
  esac
done

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$root"

python="${PYTHON:-python3}"
if ! "$python" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  echo "install.sh: Python 3.11+ is required (set PYTHON=/path/to/python to override)." >&2
  exit 1
fi

if [ ! -x .venv/bin/pip ]; then
  rm -rf .venv   # absent, or left half-built by an earlier failed run
  if ! "$python" -m venv .venv; then
    rm -rf .venv
    echo "install.sh: could not create .venv. On Debian/Ubuntu: apt install python3-venv" >&2
    exit 1
  fi
fi
.venv/bin/pip install --quiet -e .

mkdir -p "$bin_dir"
ln -sfn "$root/.venv/bin/slicer" "$bin_dir/slicer"
echo "Installed slicer (editable) -> $bin_dir/slicer"

case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) echo "Note: $bin_dir is not on your PATH." >&2 ;;
esac

.venv/bin/slicer --about
