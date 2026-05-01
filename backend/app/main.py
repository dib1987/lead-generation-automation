from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.settings import settings
import app.db.base  # noqa: F401 — pre-loads all SQLAlchemy models before any route handler runs, breaking the circular import chain
from app.api.v1.health import router as health_router
from app.api.v1.leads import router as leads_router


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

    return app


app = create_app()
