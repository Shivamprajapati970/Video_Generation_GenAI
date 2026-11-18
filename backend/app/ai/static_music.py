import os
import json
import wave
import logging
import re
from typing import Optional, Dict, Any, List
from pathlib import Path

from app.core.config import settings
from app.ai.base import BaseMusicProvider
from app.core.exceptions import NotFoundError, BadRequestError, ValidationError

logger = logging.getLogger("video_gen_app.static_music")

DEFAULT_CATEGORIES = ["birthday", "anniversary", "memories", "family", "cinematic", "friends"]

# Mood-to-category mapping for AI/prompt fallback matching
MOOD_CATEGORY_MAP = {
    "birthday": "birthday",
    "happy": "birthday",
    "celebration": "birthday",
    "anniversary": "anniversary",
    "romantic": "anniversary",
    "love": "anniversary",
    "memories": "memories",
    "nostalgic": "memories",
    "chill": "memories",
    "calm": "memories",
    "family": "family",
    "home": "family",
    "cinematic": "cinematic",
    "epic": "cinematic",
    "emotional": "cinematic",
    "friends": "friends",
    "party": "friends",
    "travel": "friends",
}


def _sanitize_slug(text: str) -> str:
    """Validates that an identifier contains only safe alphanumeric/hyphen/underscore chars."""
    if not text or not re.match(r"^[a-zA-Z0-9_\-]+$", text):
        raise BadRequestError(detail=f"Invalid track or category identifier: {text}")
    return text.strip()


