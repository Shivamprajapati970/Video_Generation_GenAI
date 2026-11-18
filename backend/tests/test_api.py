import pytest
import os
import asyncio
from app.core.config import settings
from app.ai.groq_vision import GroqVisionProvider
from app.ai.hf_wan_video import HFWanVideoProvider
from app.ai.hf_chatterbox import HFChatterboxProvider
from app.ai.hf_acestep import HFAceStepProvider
from app.schemas.scene import ScenePlan


def test_root_endpoint(client):
    """Test root endpoint returns service info."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "version" in data
    assert data["health"] == "/api/v1/health"


def test_health_check(client):
    """Test health check returns healthy status."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


def test_register_and_me_flow(client):
    """Test registration sets session cookie and /auth/me returns user."""
    from app.core.database import SessionLocal
    from app.models.user import User
    with SessionLocal() as db:
        existing = db.query(User).filter(User.email == "testuser@example.com").first()
        if existing:
            db.delete(existing)
            db.commit()

    reg_payload = {
        "email": "testuser@example.com",
        "password": "Password123!",
        "full_name": "Test Creator"
    }
    reg_res = client.post("/api/v1/auth/register", json=reg_payload)
    assert reg_res.status_code == 201
    user_data = reg_res.json()
    assert user_data["email"] == "testuser@example.com"
    assert user_data["full_name"] == "Test Creator"
    assert "id" in user_data

    # Check session cookie was set
    assert settings.SESSION_COOKIE_NAME in reg_res.cookies

    # Test /auth/me using the session cookie automatically maintained by TestClient
    me_res = client.get("/api/v1/auth/me")
    assert me_res.status_code == 200
    me_data = me_res.json()
    assert me_data["id"] == user_data["id"]
    assert me_data["email"] == "testuser@example.com"


def test_duplicate_registration_fails(client):
    """Test registering the same email again returns 409 Conflict."""
    reg_payload = {
        "email": "testuser@example.com",
        "password": "Password123!",
        "full_name": "Duplicate User"
    }
    res = client.post("/api/v1/auth/register", json=reg_payload)
    assert res.status_code == 409
    data = res.json()
    assert data["error"]["code"] == "CONFLICT"


def test_login_and_logout_flow(client):
    """Test logging in and logging out."""
    login_payload = {
        "email": "testuser@example.com",
        "password": "Password123!"
    }
    login_res = client.post("/api/v1/auth/login", json=login_payload)
    assert login_res.status_code == 200
    assert settings.SESSION_COOKIE_NAME in login_res.cookies

    # Logout
    logout_res = client.post("/api/v1/auth/logout")
    assert logout_res.status_code == 200

    # /auth/me should now fail with 401
    me_res = client.get("/api/v1/auth/me")
    assert me_res.status_code == 401


def test_video_project_lifecycle_and_aspect_ratio(client):
    """Test full CRUD operations on VideoProject including 9:16 default and aspect ratios."""
    # Ensure test user exists and login to establish session
    client.post("/api/v1/auth/register", json={
        "email": "testuser@example.com",
        "password": "Password123!",
        "full_name": "Test Creator"
    })
    login_res = client.post("/api/v1/auth/login", json={
        "email": "testuser@example.com",
        "password": "Password123!"
    })
    assert login_res.status_code == 200

    # 1. Create project with default aspect ratio
    create_payload = {
        "title": "Summer Vacation in Paris",
        "occasion": "Travel",
        "duration_seconds": 20,
        "aspect_ratio": "9:16",
        "music_mood": "Cinematic",
        "custom_music_prompt": "Epic orchestral strings and acoustic guitar",
        "description": "Highlights of our trip to Paris in summer 2026."
    }
    create_res = client.post("/api/v1/projects", json=create_payload)
    assert create_res.status_code == 201
    project = create_res.json()
    project_id = project["id"]
    assert project["title"] == "Summer Vacation in Paris"
    assert project["occasion"] == "Travel"
    assert project["duration_seconds"] == 20
    assert project["aspect_ratio"] == "9:16"
    assert project["status"] == "DRAFT"

    # 2. List projects
    list_res = client.get("/api/v1/projects")
    assert list_res.status_code == 200
    projects = list_res.json()
    assert len(projects) >= 1
    assert any(p["id"] == project_id for p in projects)

    # 3. Get project details
    detail_res = client.get(f"/api/v1/projects/{project_id}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["id"] == project_id
    assert detail["title"] == "Summer Vacation in Paris"
    assert detail["aspect_ratio"] == "9:16"

    # 4. Update project (change to 16:9 landscape and 30s)
    update_res = client.put(f"/api/v1/projects/{project_id}", json={
        "title": "Paris Summer Trip 2026 - Extended",
        "duration_seconds": 30,
        "aspect_ratio": "16:9"
    })
    assert update_res.status_code == 200
    updated = update_res.json()
    assert updated["title"] == "Paris Summer Trip 2026 - Extended"
    assert updated["duration_seconds"] == 30
    assert updated["aspect_ratio"] == "16:9"

    # 5. Delete project
    del_res = client.delete(f"/api/v1/projects/{project_id}")
    assert del_res.status_code == 204

    # 6. Verify deleted
    get_del_res = client.get(f"/api/v1/projects/{project_id}")
    assert get_del_res.status_code == 404


def test_unauthenticated_access_blocked(client):
    """Test accessing protected project routes without session cookie fails with 401."""
    client.cookies.clear()
    res = client.get("/api/v1/projects")
    assert res.status_code == 401
    data = res.json()
    assert data["error"]["code"] == "AUTHENTICATION_FAILED"


def test_ai_provider_adapters():
    """Verify all four AI service adapters (Groq, Wan 2.2, Chatterbox, ACE-Step)."""
    # 1. Groq Vision Provider
    vision_provider = GroqVisionProvider()
    plan = asyncio.run(
        vision_provider.analyze_and_plan(
            image_paths=["test_img1.jpg", "test_img2.jpg"],
            user_description="Our wedding anniversary celebration in Rome",
            occasion="Anniversary",
            duration=15,
            aspect_ratio="9:16",
        )
    )
    assert isinstance(plan, ScenePlan)
    assert plan.occasion == "Anniversary"
    assert len(plan.scenes) == 3  # 15s = 3 scenes of 5s each
    assert plan.aspect_ratio == "9:16"
    assert plan.full_narration_script is not None
    assert plan.scenes[0].narration_text is not None

    # 2. Wan 2.2 Video Provider
    video_provider = HFWanVideoProvider()
    clip_path = asyncio.run(
        video_provider.generate_scene_clip(
            image_path="test_img1.jpg",
            prompt="Smooth slow forward push-in with warm sunlight",
            aspect_ratio="9:16",
            duration_seconds=5,
            scene_index=1,
        )
    )
    assert os.path.exists(clip_path)

    # 3. Chatterbox TTS Voiceover Provider
    tts_provider = HFChatterboxProvider()
    voiceover_path = asyncio.run(
        tts_provider.generate_voiceover(
            narration_text="Celebrating five wonderful years together in the eternal city.",
            mood="Romantic",
            target_duration=15,
        )
    )
    assert os.path.exists(voiceover_path)

    # 4. ACE-Step Music Provider
    music_provider = HFAceStepProvider()
    music_path = asyncio.run(
        music_provider.generate_music(
            mood="Romantic",
            custom_prompt="Warm acoustic guitar and delicate orchestral strings",
            duration_seconds=15,
        )
    )
    assert os.path.exists(music_path)
