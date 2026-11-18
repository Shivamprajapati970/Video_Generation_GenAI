import os
import json
import hashlib
import logging
from typing import Optional, Dict, Any, List
from pathlib import Path
from app.schemas.scene import ImageAnalysisItem

logger = logging.getLogger(__name__)

CACHE_DIR = Path("storage/cache")
CACHE_FILE = CACHE_DIR / "image_analyses.json"


class ImageAnalysisCache:
    """Persistent and in-memory cache for Groq vision image analysis results."""

    _memory_cache: Dict[str, Dict[str, Any]] = {}
    _hits: int = 0
    _misses: int = 0

    @classmethod
    def _compute_file_hash(cls, file_path: str) -> Optional[str]:
        """Computes SHA-256 hash of image file content."""
        if not os.path.isfile(file_path):
            return None
        try:
            hasher = hashlib.sha256()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception as exc:
            logger.warning(f"Failed to hash image file {file_path}: {exc}")
            return None

    @classmethod
    def _load_disk_cache(cls) -> None:
        """Loads cached analyses from disk into memory."""
        if not CACHE_FILE.is_file():
            return
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    cls._memory_cache.update(data)
        except Exception as exc:
            logger.warning(f"Failed to load image analysis disk cache: {exc}")

    @classmethod
    def _persist_disk_cache(cls) -> None:
        """Persists memory cache to disk."""
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cls._memory_cache, f, indent=2)
        except Exception as exc:
            logger.warning(f"Failed to persist image analysis disk cache: {exc}")

    @classmethod
    def get(
        cls,
        file_path: str,
        image_id: str,
        original_filename: str,
        order_index: int,
    ) -> Optional[ImageAnalysisItem]:
        """Retrieves cached image analysis by file content hash or image ID."""
        if not cls._memory_cache:
            cls._load_disk_cache()

        file_hash = cls._compute_file_hash(file_path)
        cached_data = None

        if file_hash and file_hash in cls._memory_cache:
            cached_data = cls._memory_cache[file_hash]
        elif image_id and image_id in cls._memory_cache:
            cached_data = cls._memory_cache[image_id]

        if cached_data:
            cls._hits += 1
            logger.info(f"Cache HIT for image '{original_filename}' (hash: {file_hash[:8] if file_hash else 'id'})")
            # Re-key with current project image_id, original_filename, and order_index
            item_data = dict(cached_data)
            item_data["image_id"] = image_id
            item_data["original_filename"] = original_filename
            item_data["order_index"] = order_index
            return ImageAnalysisItem.model_validate(item_data)

        cls._misses += 1
        logger.info(f"Cache MISS for image '{original_filename}' (hash: {file_hash[:8] if file_hash else 'none'})")
        return None

    @classmethod
    def put(cls, file_path: str, analysis: ImageAnalysisItem) -> None:
        """Saves image analysis into cache by file hash and image ID."""
        data = analysis.model_dump()
        file_hash = cls._compute_file_hash(file_path)
        if file_hash:
            cls._memory_cache[file_hash] = data
        if analysis.image_id:
            cls._memory_cache[str(analysis.image_id)] = data
        cls._persist_disk_cache()

    @classmethod
    def get_stats(cls) -> Dict[str, int]:
        """Returns cache hit and miss statistics."""
        return {
            "hits": cls._hits,
            "misses": cls._misses,
            "cached_entries": len(cls._memory_cache),
        }

    @classmethod
    def reset_stats(cls) -> None:
        """Resets hit/miss counters."""
        cls._hits = 0
        cls._misses = 0

    @classmethod
    def clear(cls) -> None:
        """Clears in-memory and on-disk caches."""
        cls._memory_cache.clear()
        cls._hits = 0
        cls._misses = 0
        if CACHE_FILE.is_file():
            try:
                CACHE_FILE.unlink()
            except Exception as exc:
                logger.warning(f"Failed to remove cache file {CACHE_FILE}: {exc}")
