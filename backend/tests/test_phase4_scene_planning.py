import io
import os
import uuid
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from PIL import Image

from app.schemas.scene import ScenePlan, SceneItem, ImageAnalysisItem
from app.ai.groq_vision import GroqVisionProvider
from app.services.scene_planner import ScenePlanningService
from app.core.exceptions import (
    BadRequestError,
    PermissionDeniedError,
    NotFoundError,
    RateLimitError,
    ProviderTimeoutError,
    ServiceUnavailableError,
    ValidationError as AppValidationError,
)
from pydantic import ValidationError
from groq import (
    RateLimitError as GroqRateLimitError,
    APITimeoutError as GroqTimeoutError,
)
import httpx


def make_test_image(size=(100, 100), color=(50, 100, 150), fmt="JPEG") -> bytes:
    """Create genuine image bytes for upload testing."""
    buf = io.BytesIO()
    img = Image.new("RGB", size, color=color)
    img.save(buf, format=fmt)
    return buf.getvalue()


@pytest.fixture
def test_user(client):
    """Register and login primary test user."""
    email = f"planner_{uuid.uuid4().hex[:6]}@example.com"
    pwd = "Password123!"
    client.post("/api/v1/auth/register", json={
        "email": email,
        "password": pwd,
        "full_name": "Planner User"
    })
    client.post("/api/v1/auth/login", json={"email": email, "password": pwd})
    return email


@pytest.fixture
def other_user(client):
    """Register and login a second user for authorization tests."""
    email = f"other_{uuid.uuid4().hex[:6]}@example.com"
    pwd = "Password123!"
    client.post("/api/v1/auth/register", json={
        "email": email,
        "password": pwd,
        "full_name": "Other User"
    })
    client.post("/api/v1/auth/login", json={"email": email, "password": pwd})
    return email


# ---------------------------------------------------------------------------
# 1. Successful scene planning using mocked provider
# ---------------------------------------------------------------------------
def test_successful_scene_planning_mocked(client, test_user):
    """Verify complete scene planning workflow for a 15-second project."""
    proj = client.post("/api/v1/projects", json={
        "title": "Grandma's 80th Birthday",
        "occasion": "Birthday",
        "duration_seconds": 15,
        "aspect_ratio": "9:16",
        "music_mood": "Happy",
        "description": "Celebrating grandma's 80th birthday with the whole family gathered around the cake.",
    }).json()
    proj_id = proj["id"]

    # Upload 2 images
    client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[
            ("files", ("grandma1.jpg", make_test_image(color=(255, 200, 200)), "image/jpeg")),
            ("files", ("grandma2.jpg", make_test_image(color=(200, 255, 200)), "image/jpeg")),
        ]
    )

    # Call analyze endpoint
    resp = client.post(f"/api/v1/projects/{proj_id}/analyze")
    assert resp.status_code == 200
    plan = resp.json()

    # Validate high-level metadata
    assert plan["occasion"] == "Birthday"
    assert plan["total_duration"] == 15
    assert plan["aspect_ratio"] == "9:16"
    assert plan["video_concept"]
    assert plan["story_summary"]
    assert plan["visual_style"]
    assert plan["emotional_tone"]
    assert plan["full_narration_script"]
    assert plan["opening_narration"]
    assert plan["closing_narration"]

    # Validate scenes
    scenes = plan["scenes"]
    assert len(scenes) == 3  # 15s = 3 scenes
    assert sum(s["duration_seconds"] for s in scenes) == 15

    for s in scenes:
        assert s["duration_seconds"] == 5
        assert s["title"]
        assert len(s["reference_image_ids"]) >= 1
        assert s["visual_description"]
        assert s["scene_objective"]
        assert s["camera_movement"]
        assert s["motion_prompt"]
        assert s["narration_text"]
        assert len(s["narration_text"].strip()) > 0


