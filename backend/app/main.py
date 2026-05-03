import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.core.settings import settings
from app.core.logging_config import configure_logging
import app.db.base  # noqa: F401 — pre-loads all SQLAlchemy models before any route handler runs, breaking the circular import chain
from app.api.v1.health import router as health_router
from app.api.v1.leads import router as leads_router
from app.api.v1.webhooks import router as webhooks_router
from app.api.v1.admin import router as admin_router

_FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

configure_logging(settings.environment)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: nothing to initialize (engine is created at import time)
    yield
    # Shutdown: nothing to clean up for now


def create_app() -> FastAPI:
    app = FastAPI(
        title="Lead Generation System",
        description="Domain-agnostic lead capture and automated outreach",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router, prefix="/api/v1")
    app.include_router(leads_router, prefix="/api/v1/leads")
    app.include_router(webhooks_router, prefix="/api/v1/webhooks")
    app.include_router(admin_router, prefix="/api/v1/admin")

    frontend_dir = os.path.normpath(_FRONTEND_DIR)
    if os.path.isdir(frontend_dir):
        app.mount("/frontend", StaticFiles(directory=frontend_dir), name="frontend")

        @app.get("/", include_in_schema=False)
        async def serve_lead_form():
            return FileResponse(os.path.join(frontend_dir, "index.html"))

    return app


app = create_app()
