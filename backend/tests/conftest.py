import os
import sys

# Set testing environment variable
os.environ["TESTING"] = "true"
os.environ["ENVIRONMENT"] = "testing"

# Add backend directory to sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.database import get_db, engine, SessionLocal
from app.models.base import Base
import app.models
from app.main import app
from app.services.image_analysis_cache import ImageAnalysisCache


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    """Verify database schema is ready before tests run."""
    ImageAnalysisCache.clear()
    Base.metadata.create_all(bind=engine)
    yield
    # Clean up test rows from real postgres tables after test suite
    with engine.connect() as conn:
        try:
            conn.execute(text("DELETE FROM generated_assets WHERE project_id IN (SELECT id FROM video_projects WHERE title LIKE '%Paris%' OR title LIKE '%AI Video%')"))
            conn.execute(text("DELETE FROM project_images WHERE project_id IN (SELECT id FROM video_projects WHERE title LIKE '%Paris%' OR title LIKE '%AI Video%')"))
            conn.execute(text("DELETE FROM generation_jobs WHERE project_id IN (SELECT id FROM video_projects WHERE title LIKE '%Paris%' OR title LIKE '%AI Video%')"))
            conn.execute(text("DELETE FROM video_projects WHERE title LIKE '%Paris%' OR title LIKE '%AI Video%'"))
            conn.execute(text("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE email = 'testuser@example.com')"))
            conn.execute(text("DELETE FROM users WHERE email = 'testuser@example.com'"))
            conn.commit()
        except Exception:
            pass
    engine.dispose()


@pytest.fixture
def db_session():
    """Provide a database session for each test."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    """Provide a FastAPI TestClient with overridden database dependency."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
