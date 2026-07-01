# ── Stage 1: build the React SPA ─────────────────────────────────────────────
FROM node:20-slim AS frontend
WORKDIR /ui
COPY ui/package.json ui/package-lock.json* ./
RUN npm install
COPY ui/ ./
# Build straight to a predictable path (overrides vite.config outDir).
RUN npm run build -- --outDir /static --emptyOutDir

# ── Stage 2: Python runtime serving API + built SPA ──────────────────────────
FROM python:3.11-slim AS runtime
WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# Install deps first (better layer caching).
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Backend source (app/, alembic/, alembic.ini).
COPY backend/ ./

# Built frontend from stage 1 → served by FastAPI at /  (static_dir="static").
COPY --from=frontend /static ./static

# Non-root user.
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

ENV PYTHONUNBUFFERED=1 ENVIRONMENT=production PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD curl -fsS http://localhost:8000/api/health || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
