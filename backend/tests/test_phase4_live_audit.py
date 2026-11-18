import os
import sys
import uuid
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
import httpx
import pytest
from PIL import Image
import io

from app.core.config import settings
from app.ai.groq_vision import GroqVisionProvider
from app.services.scene_planner import ScenePlanningService
from app.schemas.scene import ScenePlan, SceneItem, ImageAnalysisItem
from app.core.exceptions import (
    BadRequestError,
    RateLimitError,
    ProviderTimeoutError,
    ServiceUnavailableError,
)
from groq import (
    AsyncGroq,
    RateLimitError as GroqRateLimitError,
    APITimeoutError as GroqTimeoutError,
    AuthenticationError as GroqAuthError,
    NotFoundError as GroqNotFoundError,
)


def make_test_image(size=(80, 80), color=(100, 150, 200)) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", size, color=color)
    img.save(buf, format="JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Section 1: Groq Models Verification & Deprecation Audit
# ---------------------------------------------------------------------------
def test_audit_groq_model_configuration():
    """Verify configured model is qwen/qwen3.8-27b and deprecated fallback is removed."""
    provider = GroqVisionProvider()
    assert provider.model == "qwen/qwen3.8-27b", f"Expected 'qwen/qwen3.8-27b', got {provider.model}"
    # Verify llama-3.2-11b-vision-preview is NOT the active model or fallback
    assert provider.model != "llama-3.2-11b-vision-preview"
    assert provider.fallback_model != "llama-3.2-11b-vision-preview"
    assert settings.GROQ_FALLBACK_MODEL != "llama-3.2-11b-vision-preview"


# ---------------------------------------------------------------------------
# Section 2: Error Handling Audit (All 6 Failure Scenarios)
# ---------------------------------------------------------------------------

def test_audit_missing_api_key():
    """Verify missing API key raises clear BadRequestError without making network call."""
    provider = GroqVisionProvider(api_key="", mock_mode=False)
    assert not provider.is_configured()

    with pytest.raises(BadRequestError) as exc_info:
        asyncio.run(
            provider.analyze_image_batch(
                batch_images=[{"image_id": "test-uuid", "file_path": "fake.jpg", "order_index": 0}],
                user_description="Audit missing key",
                occasion="Birthday",
            )
        )
    assert "Groq API key is not configured" in str(exc_info.value.detail)


def test_audit_live_invalid_api_key():
    """Verify live Groq API rejects invalid API key with HTTP 401 AuthenticationError mapped to BadRequestError."""
    # Use real network connection with an intentionally invalid key
    client = AsyncGroq(api_key="gsk_invalid_live_audit_key_00000000000000000000")
    
    with pytest.raises(GroqAuthError) as exc_info:
        asyncio.run(client.models.list())
    assert exc_info.value.status_code == 401
    assert "Invalid API Key" in str(exc_info.value)

    # Now verify GroqVisionProvider maps GroqAuthError to BadRequestError
    provider = GroqVisionProvider(api_key="gsk_invalid_live_audit_key_00000000000000000000", mock_mode=False)
    with pytest.raises(BadRequestError) as exc_provider:
        asyncio.run(
            provider.analyze_image_batch(
                batch_images=[{"image_id": "test-uuid", "file_path": "fake.jpg", "order_index": 0}],
                user_description="Audit invalid key",
                occasion="Birthday",
            )
        )
    assert "Invalid Groq API key" in str(exc_provider.value.detail)


def test_audit_invalid_model_id():
    """Verify non-existent or decommissioned model ID raises ServiceUnavailableError."""
    provider = GroqVisionProvider(api_key="gsk_dummy_valid_format_key", model="invalid-model-id-9999", mock_mode=False)

    async def mock_call_model_not_found(*args, **kwargs):
        req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
        resp = httpx.Response(status_code=404, request=req, json={"error": {"message": "Model not found", "code": "model_not_found"}})
        raise GroqNotFoundError(message="Model not found", response=resp, body=None)

    with patch("app.ai.groq_vision.AsyncGroq") as mock_groq_class:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=mock_call_model_not_found)
        mock_groq_class.return_value = mock_client

        with pytest.raises(ServiceUnavailableError) as exc_info:
            asyncio.run(
                provider.analyze_image_batch(
                    batch_images=[{"image_id": "test-uuid", "file_path": "fake.jpg", "order_index": 0}],
                    user_description="Audit invalid model",
                    occasion="Birthday",
                )
            )
        assert "invalid or no longer available" in str(exc_info.value.detail).lower() or "not found" in str(exc_info.value.detail).lower()


