import os
from pathlib import Path
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

# Resolve paths to backend/.env and root .env dynamically
_current_file = Path(__file__).resolve()
_backend_dir = _current_file.parent.parent.parent
_root_dir = _backend_dir.parent

_env_candidates = [
    _backend_dir / ".env",
    _root_dir / ".env",
    Path(".env"),
    Path("backend/.env"),
]

for _candidate in _env_candidates:
    if _candidate.is_file():
        load_dotenv(str(_candidate), override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=tuple(str(p) for p in _env_candidates if p.is_file()) or ".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # App
    PROJECT_NAME: str = "AI Video Generation API"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    SECRET_KEY: str = "dev-secret-key-32-characters-minimum-length-for-security-check"

    # Server & CORS
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    FRONTEND_URL: str = "http://localhost:3000"
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
    ]

    # Database URLs (PostgreSQL on local machine with verified credentials)
    DATABASE_URL: str = "postgresql+asyncpg://postgres:1234@localhost:5432/video_gen_db"
    SYNC_DATABASE_URL: str = "postgresql+psycopg2://postgres:1234@localhost:5432/video_gen_db"

    # Redis & Celery
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    # Session Cookies (Server-side session auth)
    SESSION_COOKIE_NAME: str = "video_gen_session"
    SESSION_MAX_AGE_SECONDS: int = 604800  # 7 days
    SESSION_SECURE_COOKIE: bool = False
    SESSION_COOKIE_SAMESITE: str = "lax"

    # Free Hosted AI Providers (No paid APIs)
    GROQ_API_KEY: str = ""
    GROQ_VISION_MODEL: str = "qwen/qwen3.8-27b"
    GROQ_FALLBACK_MODEL: Optional[str] = None
    GROQ_VISION_BATCH_SIZE: int = 3  # Default 3+2 batching (max 3 images per request), configurable to 1 for smaller batches
    GROQ_MAX_RETRIES: int = 10  # Maximum rate limit recovery retry attempts per planning session
    GROQ_INITIAL_BACKOFF_SECONDS: float = 5.0  # Initial exponential backoff when retry-after header is omitted
    GROQ_MAX_BACKOFF_SECONDS: float = 60.0  # Ceiling for exponential backoff
    GROQ_BACKOFF_FACTOR: float = 2.0  # Multiplier for exponential backoff

    # Free Hugging Face Spaces & Public Demos
    HF_TOKEN: str = ""  # Optional free HF user token to raise rate limits
    HF_WAN_SPACE: str = "zerogpu-aoti/wan2-2-fp8da-aoti-faster"
    HF_CHATTERBOX_SPACE: str = "ResembleAI/Chatterbox"
    HF_ACESTEP_SPACE: str = "ACE-Studio/ACE-Step"

    # Storage
    STORAGE_TYPE: str = "local"
    UPLOAD_DIR: str = "storage/uploads"
    OUTPUT_DIR: str = "storage/outputs"
    MAX_UPLOAD_SIZE_MB: int = 25
    MAX_IMAGES_PER_PROJECT: int = 5

    # S3 (Optional)
    S3_BUCKET_NAME: str = ""
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"

    @property
    def is_testing(self) -> bool:
        return self.ENVIRONMENT.lower() == "testing" or os.getenv("TESTING", "").lower() == "true"


settings = Settings()
