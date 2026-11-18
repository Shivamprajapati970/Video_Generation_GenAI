import uuid
from datetime import datetime, timezone
from typing import Optional, Generator
from fastapi import Request, Depends
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import AuthenticationError, SessionExpiredError
from app.models.user import User
from app.models.session import Session as SessionModel


def get_current_session(
    request: Request,
    db: Session = Depends(get_db),
) -> SessionModel:
    """Retrieve and validate the active server-side session from the HttpOnly session cookie."""
    cookie_name = settings.SESSION_COOKIE_NAME
    token = request.cookies.get(cookie_name)

    if not token:
        # Also support Authorization header fallback for automated testing: Bearer <session_token>
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]

    if not token:
        raise AuthenticationError("Authentication session cookie missing")

    now = datetime.now(timezone.utc)

    # Query active session joined with user
    stmt = (
        select(SessionModel)
        .options(selectinload(SessionModel.user))
        .where(SessionModel.session_token == token)
    )
    session_obj = db.execute(stmt).scalar_one_or_none()

    if not session_obj:
        raise AuthenticationError("Invalid or expired session token")

    # Ensure timezone awareness for comparison
    expires_at = session_obj.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at <= now:
        db.delete(session_obj)
        db.commit()
        raise SessionExpiredError("Session has expired. Please log in again.")

    return session_obj


def get_current_user(
    current_session: SessionModel = Depends(get_current_session),
) -> User:
    """Retrieve the currently authenticated active user."""
    user = current_session.user
    if not user or not user.is_active:
        raise AuthenticationError("User account is inactive or disabled")
    return user


def get_optional_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Retrieve current user if authenticated, without raising an exception if not."""
    try:
        session_obj = get_current_session(request, db)
        return session_obj.user if session_obj.user.is_active else None
    except Exception:
        return None
