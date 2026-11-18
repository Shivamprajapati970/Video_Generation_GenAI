import uuid
from typing import List, TYPE_CHECKING
from sqlalchemy import String, Boolean, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.session import Session
    from app.models.project import VideoProject


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
        index=True
    )
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False
    )
    hashed_password: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )
    full_name: Mapped[str] = mapped_column(
        String(255),
        nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False
    )
    is_superuser: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False
    )

    # Relationships
    sessions: Mapped[List["Session"]] = relationship(
        "Session",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin"
    )
    projects: Mapped[List["VideoProject"]] = relationship(
        "VideoProject",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email}>"
