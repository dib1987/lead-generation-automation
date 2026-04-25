from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.settings import settings
from app.api.v1.health import router as health_router


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

    return app


app = create_app()
