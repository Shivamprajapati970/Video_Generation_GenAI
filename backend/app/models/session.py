import uuid
from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import String, DateTime, ForeignKey, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
        index=True
    )
    session_token: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        index=True,
        nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    ip_address: Mapped[str] = mapped_column(
        String(45),
        nullable=True
    )
    user_agent: Mapped[str] = mapped_column(
        String(255),
        nullable=True
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="sessions")

    def __repr__(self) -> str:
        return f"<Session id={self.id} user_id={self.user_id} expires_at={self.expires_at}>"