def test_audit_rate_limit_429():
    """Verify Groq HTTP 429 RateLimitError is caught and mapped to RateLimitError (HTTP 429)."""
    provider = GroqVisionProvider(api_key="gsk_dummy_valid_format_key", mock_mode=False)

    async def mock_rate_limit(*args, **kwargs):
        req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
        resp = httpx.Response(status_code=429, request=req, json={"error": {"message": "Rate limit reached", "code": "rate_limit_exceeded"}})
        raise GroqRateLimitError(message="Rate limit reached", response=resp, body=None)

    with patch("app.ai.groq_vision.AsyncGroq") as mock_groq_class:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=mock_rate_limit)
        mock_groq_class.return_value = mock_client

        with pytest.raises(RateLimitError) as exc_info:
            asyncio.run(
                provider.analyze_image_batch(
                    batch_images=[{"image_id": "test-uuid", "file_path": "fake.jpg", "order_index": 0}],
                    user_description="Audit rate limit",
                    occasion="Birthday",
                )
            )
        assert exc_info.value.status_code == 429
        assert "rate limit" in str(exc_info.value.detail).lower()


def test_audit_api_timeout():
    """Verify Groq APITimeoutError is caught and mapped to ProviderTimeoutError (HTTP 504)."""
    provider = GroqVisionProvider(api_key="gsk_dummy_valid_format_key", mock_mode=False)

    async def mock_timeout(*args, **kwargs):
        req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
        raise GroqTimeoutError(request=req)

    with patch("app.ai.groq_vision.AsyncGroq") as mock_groq_class:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=mock_timeout)
        mock_groq_class.return_value = mock_client

        with pytest.raises(ProviderTimeoutError) as exc_info:
            asyncio.run(
                provider.analyze_image_batch(
                    batch_images=[{"image_id": "test-uuid", "file_path": "fake.jpg", "order_index": 0}],
                    user_description="Audit timeout",
                    occasion="Birthday",
                )
            )
        assert exc_info.value.status_code == 504
        assert "timed out" in str(exc_info.value.detail).lower()


def test_audit_invalid_json_response():
    """Verify malformed JSON from Groq triggers ServiceUnavailableError."""
    provider = GroqVisionProvider(api_key="gsk_dummy_valid_format_key", mock_mode=False)

    async def mock_corrupt_json(*args, **kwargs):
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="<<< Not JSON >>>"))]
        return mock_resp

    with patch("app.ai.groq_vision.AsyncGroq") as mock_groq_class:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=mock_corrupt_json)
        mock_groq_class.return_value = mock_client

        with pytest.raises(ServiceUnavailableError) as exc_info:
            asyncio.run(
                provider.analyze_image_batch(
                    batch_images=[{"image_id": "test-uuid", "file_path": "fake.jpg", "order_index": 0}],
                    user_description="Audit corrupt JSON",
                    occasion="Birthday",
                )
            )
        assert "valid structured json" in str(exc_info.value.detail).lower()


