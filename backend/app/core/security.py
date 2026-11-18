import secrets
import bcrypt


def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt with standard salt."""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8")
        )
    except Exception:
        return False


def generate_session_token() -> str:
    """Generate a secure, cryptographically random session token (256-bit URL-safe)."""
    return secrets.token_urlsafe(32)


def secure_compare(val1: str, val2: str) -> bool:
    """Constant-time string comparison to prevent timing attacks."""
    return secrets.compare_digest(val1, val2)
