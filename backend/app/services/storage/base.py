from abc import ABC, abstractmethod


class BaseStorageService(ABC):
    """Abstract base class for storage services (Local or S3-compatible)."""

    @abstractmethod
    def save_file(self, content: bytes, filename: str, subfolder: str = "images") -> str:
        """Saves file content and returns a safe storage key/relative path."""
        pass

    @abstractmethod
    def get_full_path(self, storage_key: str) -> str:
        """Returns the resolved absolute path to the file on disk."""
        pass

    @abstractmethod
    def read_file(self, storage_key: str) -> bytes:
        """Reads and returns the byte content of the file."""
        pass

    @abstractmethod
    def delete_file(self, storage_key: str) -> bool:
        """Deletes the file from storage."""
        pass

    @abstractmethod
    def file_exists(self, storage_key: str) -> bool:
        """Returns True if the file exists in storage."""
        pass
