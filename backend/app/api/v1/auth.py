from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Response, Request, status
from sqlalchemy.orm import Session as DBSession
from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_password, verify_password, generate_session_token
from app.core.exceptions import ConflictError, AuthenticationError
from app.api.deps import get_db, get_current_user, get_current_session
from app.models.user import User
from app.models.session import Session
from app.schemas.auth import UserRegister, UserLogin, UserResponse, SessionResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])


def set_session_cookie(response: Response, session_token: str, max_age: int) -> None:
    """Set secure, HttpOnly session cookie on the outgoing response."""
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=session_token,
        max_age=max_age,
        expires=max_age,
        httponly=True,
        secure=settings.SESSION_SECURE_COOKIE,
        samesite=settings.SESSION_COOKIE_SAMESITE,
        path="/"
    )


def clear_session_cookie(response: Response) -> None:
    """Clear the session cookie from the client."""
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=settings.SESSION_SECURE_COOKIE,
        samesite=settings.SESSION_COOKIE_SAMESITE,
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(
    data: UserRegister,
    response: Response,
    request: Request,
    db: DBSession = Depends(get_db),
):
    """Register a new user account and automatically establish a session."""
    stmt = select(User).where(User.email == data.email.lower().strip())
    existing_user = db.execute(stmt).scalar_one_or_none()
    if existing_user:
        raise ConflictError(detail="An account with this email address already exists.")

    user = User(
        email=data.email.lower().strip(),
        hashed_password=hash_password(data.password),
        full_name=data.full_name,
        is_active=True,
    )
    db.add(user)
    db.flush()

    token = generate_session_token()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=settings.SESSION_MAX_AGE_SECONDS)

    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent", "")[:255]

    session_obj = Session(
        user_id=user.id,
        session_token=token,
        expires_at=expires_at,
        ip_address=client_ip,
        user_agent=user_agent
    )
    db.add(session_obj)
    db.commit()
    db.refresh(user)

    set_session_cookie(response, token, settings.SESSION_MAX_AGE_SECONDS)
    return user


@router.post("/login", response_model=SessionResponse)
def login(
    data: UserLogin,
    response: Response,
    request: Request,
    db: DBSession = Depends(get_db),
):
    """Authenticate with email and password, creating a server-side session."""
    stmt = select(User).where(User.email == data.email.lower().strip())
    user = db.execute(stmt).scalar_one_or_none()

    if not user or not verify_password(data.password, user.hashed_password):
        raise AuthenticationError("Invalid email or password.")

    if not user.is_active:
        raise AuthenticationError("This account has been deactivated.")

    token = generate_session_token()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=settings.SESSION_MAX_AGE_SECONDS)

    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent", "")[:255]

    session_obj = Session(
        user_id=user.id,
        session_token=token,
        expires_at=expires_at,
        ip_address=client_ip,
        user_agent=user_agent
    )
    db.add(session_obj)
    db.commit()
    db.refresh(session_obj)

    set_session_cookie(response, token, settings.SESSION_MAX_AGE_SECONDS)

    return SessionResponse(
        id=session_obj.id,
        user=UserResponse.model_validate(user),
        expires_at=session_obj.expires_at,
        message="Logged in successfully"
    )


@router.post("/logout")
def logout(
    response: Response,
    current_session: Session = Depends(get_current_session),
    db: DBSession = Depends(get_db),
):
    """Invalidate current session and remove session cookie."""
    db.delete(current_session)
    db.commit()

    clear_session_cookie(response)
    return {"message": "Logged out successfully"}


@router.get("/me", response_model=UserResponse)
def get_me(
    current_user: User = Depends(get_current_user),
):
    """Retrieve the current logged-in user profile."""
    return current_user
