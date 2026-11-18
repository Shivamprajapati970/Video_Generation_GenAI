import os
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import FileResponse

from app.core.exceptions import NotFoundError, BadRequestError
from app.schemas.job import MusicTrackResponse
from app.ai.static_music import StaticMusicLibraryProvider, get_static_music_provider

logger = logging.getLogger("video_gen_app.music_api")

router = APIRouter(prefix="/music", tags=["Music Library"])


@router.get("/categories", response_model=List[str], status_code=status.HTTP_200_OK)
def list_music_categories(
    provider: StaticMusicLibraryProvider = Depends(get_static_music_provider),
):
    """Retrieve all available music library categories."""
    return provider.list_categories()


@router.get("/tracks", response_model=List[MusicTrackResponse], status_code=status.HTTP_200_OK)
def list_music_tracks(
    category: Optional[str] = Query(default=None, description="Filter tracks by category (e.g. birthday, cinematic)"),
    provider: StaticMusicLibraryProvider = Depends(get_static_music_provider),
):
    """List curated royalty-free/development music tracks, optionally filtered by category."""
    tracks = provider.list_tracks(category=category)
    return [
        MusicTrackResponse(
            id=t["id"],
            title=t["title"],
            category=t["category"],
            description=t["description"],
            duration_seconds=t["duration_seconds"],
            audio_url=t["audio_url"],
            preview_url=t["preview_url"],
            attribution=t["attribution"],
            license=t["license"],
            available=t["available"],
            source=t.get("source"),
            commercial_use=t.get("commercial_use", False),
        )
        for t in tracks
    ]


@router.get("/tracks/{track_id}", response_model=MusicTrackResponse, status_code=status.HTTP_200_OK)
def get_music_track(
    track_id: str,
    provider: StaticMusicLibraryProvider = Depends(get_static_music_provider),
):
    """Retrieve metadata for a specific music track by ID."""
    t = provider.get_track(track_id)
    if not t:
        raise NotFoundError(detail=f"Music track '{track_id}' not found in library")

    return MusicTrackResponse(
        id=t["id"],
        title=t["title"],
        category=t["category"],
        description=t["description"],
        duration_seconds=t["duration_seconds"],
        audio_url=t["audio_url"],
        preview_url=t["preview_url"],
        attribution=t["attribution"],
        license=t["license"],
        available=t["available"],
        source=t.get("source"),
        commercial_use=t.get("commercial_use", False),
    )


@router.get("/tracks/{track_id}/stream")
def stream_music_track(
    track_id: str,
    request: Request,
    provider: StaticMusicLibraryProvider = Depends(get_static_music_provider),
):
    """Preview or stream a library music track with Range request (206) support."""
    file_path = provider.get_track_file_path(track_id)
    if not os.path.isfile(file_path):
        raise NotFoundError(detail=f"Audio file for track '{track_id}' was not found on disk")

    ext = os.path.splitext(file_path)[1].lower()
    media_type = "audio/wav" if ext == ".wav" else "audio/mpeg"

    return FileResponse(
        path=file_path,
        media_type=media_type,
        filename=os.path.basename(file_path),
    )
