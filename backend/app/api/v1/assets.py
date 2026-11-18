import os
import uuid
import logging
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session as DBSession
from sqlalchemy import select

from app.core.config import settings
from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.models.asset import GeneratedAsset
from app.models.project import VideoProject
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.services.storage import get_storage_service

logger = logging.getLogger("video_gen_app.assets")

router = APIRouter(prefix="/assets", tags=["Assets"])


def _resolve_asset_file(asset_id: uuid.UUID, current_user: User, db: DBSession) -> tuple[str, str]:
    """Helper to verify asset exists, enforce ownership, and resolve disk file path."""
    asset = db.get(GeneratedAsset, asset_id)
    if not asset:
        raise NotFoundError(detail="Generated asset not found")

    # Verify project ownership
    project = db.get(VideoProject, asset.project_id)
    if not project or str(project.user_id) != str(current_user.id):
        raise PermissionDeniedError(detail="Access denied to this asset")

    # Resolve file on disk checking multiple standard paths
    clean_path = asset.file_path.replace("\\", "/").lstrip("/")
    candidates = [
        os.path.abspath(clean_path),
        os.path.abspath(os.path.join("storage", clean_path)),
        os.path.abspath(os.path.join(settings.OUTPUT_DIR, clean_path.replace("outputs/", ""))),
        os.path.abspath(os.path.join(settings.OUTPUT_DIR, "clips", os.path.basename(clean_path))),
        os.path.abspath(os.path.join(settings.OUTPUT_DIR, "audio", os.path.basename(clean_path))),
        os.path.abspath(os.path.join(settings.OUTPUT_DIR, "music", os.path.basename(clean_path))),
        os.path.abspath(os.path.join(settings.OUTPUT_DIR, "videos", os.path.basename(clean_path))),
        os.path.abspath(os.path.join("backend", clean_path)),
        os.path.abspath(os.path.join("media", "music", clean_path.replace("media/music/", ""))),
        os.path.abspath(os.path.join("backend", "media", "music", clean_path.replace("media/music/", ""))),
        # Also check relative to project root if running from backend/ subdirectory
        os.path.abspath(os.path.join("..", clean_path)),
        os.path.abspath(os.path.join("..", "storage", clean_path)),
        os.path.abspath(os.path.join("..", "storage", "outputs", "clips", os.path.basename(clean_path))),
        os.path.abspath(os.path.join("..", "storage", "outputs", "audio", os.path.basename(clean_path))),
        os.path.abspath(os.path.join("..", "storage", "outputs", "music", os.path.basename(clean_path))),
        os.path.abspath(os.path.join("..", "storage", "outputs", "videos", os.path.basename(clean_path))),
        os.path.abspath(os.path.join("..", "backend", clean_path)),
        os.path.abspath(os.path.join("..", "backend", "media", "music", clean_path.replace("media/music/", ""))),
    ]
    try:
        candidates.append(get_storage_service().get_full_path(clean_path))
    except Exception:
        pass

    full_path = None
    for c in candidates:
        if os.path.isfile(c) and os.path.getsize(c) > 0:
            full_path = c
            break

    if not full_path:
        raise NotFoundError(detail="Asset file does not exist on disk")

    mime = "video/mp4"
    if full_path.endswith(".wav"):
        mime = "audio/wav"
    elif full_path.endswith(".mp3"):
        mime = "audio/mpeg"
    elif full_path.endswith(".png"):
        mime = "image/png"
    elif full_path.endswith(".jpg") or full_path.endswith(".jpeg"):
        mime = "image/jpeg"
    elif full_path.endswith(".webp"):
        mime = "image/webp"

    return full_path, mime


@router.get("/{asset_id}/stream")
def stream_asset_video(
    asset_id: uuid.UUID,
    request: Request,
    db: DBSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Securely stream a generated video clip or final video file.

    Supports HTTP Range requests (206 Partial Content) for browser seeking.
    """
    full_path, mime = _resolve_asset_file(asset_id, current_user, db)
    return FileResponse(
        path=full_path,
        media_type=mime,
        filename=os.path.basename(full_path),
    )


@router.get("/{asset_id}/download")
def download_asset_file(
    asset_id: uuid.UUID,
    db: DBSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Securely download a generated video clip, voiceover, music, or final video as attachment."""
    full_path, mime = _resolve_asset_file(asset_id, current_user, db)
    return FileResponse(
        path=full_path,
        media_type=mime,
        filename=os.path.basename(full_path),
        content_disposition_type="attachment",
    )