# ---------------------------------------------------------------------------
# 2. Analysis of 5 images using 3+2 batching
# ---------------------------------------------------------------------------
def test_analysis_5_images_batching_3_plus_2(client, test_user):
    """Verify 5 images are analyzed across two batches of 3 and 2."""
    proj = client.post("/api/v1/projects", json={
        "title": "Summer Roadtrip Adventure",
        "occasion": "Travel",
        "duration_seconds": 30,
        "aspect_ratio": "16:9",
        "music_mood": "Cinematic",
        "description": "Cross-country roadtrip visiting mountains, beaches, and scenic national parks.",
    }).json()
    proj_id = proj["id"]

    # Upload 5 images
    client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[
            ("files", (f"trip_{i}.jpg", make_test_image(color=(10 * i, 20 * i, 30 * i)), "image/jpeg"))
            for i in range(1, 6)
        ]
    )

    batch_calls = []
    orig_analyze_batch = GroqVisionProvider.analyze_image_batch

    async def mock_analyze_batch(self, batch_images, user_description, occasion):
        batch_calls.append(len(batch_images))
        return await orig_analyze_batch(self, batch_images, user_description, occasion)

    with patch.object(GroqVisionProvider, "analyze_image_batch", new=mock_analyze_batch):
        resp = client.post(f"/api/v1/projects/{proj_id}/analyze")

    assert resp.status_code == 200
    plan = resp.json()

    # Verify 3+2 batching took place
    assert batch_calls == [3, 2], f"Expected batches of [3, 2], got {batch_calls}"

    # Verify 30s video has 6 scenes of 5 seconds each
    assert plan["total_duration"] == 30
    assert len(plan["scenes"]) == 6
    assert sum(s["duration_seconds"] for s in plan["scenes"]) == 30


# ---------------------------------------------------------------------------
# 3. Correct image ID and order preservation
# ---------------------------------------------------------------------------
def test_image_id_and_order_preservation(client, test_user):
    """Ensure all scene reference_image_ids point to real uploaded image database UUIDs."""
    proj = client.post("/api/v1/projects", json={
        "title": "Wedding Highlights",
        "occasion": "Wedding",
        "duration_seconds": 20,
    }).json()
    proj_id = proj["id"]

    # Upload 4 images
    up_resp = client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[
            ("files", (f"wedding_{i}.jpg", make_test_image(color=(i * 30, i * 20, 100)), "image/jpeg"))
            for i in range(1, 5)
        ]
    )
    uploaded_images = up_resp.json()
    uploaded_ids = {img["id"] for img in uploaded_images}

    resp = client.post(f"/api/v1/projects/{proj_id}/analyze")
    assert resp.status_code == 200
    plan = resp.json()

    # Every referenced image ID must belong to uploaded IDs
    for scene in plan["scenes"]:
        for ref_id in scene["reference_image_ids"]:
            assert ref_id in uploaded_ids, f"Unknown image ID {ref_id} found in scene {scene['scene_number']}"


# ---------------------------------------------------------------------------
# 4. Project ownership validation (403 for cross-user)
# ---------------------------------------------------------------------------
def test_project_ownership_validation(client, test_user, other_user):
    """Verify unauthorized users cannot analyze or view another user's scene plan."""
    # User A creates project & uploads image
    client.post("/api/v1/auth/login", json={"email": test_user, "password": "Password123!"})
    proj = client.post("/api/v1/projects", json={"title": "Private Project"}).json()
    proj_id = proj["id"]
    client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[("files", ("p1.jpg", make_test_image(), "image/jpeg"))]
    )

    # User B logs in
    client.post("/api/v1/auth/login", json={"email": other_user, "password": "Password123!"})

    # User B attempts to analyze User A's project
    r_analyze = client.post(f"/api/v1/projects/{proj_id}/analyze")
    assert r_analyze.status_code == 403

    # User B attempts to get User A's scene plan
    r_plan = client.get(f"/api/v1/projects/{proj_id}/scene-plan")
    assert r_plan.status_code == 403


# ---------------------------------------------------------------------------
# 5. Missing image rejection (400 if 0 images uploaded)
# ---------------------------------------------------------------------------
def test_missing_images_rejection(client, test_user):
    """Verify analysis request is rejected when no reference images have been uploaded."""
    proj = client.post("/api/v1/projects", json={"title": "Empty Project"}).json()
    proj_id = proj["id"]

    resp = client.post(f"/api/v1/projects/{proj_id}/analyze")
    assert resp.status_code == 400
    err_msg = resp.json()["error"]["message"]
    assert "Cannot generate scene plan without reference images" in err_msg


# ---------------------------------------------------------------------------
# 6. Missing Groq API key handling
# ---------------------------------------------------------------------------
def test_missing_groq_api_key_handling():
    """Verify provider raises clear BadRequestError when API key is missing in non-mock mode."""
    provider = GroqVisionProvider(api_key="", mock_mode=False)
    assert not provider.is_configured()

    with pytest.raises(BadRequestError) as exc_info:
        import asyncio
        asyncio.run(
            provider.analyze_image_batch(
                batch_images=[{"image_id": "test", "file_path": "fake.jpg", "original_filename": "f.jpg", "order_index": 0}],
                user_description="Test",
                occasion="Birthday"
            )
        )
    assert "Groq API key is not configured" in str(exc_info.value.detail)


