from fastapi import APIRouter
from app.api.v1.auth import router as auth_router
from app.api.v1.projects import router as projects_router
from app.api.v1.assets import router as assets_router
from app.api.v1.music import router as music_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(projects_router)
api_router.include_router(assets_router)
api_router.include_router(music_router)


@api_router.get("/health", tags=["Health"])
async def health_check():
    """System and API health check endpoint."""
    return {
        "status": "healthy",
        "service": "AI Video Generation API",
        "version": "1.0.0"
    }
