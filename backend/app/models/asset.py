import uuid
from typing import Optional, Dict, Any, TYPE_CHECKING
from datetime import datetime
from sqlalchemy import String, Integer, Float, Text, ForeignKey, Uuid, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, CompatibleJSONB

if TYPE_CHECKING:
    from app.models.project import VideoProject


class GeneratedAsset(Base):
    __tablename__ = "generated_assets"

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
    # CLIP, MUSIC, VOICEOVER, FINAL_VIDEO
    asset_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True
    )
    file_path: Mapped[str] = mapped_column(
        String(512),
        nullable=False
    )
    provider_url: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True
    )
    duration_seconds: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True
    )
    file_size: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True
    )
    # Stores scene info, prompt text, voiceover script, audio settings, video codecs, etc.
    meta_data: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        CompatibleJSONB,
        nullable=True,
        default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    # Relationships
    project: Mapped["VideoProject"] = relationship("VideoProject", back_populates="assets")

    def __repr__(self) -> str:
        return f"<GeneratedAsset id={self.id} type={self.asset_type} path={self.file_path}>"
