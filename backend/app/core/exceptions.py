from typing import Any, Optional, Dict
from fastapi import HTTPException, status


class AppError(HTTPException):
    def __init__(
        self,
        status_code: int,
        detail: str,
        code: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ):
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code or "INTERNAL_ERROR"


class AuthenticationError(AppError):
    def __init__(self, detail: str = "Invalid authentication credentials"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            code="AUTHENTICATION_FAILED",
            headers={"WWW-Authenticate": "Cookie"}
        )


class SessionExpiredError(AppError):
    def __init__(self, detail: str = "Session has expired"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            code="SESSION_EXPIRED"
        )


class PermissionDeniedError(AppError):
    def __init__(self, detail: str = "Access denied"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
            code="PERMISSION_DENIED"
        )


class NotFoundError(AppError):
    def __init__(self, detail: str = "Resource not found"):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=detail,
            code="NOT_FOUND"
        )


class BadRequestError(AppError):
    def __init__(self, detail: str = "Bad request"):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
            code="BAD_REQUEST"
        )


class ValidationError(AppError):
    def __init__(self, detail: str = "Validation failed"):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
            code="VALIDATION_ERROR"
        )


class ConflictError(AppError):
    def __init__(self, detail: str = "Resource conflict"):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail,
            code="CONFLICT"
        )


class ExternalProviderError(AppError):
    def __init__(self, detail: str = "External AI service provider error"):
        super().__init__(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=detail,
            code="EXTERNAL_PROVIDER_ERROR"
        )


class RateLimitError(AppError):
    def __init__(
        self,
        detail: str = "AI provider rate limit or quota exceeded. Please retry later.",
        code: str = "RATE_LIMIT_EXCEEDED",
        retry_after: Optional[int] = None,
    ):
        headers = {"Retry-After": str(int(retry_after))} if retry_after is not None else None
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail,
            code=code,
            headers=headers,
        )
        self.retry_after = retry_after


class ProviderTimeoutError(AppError):
    def __init__(self, detail: str = "AI provider request timed out. Please retry."):
        super().__init__(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=detail,
            code="PROVIDER_TIMEOUT"
        )


class ServiceUnavailableError(AppError):
    def __init__(self, detail: str = "AI service temporarily unavailable"):
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
            code="SERVICE_UNAVAILABLE"
        )
