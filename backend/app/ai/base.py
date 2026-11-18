from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from app.schemas.scene import ScenePlan


class BaseVisionProvider(ABC):
    """Abstract base class for vision understanding & scene planning."""

    @abstractmethod
    async def analyze_and_plan(
        self,
        image_paths: List[str],
        user_description: str,
        occasion: str,
        duration: int,
        aspect_ratio: str = "9:16",
    ) -> ScenePlan:
        """Analyze reference images and user prompt to generate a structured ScenePlan."""
        pass


class BaseVideoProvider(ABC):
    """Abstract base class for Image-to-Video generation (Wan 2.2)."""

    @abstractmethod
    async def generate_scene_clip(
        self,
        image_path: str,
        prompt: str,
        aspect_ratio: str = "9:16",
        duration_seconds: int = 5,
        scene_index: int = 1,
    ) -> str:
        """Generate a single video clip from a reference image and prompt. Returns saved clip path."""
        pass


class BaseTTSProvider(ABC):
    """Abstract base class for voiceover / speech synthesis (Chatterbox)."""

    @abstractmethod
    async def generate_voiceover(
        self,
        narration_text: str,
        mood: str = "cinematic",
        target_duration: Optional[int] = None,
    ) -> str:
        """Generate voiceover audio from narration text. Returns saved audio path."""
        pass


class BaseMusicProvider(ABC):
    """Abstract base class for background music generation (ACE-Step)."""

    @abstractmethod
    async def generate_music(
        self,
        mood: str,
        custom_prompt: str = "",
        duration_seconds: int = 15,
    ) -> str:
        """Generate mood-matched background music track. Returns saved audio path."""
        pass
