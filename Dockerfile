# One container: React UI (static) + FastAPI backend, served on one port.
# Built and run by Hugging Face Spaces (Docker SDK), works on any Docker host.

# ---- 1. build the frontend ----
FROM node:20-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 2. backend runtime ----
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860

# Spaces run containers as uid 1000; give it a home and writable app dir.
RUN useradd -m -u 1000 app
WORKDIR /app/backend
COPY backend/requirements.lock.txt ./
RUN pip install -r requirements.lock.txt
# rasterio's bundled GDAL links against the system expat library, absent from slim images
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

COPY --chown=app backend/ ./
COPY --from=web --chown=app /web/dist /app/frontend/dist
RUN mkdir -p .cache && chown -R app /app
USER app

EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health')"
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
