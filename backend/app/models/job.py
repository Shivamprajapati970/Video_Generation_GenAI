import uuid
from typing import Optional, Dict, Any, TYPE_CHECKING
from datetime import datetime
from sqlalchemy import String, Integer, Text, ForeignKey, Uuid, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, CompatibleJSONB

if TYPE_CHECKING:
    from app.models.project import VideoProject


class GenerationJob(Base):
    __tablename__ = "generation_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
        index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("video_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    # PENDING, ANALYZING_IMAGES, GENERATING_CLIPS, GENERATING_MUSIC, COMPOSING, COMPLETED, FAILED
    stage: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="PENDING",
        index=True
    )
    # QUEUED, RUNNING, COMPLETED, FAILED
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="QUEUED",
        index=True
    )
    progress_percent: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0
    )
    # Tracks { "groq_request_id": "...", "fal_video_request_ids": [...], "fal_music_request_id": "..." }
    external_provider_ids: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        CompatibleJSONB,
        nullable=True,
        default=dict
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True
    )
    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    # Relationships
    project: Mapped["VideoProject"] = relationship("VideoProject", back_populates="jobs")

    def __repr__(self) -> str:
        return f"<GenerationJob id={self.id} stage={self.stage} status={self.status} progress={self.progress_percent}%>"
