import uuid
from typing import Optional, Dict, Any, TYPE_CHECKING
from datetime import datetime
from sqlalchemy import String, Integer, Text, ForeignKey, Uuid, DateTime, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, CompatibleJSONB

if TYPE_CHECKING:
    from app.models.project import VideoProject
    from app.models.image import ProjectImage


class ImageAnalysisCheckpoint(Base):
    """Persistent checkpoint for individual image analysis within a project's scene planning workflow.
    
    Guarantees that once an image is successfully analyzed by Groq Vision, its result is
    persisted immediately and never re-analyzed if downstream images or scene planning
    experience rate limits or server restarts.
    """
    __tablename__ = "image_analysis_checkpoints"

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
    image_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("project_images.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    order_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0
    )
    # PENDING, IN_PROGRESS, COMPLETED, FAILED, WAITING_FOR_RATE_LIMIT
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="PENDING",
        index=True
    )
    # Structured JSON matching ImageAnalysisItem: subjects, environment, details, mood, etc.
    analysis_result: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        CompatibleJSONB,
        nullable=True
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
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )

    # Relationships
    project: Mapped["VideoProject"] = relationship("VideoProject", back_populates="analysis_checkpoints")
    image: Mapped["ProjectImage"] = relationship("ProjectImage", back_populates="analysis_checkpoint")

    __table_args__ = (
        UniqueConstraint("project_id", "image_id", name="uq_project_image_checkpoint"),
    )

    def __repr__(self) -> str:
        return f"<ImageAnalysisCheckpoint id={self.id} image_id={self.image_id} order={self.order_index} status={self.status}>"
