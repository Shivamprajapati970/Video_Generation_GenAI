import uuid
from typing import Optional, TYPE_CHECKING
from datetime import datetime
from sqlalchemy import String, Integer, ForeignKey, Uuid, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

if TYPE_CHECKING:
    from app.models.project import VideoProject
    from app.models.image_analysis import ImageAnalysisCheckpoint


class ProjectImage(Base):
    __tablename__ = "project_images"

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
    file_path: Mapped[str] = mapped_column(
        String(512),
        nullable=False
    )
    original_filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )
    mime_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )
    file_size: Mapped[int] = mapped_column(
        Integer,
        nullable=False
    )
    width: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True
    )
    height: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True
    )
    order_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    # Relationships
    project: Mapped["VideoProject"] = relationship("VideoProject", back_populates="images")
    analysis_checkpoint: Mapped[Optional["ImageAnalysisCheckpoint"]] = relationship(
        "ImageAnalysisCheckpoint",
        back_populates="image",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<ProjectImage id={self.id} filename={self.original_filename} order={self.order_index}>"