# ---------------------------------------------------------------------------
# 7. Invalid AI JSON response handling
# ---------------------------------------------------------------------------
def test_invalid_ai_json_response_handling(client, test_user):
    """Verify service handles corrupted or invalid JSON from external AI provider."""
    proj = client.post("/api/v1/projects", json={"title": "Corrupted JSON Test"}).json()
    proj_id = proj["id"]
    client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[("files", ("img.jpg", make_test_image(), "image/jpeg"))]
    )

    async def mock_corrupt_synthesize(*args, **kwargs):
        raise ServiceUnavailableError("Groq AI service failed to synthesize scene plan: Invalid JSON")

    with patch.object(GroqVisionProvider, "synthesize_scene_plan", new=mock_corrupt_synthesize):
        resp = client.post(f"/api/v1/projects/{proj_id}/analyze")
    assert resp.status_code == 503
    assert "Invalid JSON" in resp.json()["error"]["message"]


# ---------------------------------------------------------------------------
# 8. Missing narration rejection
# ---------------------------------------------------------------------------
def test_missing_narration_rejection():
    """Verify SceneItem rejects empty or whitespace-only narration text."""
    with pytest.raises(ValidationError) as exc_info:
        SceneItem(
            scene_number=1,
            title="Scene 1",
            duration_seconds=5,
            reference_image_ids=["test-id"],
            visual_description="A beautiful sunset",
            scene_objective="Introduce setting",
            camera_movement="Slow pan",
            motion_prompt="Gentle camera glide",
            narration_text="",  # Empty!
        )
    assert "at least 1 character" in str(exc_info.value) or "Narration text is mandatory" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info_ws:
        SceneItem(
            scene_number=1,
            title="Scene 1",
            duration_seconds=5,
            reference_image_ids=["test-id"],
            visual_description="A beautiful sunset",
            scene_objective="Introduce setting",
            camera_movement="Slow pan",
            motion_prompt="Gentle camera glide",
            narration_text="   ",  # Whitespace only!
        )
    assert "Narration text is mandatory" in str(exc_info_ws.value)


# ---------------------------------------------------------------------------
# 9. Incorrect scene duration rejection
# ---------------------------------------------------------------------------
def test_incorrect_scene_duration_rejection():
    """Verify scenes with zero or negative durations are rejected."""
    with pytest.raises(ValidationError) as exc_info_zero:
        SceneItem(
            scene_number=1,
            title="Scene 1",
            duration_seconds=0,  # Zero duration!
            reference_image_ids=["test-id"],
            visual_description="Visual desc",
            scene_objective="Objective",
            camera_movement="Pan",
            motion_prompt="Motion",
            narration_text="Spoken line",
        )
    assert "Input should be greater than or equal to 1" in str(exc_info_zero.value) or "Scene duration must be positive" in str(exc_info_zero.value)


# ---------------------------------------------------------------------------
# 10. Total duration validation (15s=3 scenes, 20s=4 scenes, 30s=6 scenes)
# ---------------------------------------------------------------------------
def test_total_duration_validation():
    """Verify ScenePlan validates that scene count and duration sum match total duration."""
    make_scene = lambda num: SceneItem(
        scene_number=num,
        title=f"Scene {num}",
        duration_seconds=5,
        reference_image_ids=["img-id"],
        visual_description="Visual description text",
        scene_objective="Scene narrative objective",
        camera_movement="Push in",
        motion_prompt="Cinematic motion prompt",
        narration_text=f"Spoken narration text line {num}",
    )

    # Valid 15s plan (3 scenes)
    p15 = ScenePlan(
        video_concept="Concept",
        occasion="Birthday",
        story_summary="Story summary text",
        visual_style="Cinematic style",
        emotional_tone="Joyous",
        total_duration=15,
        aspect_ratio="9:16",
        full_narration_script="Full script text",
        opening_narration="Opening line",
        closing_narration="Closing line",
        scenes=[make_scene(1), make_scene(2), make_scene(3)],
    )
    assert len(p15.scenes) == 3

    # Invalid 15s plan with 4 scenes (duration sum mismatch: 20s != 15s)
    with pytest.raises(ValidationError) as exc_info:
        ScenePlan(
            video_concept="Concept",
            occasion="Birthday",
            story_summary="Story summary text",
            visual_style="Cinematic style",
            emotional_tone="Joyous",
            total_duration=15,
            aspect_ratio="9:16",
            full_narration_script="Full script text",
            opening_narration="Opening line",
            closing_narration="Closing line",
            scenes=[make_scene(1), make_scene(2), make_scene(3), make_scene(4)],
        )
    assert "Sum of scene durations" in str(exc_info.value) or "Expected exactly 3 scenes" in str(exc_info.value)

    # Invalid total duration (e.g. 25 seconds)
    with pytest.raises(ValidationError) as exc_duration:
        ScenePlan(
            video_concept="Concept",
            occasion="Birthday",
            story_summary="Story summary text",
            visual_style="Cinematic style",
            emotional_tone="Joyous",
            total_duration=25,
            aspect_ratio="9:16",
            full_narration_script="Full script text",
            opening_narration="Opening line",
            closing_narration="Closing line",
            scenes=[make_scene(i) for i in range(1, 6)],
        )
    assert "Total duration must be exactly 15, 20, or 30 seconds" in str(exc_duration.value)


