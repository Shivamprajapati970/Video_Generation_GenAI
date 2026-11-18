import uuid
from typing import List, Optional, Dict, Any, TYPE_CHECKING
from sqlalchemy import String, Integer, Text, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin, CompatibleJSONB

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.image import ProjectImage
    from app.models.job import GenerationJob
    from app.models.asset import GeneratedAsset
    from app.models.image_analysis import ImageAnalysisCheckpoint


class VideoProject(Base, TimestampMixin):
    __tablename__ = "video_projects"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
        index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="Untitled Video Project"
    )
    occasion: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="Celebration"
    )
    duration_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=15
    )
    aspect_ratio: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="9:16"
    )
    music_mood: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="Happy"
    )
    custom_music_prompt: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True
    )
    # Stored scene plan as PostgreSQL native JSONB
    scene_plan: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        CompatibleJSONB,
        nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="DRAFT"
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="projects")
    images: Mapped[List["ProjectImage"]] = relationship(
        "ProjectImage",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectImage.order_index",
        lazy="selectin"
    )
    jobs: Mapped[List["GenerationJob"]] = relationship(
        "GenerationJob",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="desc(GenerationJob.created_at)",
        lazy="selectin"
    )
    assets: Mapped[List["GeneratedAsset"]] = relationship(
        "GeneratedAsset",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin"
    )
    analysis_checkpoints: Mapped[List["ImageAnalysisCheckpoint"]] = relationship(
        "ImageAnalysisCheckpoint",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ImageAnalysisCheckpoint.order_index",
        lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<VideoProject id={self.id} title={self.title} aspect_ratio={self.aspect_ratio} status={self.status}>"
