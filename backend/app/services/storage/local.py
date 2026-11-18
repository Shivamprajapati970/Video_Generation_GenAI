import os
import uuid
from pathlib import Path
from app.core.config import settings
from app.services.storage.base import BaseStorageService


class LocalStorageService(BaseStorageService):
    """Local filesystem storage implementation with path traversal safeguards."""

    def __init__(self, base_dir: str = None):
        self.base_dir = os.path.abspath(base_dir or settings.UPLOAD_DIR)
        os.makedirs(self.base_dir, exist_ok=True)

    def _safe_resolve(self, storage_key: str) -> str:
        """Resolves storage_key against base_dir and verifies it cannot escape base_dir."""
        # Strip leading slashes to prevent root-relative path escaping
        clean_key = storage_key.lstrip("/\\")
        full_path = os.path.abspath(os.path.join(self.base_dir, clean_key))
        try:
            common = os.path.commonpath([self.base_dir, full_path])
        except ValueError:
            raise PermissionError("Access outside storage root is prohibited")
        if common != self.base_dir:
            raise PermissionError("Access outside storage root is prohibited")
        return full_path

    def save_file(self, content: bytes, filename: str, subfolder: str = "images") -> str:
        """Saves file content under subfolder with a unique, safe filename.

        Returns relative storage key.
        """
        # Extract extension and sanitize
        ext = os.path.splitext(filename)[1].lower()
        unique_name = f"{uuid.uuid4().hex}{ext}"
        storage_key = os.path.join(subfolder, unique_name).replace("\\", "/")

        full_path = self._safe_resolve(storage_key)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        with open(full_path, "wb") as f:
            f.write(content)

        return storage_key

    def get_full_path(self, storage_key: str) -> str:
        """Returns verified safe absolute path on disk."""
        return self._safe_resolve(storage_key)

    def read_file(self, storage_key: str) -> bytes:
        """Reads file bytes from storage."""
        full_path = self._safe_resolve(storage_key)
        if not os.path.isfile(full_path):
            raise FileNotFoundError(f"Storage file not found: {storage_key}")
        with open(full_path, "rb") as f:
            return f.read()

    def delete_file(self, storage_key: str) -> bool:
        """Deletes file from storage. Returns True if deleted, False if did not exist."""
        try:
            full_path = self._safe_resolve(storage_key)
            if os.path.isfile(full_path):
                os.remove(full_path)
                return True
            return False
        except Exception:
            return False

    def file_exists(self, storage_key: str) -> bool:
        """Returns True if file exists on disk."""
        try:
            full_path = self._safe_resolve(storage_key)
            return os.path.isfile(full_path)
        except Exception:
            return False
