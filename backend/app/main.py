"""
FastAPI application entrypoint.

Serves the JSON API under /api and the built React SPA (everything else),
so the whole app deploys as a single Fly.io service on one origin.
"""

import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.auth import hash_password
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.models import User
from app.routes import router

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def _seed_first_user() -> None:
    """Create the seed admin user if the users table is empty."""
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            db.add(
                User(
                    username=settings.seed_username,
                    hashed_password=hash_password(settings.seed_password),
                )
            )
            db.commit()
            logger.info("Seeded first user: %s", settings.seed_username)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s (%s)", settings.app_name, settings.environment)
    # In dev this bootstraps tables; in prod prefer `alembic upgrade head`.
    Base.metadata.create_all(bind=engine)
    _seed_first_user()
    yield
    logger.info("Shutting down")


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# CORS only matters for local dev (Vite dev server on a different port).
# In production the SPA is same-origin, so the list is empty there.
if not settings.is_production:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(router)


# ── Serve the React SPA ──────────────────────────────────────────────────────
# StaticFiles handles real asset files; the catch-all returns index.html so
# client-side routes (React Router) deep-link correctly. API 404s stay JSON.
_static_dir = settings.static_dir
_index_file = os.path.join(_static_dir, "index.html")

if os.path.isdir(_static_dir):
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(_static_dir, "assets")),
        name="assets",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        candidate = os.path.join(_static_dir, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        if os.path.isfile(_index_file):
            return FileResponse(_index_file)
        return JSONResponse({"detail": "Frontend not built"}, status_code=404)
else:
    logger.warning("Static dir '%s' not found — API-only mode.", _static_dir)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=not settings.is_production,
    )
