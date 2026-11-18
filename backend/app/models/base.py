import uuid
from datetime import datetime
from sqlalchemy import DateTime, func, JSON
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base model class for all SQLAlchemy entities."""
    pass


# Type helper for PostgreSQL native JSONB with fallback for SQLite tests
CompatibleJSONB = JSON().with_variant(JSONB, "postgresql")


class TimestampMixin:
    """Mixin providing created_at and updated_at datetime timestamps."""
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
