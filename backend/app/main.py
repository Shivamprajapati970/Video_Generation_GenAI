import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.database import engine
from app.core.exceptions import AppError
from app.models.base import Base
# Import all models so metadata knows all tables
import app.models
from app.api.v1.router import api_router

# Configure logging
logger = logging.getLogger("video_gen_app")
logger.setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifespan events."""
    logger.info("Initializing application startup...")

    # Ensure required local storage directories exist
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    os.makedirs(settings.OUTPUT_DIR, exist_ok=True)

    # Initialize tables if configured or when using fallback
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Database schema tables verified and ready.")
    except Exception as exc:
        logger.warning(
            f"Database auto-creation skipped: {exc}"
        )

    # Resume any interrupted scene planning jobs from previous runs
    try:
        import asyncio
        from app.services.scene_planner import get_scene_planning_service
        from app.core.database import SessionLocal
        planner = get_scene_planning_service()
        asyncio.create_task(planner.resume_interrupted_jobs(SessionLocal))
        logger.info("Startup recovery task scheduled for pending/interrupted scene planning jobs.")
    except Exception as exc:
        logger.warning(f"Could not trigger startup job recovery: {exc}")

    yield

    logger.info("Application shutdown: disposing database connection pool...")
    engine.dispose()


def create_application() -> FastAPI:
    """Application factory for FastAPI server."""
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        debug=settings.DEBUG,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan
    )

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Custom Application Exception Handler
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.detail
                }
            },
            headers=exc.headers
        )

    # General Exception Handler
    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        logger.exception(f"Unhandled exception on {request.method} {request.url.path}: {exc}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "An unexpected internal server error occurred."
                }
            }
        )

    # Mount local storage for file serving in development
    if os.path.exists(settings.OUTPUT_DIR):
        app.mount("/outputs", StaticFiles(directory=settings.OUTPUT_DIR), name="outputs")

    # Include API Routers
    app.include_router(api_router, prefix=settings.API_V1_STR)

    @app.get("/", tags=["Root"])
    async def root():
        return {
            "name": settings.PROJECT_NAME,
            "version": settings.VERSION,
            "docs": "/docs",
            "health": f"{settings.API_V1_STR}/health"
        }

    return app


app = create_application()