# ---------------------------------------------------------------------------
# 11. Scene plan persistence in PostgreSQL video_projects.scene_plan
# ---------------------------------------------------------------------------
def test_scene_plan_persistence_postgresql(client, test_user, db_session):
    """Verify ScenePlan is persisted as JSONB in video_projects table and status updated to PLANNED."""
    from app.models.project import VideoProject

    proj = client.post("/api/v1/projects", json={
        "title": "Postgres Persistence Verification",
        "occasion": "Anniversary",
        "duration_seconds": 15,
    }).json()
    proj_id = uuid.UUID(proj["id"])

    client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[("files", ("anniv.jpg", make_test_image(), "image/jpeg"))]
    )

    resp = client.post(f"/api/v1/projects/{proj_id}/analyze")
    assert resp.status_code == 200

    # Query directly from DB
    db_session.expire_all()
    db_proj = db_session.query(VideoProject).filter(VideoProject.id == proj_id).first()
    assert db_proj is not None
    assert db_proj.status == "PLANNED"
    assert db_proj.scene_plan is not None
    assert isinstance(db_proj.scene_plan, dict)
    assert db_proj.scene_plan["occasion"] == "Anniversary"
    assert len(db_proj.scene_plan["scenes"]) == 3


# ---------------------------------------------------------------------------
# 12. Retrieving saved scene plan via GET /projects/{id}/scene-plan
# ---------------------------------------------------------------------------
def test_get_saved_scene_plan_endpoint(client, test_user):
    """Verify GET /projects/{id}/scene-plan returns 404 before planning and 200 after."""
    proj = client.post("/api/v1/projects", json={
        "title": "Retrieve Plan Endpoint Test",
        "occasion": "Celebration",
        "duration_seconds": 20,
    }).json()
    proj_id = proj["id"]

    # Before planning -> 404 Not Found
    r_before = client.get(f"/api/v1/projects/{proj_id}/scene-plan")
    assert r_before.status_code == 404

    # Upload and analyze
    client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[("files", ("c1.jpg", make_test_image(), "image/jpeg"))]
    )
    r_analyze = client.post(f"/api/v1/projects/{proj_id}/analyze")
    assert r_analyze.status_code == 200
    planned_data = r_analyze.json()

    # After planning -> 200 OK
    r_after = client.get(f"/api/v1/projects/{proj_id}/scene-plan")
    assert r_after.status_code == 200
    retrieved_data = r_after.json()
    assert retrieved_data["video_concept"] == planned_data["video_concept"]
    assert len(retrieved_data["scenes"]) == len(planned_data["scenes"])


# ---------------------------------------------------------------------------
# 13. Provider timeout handling
# ---------------------------------------------------------------------------
def test_provider_timeout_handling(client, test_user):
    """Verify Groq API timeout translates to 504 Gateway Timeout."""
    proj = client.post("/api/v1/projects", json={"title": "Timeout Test"}).json()
    proj_id = proj["id"]
    client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[("files", ("t1.jpg", make_test_image(), "image/jpeg"))]
    )

    async def mock_timeout(*args, **kwargs):
        req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
        raise GroqTimeoutError(request=req)

    with patch("app.ai.groq_vision.AsyncGroq") as mock_groq_class:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=mock_timeout)
        mock_groq_class.return_value = mock_client

        with patch.object(GroqVisionProvider, "is_configured", return_value=True):
            with patch.object(GroqVisionProvider, "mock_mode", False):
                resp = client.post(f"/api/v1/projects/{proj_id}/analyze")

    assert resp.status_code == 504
    assert "timed out" in resp.json()["error"]["message"].lower()