class StaticMusicLibraryProvider(BaseMusicProvider):
    """Local royalty-free and curated music library provider.

    Reads locally stored audio tracks and metadata from catalog.json.
    Completely offline: never calls external AI models or paid inference services.
    """

    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = self._resolve_base_dir(base_dir)
        self.catalog_path = os.path.join(self.base_dir, "catalog.json")
        logger.info(f"StaticMusicLibraryProvider initialized with base directory: {self.base_dir}")

    def _resolve_base_dir(self, explicit_dir: Optional[str] = None) -> str:
        """Dynamically finds the media/music library directory across development and root environments."""
        candidates = []
        if explicit_dir:
            candidates.append(explicit_dir)

        # Configured or default relative paths
        configured_dir = getattr(settings, "MUSIC_LIBRARY_DIR", "media/music")
        candidates.extend([
            configured_dir,
            os.path.join("backend", configured_dir),
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "media", "music"),
            "backend/media/music",
            "media/music",
        ])

        for c in candidates:
            abs_c = os.path.abspath(c)
            if os.path.isdir(abs_c):
                return abs_c

        # Fallback to backend/media/music created on startup
        fallback = os.path.abspath("backend/media/music")
        os.makedirs(fallback, exist_ok=True)
        return fallback

    def _load_catalog(self) -> List[Dict[str, Any]]:
        """Loads and parses the library catalog registry."""
        if not os.path.isfile(self.catalog_path):
            logger.warning(f"Catalog file not found at {self.catalog_path}")
            return []
        try:
            with open(self.catalog_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception as exc:
            logger.error(f"Failed to read catalog from {self.catalog_path}: {exc}")
            return []

    def _validate_safe_path(self, target_path: str) -> str:
        """Path traversal defense: ensures target_path strictly resides within self.base_dir."""
        resolved = os.path.abspath(target_path)
        base = os.path.abspath(self.base_dir)
        if not resolved.startswith(base) or os.path.commonpath([base, resolved]) != base:
            logger.warning(f"Blocked path traversal attempt: target='{target_path}' base='{base}'")
            raise BadRequestError(detail="Access denied: path traversal detected")
        return resolved

    def _probe_audio(self, file_path: str) -> Dict[str, Any]:
        """Probes audio file to extract actual duration, sample rate, channels and size."""
        if not os.path.isfile(file_path):
            return {
                "available": False,
                "duration_seconds": 0.0,
                "file_size": 0,
                "sample_rate": 44100,
                "channels": 2,
                "format": "wav",
            }

        size = os.path.getsize(file_path)
        if size <= 44:
            return {
                "available": False,
                "duration_seconds": 0.0,
                "file_size": size,
                "sample_rate": 44100,
                "channels": 2,
                "format": "wav",
            }

        duration = 0.0
        sample_rate = 44100
        channels = 2
        ext = os.path.splitext(file_path)[1].lower().lstrip(".")

        if ext == "wav":
            try:
                with wave.open(file_path, "rb") as w:
                    rate = w.getframerate()
                    frames = w.getnframes()
                    channels = w.getnchannels()
                    sample_rate = rate
                    duration = round(frames / float(rate), 2) if rate > 0 else 0.0
            except Exception:
                duration = round(size / (44100 * 2 * 2), 2)
        elif ext == "mp3":
            # Estimate MP3 duration by typical 192kbps bitrate if wave parser is not applicable
            duration = max(1.0, round(size / (24000), 2))

        return {
            "available": True,
            "duration_seconds": duration,
            "file_size": size,
            "sample_rate": sample_rate,
            "channels": channels,
            "format": ext,
        }

    def list_categories(self) -> List[str]:
        """Lists available music library categories."""
        catalog = self._load_catalog()
        categories = set(DEFAULT_CATEGORIES)
        for item in catalog:
            cat = item.get("category")
            if cat:
                categories.add(cat.lower().strip())
        return sorted(list(categories))

    def list_tracks(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lists all registered tracks, optionally filtered by category, enriched with physical availability."""
        catalog = self._load_catalog()
        cat_filter = category.lower().strip() if category else None
        if cat_filter:
            _sanitize_slug(cat_filter)

        results = []
        for entry in catalog:
            entry_cat = entry.get("category", "").lower().strip()
            if cat_filter and entry_cat != cat_filter:
                continue

            track_id = entry.get("id")
            filename = entry.get("filename")
            if not track_id or not filename:
                continue

            raw_path = os.path.join(self.base_dir, entry_cat, filename)
            try:
                safe_path = self._validate_safe_path(raw_path)
                probe = self._probe_audio(safe_path)
            except Exception:
                safe_path = raw_path
                probe = {"available": False, "duration_seconds": 0.0, "file_size": 0}

            # Calculate relative path from repository root
            try:
                rel_path = os.path.relpath(safe_path, start=".").replace("\\", "/")
            except ValueError:
                rel_path = safe_path.replace("\\", "/")

            results.append({
                "id": track_id,
                "title": entry.get("title", track_id.replace("_", " ").title()),
                "category": entry_cat,
                "description": entry.get("description", ""),
                "duration_seconds": probe["duration_seconds"],
                "file_size": probe["file_size"],
                "audio_url": f"/api/v1/music/tracks/{track_id}/stream",
                "preview_url": f"/api/v1/music/tracks/{track_id}/stream",
                "attribution": entry.get("attribution", "VideoGen AI Music Library"),
                "license": entry.get("license", "Synthetic Development Track"),
                "source": entry.get("source"),
                "commercial_use": entry.get("commercial_use", False),
                "restrictions": entry.get("restrictions", ""),
                "available": probe["available"],
                "file_path": safe_path,
                "relative_path": rel_path,
            })

        return results

    def get_track(self, track_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves details of a single track by its unique ID with traversal protection."""
        _sanitize_slug(track_id)
        tracks = self.list_tracks()
        for t in tracks:
            if t["id"] == track_id:
                return t
        return None

    def get_track_file_path(self, track_id: str) -> str:
        """Retrieves and verifies the physical file path for a track, raising appropriate exceptions."""
        track = self.get_track(track_id)
        if not track:
            raise NotFoundError(detail=f"Music track '{track_id}' not found in library")

        file_path = track.get("file_path")
        if not file_path or not track.get("available"):
            raise BadRequestError(detail=f"Audio file for track '{track_id}' is not available on disk")

        return self._validate_safe_path(file_path)

    async def generate_music(
        self,
        mood: str,
        custom_prompt: str = "",
        duration_seconds: int = 15,
    ) -> str:
        """BaseMusicProvider interface implementation: selects a matching local library track for the requested mood."""
        mood_clean = (mood or "cinematic").lower().strip()
        target_category = MOOD_CATEGORY_MAP.get(mood_clean, "cinematic")

        tracks = self.list_tracks(category=target_category)
        available_tracks = [t for t in tracks if t.get("available")]

        # If no tracks in preferred category, try all available tracks
        if not available_tracks:
            all_tracks = self.list_tracks()
            available_tracks = [t for t in all_tracks if t.get("available")]

        if not available_tracks:
            raise NotFoundError(detail="No available audio tracks found in local static music library")

        selected = available_tracks[0]
        logger.info(
            f"Selected library track '{selected['id']}' for mood='{mood}' (category='{selected['category']}')"
        )
        return selected["file_path"]


def get_static_music_provider() -> StaticMusicLibraryProvider:
    """Dependency provider for StaticMusicLibraryProvider."""
    return StaticMusicLibraryProvider()
