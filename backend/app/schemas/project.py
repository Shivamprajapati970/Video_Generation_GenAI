import uuid
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict
from app.schemas.image import ImageResponse
from app.schemas.job import JobResponse


class OccasionEnum(str, Enum):
    BIRTHDAY = "Birthday"
    ANNIVERSARY = "Anniversary"
    WEDDING = "Wedding"
    TRAVEL = "Travel"
    CELEBRATION = "Celebration"
    OTHER = "Other"


class DurationEnum(int, Enum):
    DURATION_15 = 15
    DURATION_20 = 20
    DURATION_30 = 30


class AspectRatioEnum(str, Enum):
    PORTRAIT_9_16 = "9:16"
    LANDSCAPE_16_9 = "16:9"
    SQUARE_1_1 = "1:1"


class MusicMoodEnum(str, Enum):
    HAPPY = "Happy"
    ROMANTIC = "Romantic"
    EMOTIONAL = "Emotional"
    CINEMATIC = "Cinematic"
    CALM = "Calm"
    CELEBRATION = "Celebration"
    CUSTOM = "Custom prompt"


class ProjectCreate(BaseModel):
    title: str = Field(default="My AI Video", min_length=1, max_length=255)
    occasion: OccasionEnum = Field(default=OccasionEnum.CELEBRATION)
    duration_seconds: DurationEnum = Field(default=DurationEnum.DURATION_15)
    aspect_ratio: AspectRatioEnum = Field(default=AspectRatioEnum.PORTRAIT_9_16)
    music_mood: MusicMoodEnum = Field(default=MusicMoodEnum.HAPPY)
    custom_music_prompt: Optional[str] = Field(None, max_length=500)
    description: Optional[str] = Field(None, max_length=2000)


class ProjectUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    occasion: Optional[OccasionEnum] = None
    duration_seconds: Optional[DurationEnum] = None
    aspect_ratio: Optional[AspectRatioEnum] = None
    music_mood: Optional[MusicMoodEnum] = None
    custom_music_prompt: Optional[str] = None
    description: Optional[str] = None


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    occasion: str
    duration_seconds: int
    aspect_ratio: str
    music_mood: str
    custom_music_prompt: Optional[str] = None
    description: Optional[str] = None
    status: str
    image_count: int = 0
    created_at: datetime
    updated_at: datetime


class ProjectDetailResponse(ProjectResponse):
    scene_plan: Optional[Dict[str, Any]] = None
    images: List[ImageResponse] = []
    latest_job: Optional[JobResponse] = None
    final_video_url: Optional[str] = None