# ---------------------------------------------------------------------------
# Section 3: Live Image Analysis Pipeline (3+2 Batching, Reconcile, & DB)
# ---------------------------------------------------------------------------
def test_audit_5_image_pipeline_live_and_db(client, db_session):
    """Full live pipeline audit with 5 reference photos.
    
    Verifies:
    1. First batch processes 3 images.
    2. Second batch processes 2 images.
    3. Results are combined.
    4. Valid ScenePlan returned.
    5. All UUIDs match uploaded images.
    6. Narration is non-empty on every scene.
    7. Scene durations sum exactly to 30s (6 scenes of 5s each).
    8. Scene plan persisted to PostgreSQL video_projects.scene_plan (JSONB).
    """
    from app.models.project import VideoProject

    # Register user
    email = f"audit_{uuid.uuid4().hex[:6]}@example.com"
    pwd = "Password123!"
    client.post("/api/v1/auth/register", json={"email": email, "password": pwd, "full_name": "Audit User"})
    client.post("/api/v1/auth/login", json={"email": email, "password": pwd})

    # Create 30s project
    proj = client.post("/api/v1/projects", json={
        "title": "Live Audit Project",
        "occasion": "Celebration",
        "duration_seconds": 30,
        "aspect_ratio": "16:9",
        "music_mood": "Cinematic",
        "description": "Celebrating our grand milestone with friends and family.",
    }).json()
    proj_id = uuid.UUID(proj["id"])

    # Upload 5 reference photos
    up_resp = client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[
            ("files", (f"audit_photo_{i}.jpg", make_test_image(color=(i * 20, 100, 150)), "image/jpeg"))
            for i in range(1, 6)
        ]
    )
    assert up_resp.status_code == 201
    uploaded_images = up_resp.json()
    assert len(uploaded_images) == 5
    uploaded_uuids = [img["id"] for img in uploaded_images]

    # Track batch execution
    batch_tracker = []
    orig_analyze_batch = GroqVisionProvider.analyze_image_batch

    async def tracking_analyze_batch(self, batch_images, user_description, occasion):
        batch_tracker.append([img["image_id"] for img in batch_images])
        return await orig_analyze_batch(self, batch_images, user_description, occasion)

    with patch.object(GroqVisionProvider, "analyze_image_batch", new=tracking_analyze_batch):
        resp = client.post(f"/api/v1/projects/{proj_id}/analyze")

    assert resp.status_code == 200
    plan = resp.json()

    # 1. Verify First API request processes 3 images
    assert len(batch_tracker[0]) == 3, f"Expected first batch of 3, got {len(batch_tracker[0])}"
    assert batch_tracker[0] == uploaded_uuids[:3]

    # 2. Verify Second API request processes 2 images
    assert len(batch_tracker[1]) == 2, f"Expected second batch of 2, got {len(batch_tracker[1])}"
    assert batch_tracker[1] == uploaded_uuids[3:5]

    # 3. Verify total images combined
    total_batched = len(batch_tracker[0]) + len(batch_tracker[1])
    assert total_batched == 5

    # 4. Actual Scene Plan Structure
    assert plan["video_concept"]
    assert plan["occasion"] == "Celebration"
    assert plan["total_duration"] == 30
    assert plan["aspect_ratio"] == "16:9"
    assert plan["full_narration_script"]
    assert plan["opening_narration"]
    assert plan["closing_narration"]

    # 5. All reference image UUIDs are correct and present
    for scene in plan["scenes"]:
        for ref_id in scene["reference_image_ids"]:
            assert ref_id in uploaded_uuids, f"Scene referenced unknown ID: {ref_id}"

    # 6. Narration is present and non-empty
    for scene in plan["scenes"]:
        assert scene["narration_text"]
        assert len(scene["narration_text"].strip()) > 0

    # 7. Scene durations total exactly 30 seconds (6 scenes x 5s)
    assert len(plan["scenes"]) == 6
    assert sum(s["duration_seconds"] for s in plan["scenes"]) == 30
    for s in plan["scenes"]:
        assert s["duration_seconds"] == 5

    # 8. Scene plan is saved in PostgreSQL
    db_session.expire_all()
    db_proj = db_session.query(VideoProject).filter(VideoProject.id == proj_id).first()
    assert db_proj is not None
    assert db_proj.status == "PLANNED"
    assert db_proj.scene_plan is not None
    assert isinstance(db_proj.scene_plan, dict)
    assert db_proj.scene_plan["total_duration"] == 30
    assert len(db_proj.scene_plan["scenes"]) == 6
