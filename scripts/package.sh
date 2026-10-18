#!/usr/bin/env bash
# Make a clean zip of the project (no node_modules, builds, virtualenvs or secrets) next to the project folder.
set -euo pipefail
cd "$(dirname "$0")/.."
NAME="$(basename "$(pwd)")"
cd ..
rm -f "$NAME.zip"
zip -qr "$NAME.zip" "$NAME" -x "*/node_modules/*" "*/dist/*" "*/.venv/*" "*/venv/*" "*/__pycache__/*" \
  "*/.pytest_cache/*" "*/.git/*" "*/.env" "*.pyc" "*.zip"
echo "Wrote $(pwd)/$NAME.zip"
