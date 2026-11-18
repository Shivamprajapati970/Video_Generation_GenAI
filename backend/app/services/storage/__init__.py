from app.core.config import settings
from app.services.storage.base import BaseStorageService
from app.services.storage.local import LocalStorageService

_storage_instance: BaseStorageService = None


def get_storage_service() -> BaseStorageService:
    """Returns the configured storage service instance."""
    global _storage_instance
    if _storage_instance is None:
        if settings.STORAGE_TYPE.lower() == "local":
            _storage_instance = LocalStorageService()
        else:
            # Fallback to local
            _storage_instance = LocalStorageService()
    return _storage_instance


__all__ = ["BaseStorageService", "LocalStorageService", "get_storage_service"]
