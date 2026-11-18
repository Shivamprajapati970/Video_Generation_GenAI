"""FFmpeg video and audio composition package.

Provides modular command generation and execution for:
- Resolution, aspect ratio, frame rate, and pixel format normalization.
- Ordered multi-scene video concatenation.
- Dual-channel audio mixing of voiceover and background music.
- Project duration trimming and faststart optimization.
- Secure non-shell execution and decoding validation.
"""

from app.composer.ffmpeg_builder import (
    RESOLUTION_MAP,
    get_target_resolution,
    build_composition_command,
)
from app.composer.executor import (
    execute_composition,
    validate_video_file,
)

__all__ = [
    "RESOLUTION_MAP",
    "get_target_resolution",
    "build_composition_command",
    "execute_composition",
    "validate_video_file",
]