# ---------------------------------------------------------------------------
# 14. Groq rate-limit (429) / quota error handling
# ---------------------------------------------------------------------------
def test_groq_rate_limit_429_handling(client, test_user):
    """Verify Groq rate limit translates to 429 Too Many Requests."""
    proj = client.post("/api/v1/projects", json={"title": "Rate Limit Test"}).json()
    proj_id = proj["id"]
    client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[("files", ("rl1.jpg", make_test_image(), "image/jpeg"))]
    )

    async def mock_rate_limit(*args, **kwargs):
        req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
        resp = httpx.Response(status_code=429, request=req)
        raise GroqRateLimitError(message="Rate limit exceeded", response=resp, body=None)

    with patch("app.ai.groq_vision.AsyncGroq") as mock_groq_class:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=mock_rate_limit)
        mock_groq_class.return_value = mock_client

        with patch.object(GroqVisionProvider, "is_configured", return_value=True):
            with patch.object(GroqVisionProvider, "mock_mode", False):
                resp = client.post(f"/api/v1/projects/{proj_id}/analyze")

    assert resp.status_code == 429
    assert "rate limit" in resp.json()["error"]["message"].lower()


# ---------------------------------------------------------------------------
# 15. Five-Image Duration-Aware Representation for 15s, 20s, and 30s
# ---------------------------------------------------------------------------
def test_five_image_duration_aware_representation_15_20_30(client, test_user):
    """Verify that 5 uploaded photos are distributed appropriately across 15s, 20s, and 30s,
    preserving image IDs, sequence order, exact durations, and intentional closing scene reference."""
    for duration, expected_scenes, expected_durations in [
        (15, 5, [3, 3, 3, 3, 3]),
        (20, 5, [4, 4, 4, 4, 4]),
        (30, 6, [5, 5, 5, 5, 5, 5]),
    ]:
        proj = client.post("/api/v1/projects", json={
            "title": f"5-Image Test {duration}s",
            "occasion": "Birthday",
            "duration_seconds": duration,
        }).json()
        proj_id = proj["id"]

        # Upload 5 unique images
        up_resp = client.post(
            f"/api/v1/projects/{proj_id}/images",
            files=[
                ("files", (f"img_{i}_{duration}.jpg", make_test_image(color=(i * 40, i * 30, i * 20)), "image/jpeg"))
                for i in range(1, 6)
            ]
        )
        uploaded = up_resp.json()
        assert len(uploaded) == 5
        uploaded_ids = [img["id"] for img in uploaded]

        resp = client.post(f"/api/v1/projects/{proj_id}/analyze")
        assert resp.status_code == 200
        plan = resp.json()

        scenes = plan["scenes"]
        assert len(scenes) == expected_scenes
        assert sum(s["duration_seconds"] for s in scenes) == duration
        assert [s["duration_seconds"] for s in scenes] == expected_durations

        # Verify all 5 uploaded images appear in scenes 1-5 in exact upload order
        for i in range(5):
            assert scenes[i]["reference_image_ids"] == [uploaded_ids[i]], (
                f"Scene {i + 1} did not match uploaded image {i + 1} ID in order"
            )

        # For 30s: verify scene 6 uses an intentional closing reference (hero image uploaded_ids[0])
        if duration == 30:
            assert scenes[5]["reference_image_ids"] == [uploaded_ids[0]], (
                "30s Scene 6 should intentionally reprise the opening hero image"
            )

        # Confirm unselected_images_explanation is null because all 5 images are represented
        assert plan["unselected_images_explanation"] is None


# ---------------------------------------------------------------------------
# 16. Reuse scene plan on unrelated settings change (music_mood)
# ---------------------------------------------------------------------------
def test_reuse_scene_plan_on_music_mood_change(client, test_user):
    """Verify that changing only music_mood updates the scene plan directly without re-analyzing images."""
    proj = client.post("/api/v1/projects", json={
        "title": "Music Mood Reuse Test",
        "occasion": "Birthday",
        "duration_seconds": 15,
        "music_mood": "Happy",
    }).json()
    proj_id = proj["id"]

    client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[("files", ("photo.jpg", make_test_image(), "image/jpeg"))]
    )

    # Initial plan generation
    resp1 = client.post(f"/api/v1/projects/{proj_id}/analyze")
    assert resp1.status_code == 200
    plan1 = resp1.json()
    assert plan1["music_mood"] == "Happy"

    # Update project music_mood to Cinematic
    client.patch(f"/api/v1/projects/{proj_id}", json={"music_mood": "Cinematic"})

    # Re-analyze: should reuse the plan and update music_mood in-place
    with patch.object(GroqVisionProvider, "analyze_and_plan") as mock_analyze:
        resp2 = client.post(f"/api/v1/projects/{proj_id}/analyze")
        assert resp2.status_code == 200
        plan2 = resp2.json()
        assert plan2["music_mood"] == "Cinematic"
        assert plan2["story_summary"] == plan1["story_summary"]
        mock_analyze.assert_not_called()


