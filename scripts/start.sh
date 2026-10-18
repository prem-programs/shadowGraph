#!/usr/bin/env bash
# Start ShadowGraph on macOS / Linux (or Git Bash on Windows).
#   ./scripts/start.sh          docker compose (recommended)
#   ./scripts/start.sh local    Python + Node here; FalkorDB from FALKORDB_URL (.env) or a local server
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { cp .env.example .env; echo "Created .env from .env.example (all values optional)."; }
MODE="${1:-docker}"

if [ "$MODE" = "docker" ]; then
  command -v docker >/dev/null || { echo "Install Docker first, or run: ./scripts/start.sh local"; exit 1; }
  docker compose up --build -d
  echo "Waiting for http://localhost:8000 ..."
  for _ in $(seq 1 90); do curl -fs localhost:8000/api/health >/dev/null && break; sleep 2; done
  echo "Open http://localhost:8000   (stop: docker compose down)"
  exit 0
fi

PY="${PYTHON:-python3}"
[ -d backend/.venv ] || "$PY" -m venv backend/.venv
backend/.venv/bin/pip install -q -r backend/requirements.txt
(cd frontend && { [ -d node_modules ] || npm ci --no-audit --no-fund; } && npm run build)
export STATIC_DIR="$PWD/frontend/dist"
cd backend && exec .venv/bin/python -m shadowgraph serve
