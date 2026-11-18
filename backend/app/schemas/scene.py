import uuid
from typing import List, Optional, Any
from pydantic import BaseModel, Field, field_validator, model_validator


class ImageAnalysisItem(BaseModel):
    """Semantic analysis of an individual uploaded reference photo."""
    image_id: str = Field(..., description="Database UUID of the analyzed image")
    original_filename: str = Field(..., description="Original filename")
    order_index: int = Field(default=0, description="Sequence order index")
    subjects: List[str] = Field(default_factory=list, description="Main visible subjects and objects")
    environment: str = Field(default="", description="Environment and background setting")
    details: List[str] = Field(default_factory=list, description="Important visual details")
    activities: str = Field(default="", description="Visible activities or poses")
    mood: str = Field(default="Celebratory", description="Mood and atmosphere")
    lighting_composition: str = Field(default="", description="Lighting and composition")
    cinematic_presentation: str = Field(default="", description="Suitable cinematic presentation")
    story_role: str = Field(default="", description="Possible role in the video story")

    @field_validator("subjects", "details", mode="before")
    @classmethod
    def normalize_list_fields(cls, v: Any) -> List[str]:
        if isinstance(v, str):
            parts = [s.strip() for s in v.split(",") if s.strip()]
            return parts if parts else [v.strip()]
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return []



class SceneItem(BaseModel):
    """Individual 5-second scene specifications for Wan 2.2 motion synthesis."""
    scene_number: int = Field(..., ge=1, le=6, description="1-based scene sequence number")
    title: str = Field(..., min_length=1, max_length=100, description="Scene title")
    duration_seconds: int = Field(default=5, ge=1, le=10, description="Scene duration in seconds (must be > 0)")
    reference_image_ids: List[str] = Field(
        ...,
        min_length=1,
        description="Database UUID(s) of reference photos used for this scene"
    )

    @field_validator("reference_image_ids", mode="before")
    @classmethod
    def normalize_ref_ids(cls, v: Any) -> List[str]:
        if isinstance(v, str):
            return [v.strip()]
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return []
    visual_description: str = Field(..., min_length=5, description="Detailed visual description of the scene")
    scene_objective: str = Field(..., min_length=2, description="Narrative objective of this scene")
    camera_movement: str = Field(..., min_length=2, description="Specific camera motion instruction")
    motion_prompt: str = Field(
        ...,
        min_length=5,
        description="Detailed prompt for Wan 2.2 image-to-video synthesis"
    )
    transition: str = Field(default="fade", description="Transition effect into this scene")
    generation_type: str = Field(
        default="wan_video",
        description="Video generation mode: 'wan_video' for AI video synthesis, or 'animated_still' for FFmpeg pan/zoom animation"
    )
    narration_text: str = Field(
        ...,
        min_length=1,
        description="Mandatory spoken voiceover line for this scene"
    )

    @field_validator("narration_text")
    @classmethod
    def validate_narration_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Narration text is mandatory and cannot be empty")
        return v.strip()

    @field_validator("duration_seconds")
    @classmethod
    def validate_positive_duration(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("Scene duration must be positive")
        return v


class ScenePlan(BaseModel):
    """Complete structured scene plan for AI video generation."""
    video_concept: str = Field(..., min_length=2, max_length=255, description="Overall creative concept")
    occasion: str = Field(..., description="Occasion matching user request")
    story_summary: str = Field(..., min_length=5, description="Cohesive video narrative story")
    visual_style: str = Field(..., min_length=2, description="Visual aesthetic and color grading")
    emotional_tone: str = Field(..., min_length=2, description="Emotional atmosphere")
    total_duration: int = Field(..., ge=15, le=30, description="Total video duration (15, 20, or 30s)")
    aspect_ratio: str = Field(..., description="Target aspect ratio: 9:16, 16:9, or 1:1")
    full_narration_script: str = Field(..., min_length=5, description="Complete unified voiceover narration script")
    opening_narration: str = Field(..., min_length=2, description="Opening voiceover line")
    closing_narration: str = Field(..., min_length=2, description="Closing voiceover line")
    music_mood: str = Field(default="Cinematic", description="Soundtrack mood for ACE-Step")
    background_music_prompt: Optional[str] = Field(None, description="Detailed prompt for ACE-Step music")
    unselected_images_explanation: Optional[str] = Field(
        None,
        description="Explanation of why any uploaded images were not selected for the final video scenes"
    )
    scenes: List[SceneItem] = Field(..., min_length=3, max_length=6, description="Planned list of scenes")

    @field_validator("total_duration")
    @classmethod
    def validate_total_duration(cls, v: int) -> int:
        if v not in (15, 20, 30):
            raise ValueError("Total duration must be exactly 15, 20, or 30 seconds")
        return v

    @field_validator("aspect_ratio")
    @classmethod
    def validate_aspect_ratio(cls, v: str) -> str:
        if v not in ("9:16", "16:9", "1:1"):
            raise ValueError("Aspect ratio must be one of '9:16', '16:9', or '1:1'")
        return v

    @model_validator(mode="after")
    def validate_scene_durations_sum(self) -> "ScenePlan":
        total_scene_time = sum(s.duration_seconds for s in self.scenes)
        if total_scene_time != self.total_duration:
            raise ValueError(
                f"Sum of scene durations ({total_scene_time}s) does not match total duration ({self.total_duration}s)"
            )
        if not (3 <= len(self.scenes) <= 6):
            raise ValueError(
                f"Expected between 3 and 6 scenes for a {self.total_duration}s video, got {len(self.scenes)}"
            )
        return self

    # Compatibility properties for Phase 1/5 consumers
    @property
    def total_duration_seconds(self) -> int:
        return self.total_duration

    @property
    def video_title(self) -> str:
        return self.video_concept


# Backwards compatibility alias for imports expecting ScenePromptItem
ScenePromptItem = SceneItem
