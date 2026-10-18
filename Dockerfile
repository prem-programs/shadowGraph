# ShadowGraph: one image with the API, the MCP server (/mcp) and the built dashboard.
# Build:  docker build -t shadowgraph .        Run with FalkorDB:  docker compose up

# ---- 1. build the dashboard
FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 2. the Python app
FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FASTEMBED_CACHE_PATH=/app/models \
    STATIC_DIR=/app/static \
    PYTHONPATH=/app/backend \
    PORT=8000
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt
# Pre-download the embedding model (about 90 MB) so the container starts offline.
# If the build machine has no internet access, the app falls back to its built-in hashing embedder.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/all-MiniLM-L6-v2')" \
    || echo "embedding model not downloaded; the hashing embedder will be used"
COPY backend/ backend/
COPY --from=ui /ui/dist /app/static
RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser
WORKDIR /app/backend
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT','8000'), timeout=4)" || exit 1
CMD ["python", "-m", "shadowgraph", "serve"]
