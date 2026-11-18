import uuid
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel, ConfigDict


class JobStage(str, Enum):
    PENDING = "PENDING"
    ANALYZING_IMAGES = "ANALYZING_IMAGES"
    GENERATING_CLIPS = "GENERATING_CLIPS"
    GENERATING_VOICEOVER = "GENERATING_VOICEOVER"
    GENERATING_MUSIC = "GENERATING_MUSIC"
    COMPOSING = "COMPOSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_FOR_RATE_LIMIT = "WAITING_FOR_RATE_LIMIT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    stage: str
    status: str
    progress_percent: int
    external_provider_ids: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime


class ClipAssetResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    scene_number: int
    title: Optional[str] = None
    duration_seconds: float
    file_size: int
    stream_url: str
    resolution: Optional[str] = None
    created_at: datetime


class VoiceoverAssetResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    asset_type: str = "VOICEOVER"
    duration_seconds: float
    file_size: int
    stream_url: str
    sample_rate: Optional[int] = 24000
    narration_text: Optional[str] = None
    created_at: datetime


class MusicAssetResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    asset_type: str = "MUSIC"
    duration_seconds: float
    file_size: int
    stream_url: str
    music_mood: Optional[str] = None
    music_prompt: Optional[str] = None
    provider: Optional[str] = "ace_step"
    title: Optional[str] = None
    track_id: Optional[str] = None
    attribution: Optional[str] = None
    license: Optional[str] = None
    created_at: datetime


class MusicTrackResponse(BaseModel):
    id: str
    title: str
    category: str
    description: str
    duration_seconds: float
    audio_url: str
    preview_url: str
    attribution: str
    license: str
    available: bool
    source: Optional[str] = None
    commercial_use: Optional[bool] = False


class SelectMusicRequest(BaseModel):
    track_id: Optional[str] = None
    provider: str = "library"


class FinalVideoResponse(BaseModel):
    id: Optional[uuid.UUID] = None
    project_id: uuid.UUID
    asset_type: str = "FINAL_VIDEO"
    duration_seconds: Optional[float] = None
    resolution: Optional[str] = None
    file_size: Optional[int] = None
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    stream_url: Optional[str] = None
    download_url: Optional[str] = None
    status: str
    created_at: Optional[datetime] = None


