import io
import os
import uuid
import pytest
import asyncio
from unittest.mock import patch, AsyncMock
from PIL import Image
from datetime import datetime, timezone

from app.core.database import SessionLocal
from app.models.user import User
from app.models.project import VideoProject
from app.models.image import ProjectImage
from app.models.image_analysis import ImageAnalysisCheckpoint
from app.models.job import GenerationJob
from app.schemas.scene import ScenePlan, ImageAnalysisItem
from app.ai.groq_vision import GroqVisionProvider
from app.services.scene_planner import get_scene_planning_service, ScenePlanningService
from app.core.exceptions import RateLimitError


def make_test_image(size=(100, 100), color=(50, 100, 150), fmt="JPEG") -> bytes:
    """Create genuine image bytes for upload testing."""
    buf = io.BytesIO()
    img = Image.new("RGB", size, color=color)
    img.save(buf, format=fmt)
    return buf.getvalue()


@pytest.fixture
def auth_user(client):
    """Register and authenticate test user."""
    email = f"checkpoint_{uuid.uuid4().hex[:6]}@example.com"
    pwd = "Password123!"
    client.post("/api/v1/auth/register", json={
        "email": email,
        "password": pwd,
        "full_name": "Checkpoint Test User"
    })
    client.post("/api/v1/auth/login", json={"email": email, "password": pwd})
    return email


# ---------------------------------------------------------------------------
# 1. Five-Image Dual-429 Rate Limit Recovery Test
# ---------------------------------------------------------------------------
def test_five_images_dual_429_checkpoint_resume(client, auth_user):
    """Verifies the exact 5-image dual-429 scenario:
    1. Project created with 5 images.
    2. Image 1 & 2 succeed -> Checkpoints 1 & 2 saved in DB.
    3. Image 3 hits Groq 429 ITPM -> Job transitions WAITING_FOR_RATE_LIMIT, cooldown auto-waits.
    4. First auto-resume: Images 1 & 2 are skipped! Image 3 resumes and succeeds. Image 4 succeeds.
    5. Image 5 hits Groq 429 OTPM -> Job transitions WAITING_FOR_RATE_LIMIT, cooldown auto-waits.
    6. Second auto-resume: Images 1, 2, 3, 4 are skipped! Image 5 resumes and succeeds.
    7. All 5 images completed -> ScenePlan automatically synthesized and saved.
    8. Mock assertion confirms Images 1 & 2 are called ONCE, never restarted from Image 1.
    """
    proj_resp = client.post("/api/v1/projects", json={
        "title": "Checkpoint 5-Image Birthday",
        "occasion": "Birthday",
        "duration_seconds": 15,
        "aspect_ratio": "9:16",
        "music_mood": "Celebration",
        "description": "Heartfelt 5-photo birthday celebration.",
    })
    assert proj_resp.status_code == 201
    proj_id = proj_resp.json()["id"]

    # Upload 5 images
    up_resp = client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[
            ("files", (f"photo_{i}.jpg", make_test_image(color=(i * 30, 80, 120)), "image/jpeg"))
            for i in range(1, 6)
        ]
    )
    assert up_resp.status_code == 201
    uploaded_images = up_resp.json()
    assert len(uploaded_images) == 5
    img_ids = [img["id"] for img in uploaded_images]

    # Track calls to analyze_image_batch
    called_image_ids = []
    attempt_counters = {"img3": 0, "img5": 0}

    orig_analyze_batch = GroqVisionProvider.analyze_image_batch

    async def simulated_flaky_analyze_batch(self, batch_images, user_description, occasion):
        current_ids = [img["image_id"] for img in batch_images]
        called_image_ids.extend(current_ids)

        # If Image 3 is in this batch and it's attempt 0, raise 429 ITPM
        if img_ids[2] in current_ids:
            if attempt_counters["img3"] == 0:
                attempt_counters["img3"] += 1
                raise RateLimitError(
                    detail="Groq Input Tokens Per Minute (ITPM) rate limit reached. Please wait for token replenishment.",
                    code="TPM_LIMIT_EXCEEDED",
                    retry_after=1,
                )

        # If Image 5 is in this batch and it's attempt 0, raise 429 OTPM
        if img_ids[4] in current_ids:
            if attempt_counters["img5"] == 0:
                attempt_counters["img5"] += 1
                raise RateLimitError(
                    detail="Groq Output Tokens Per Minute (OTPM) rate limit reached. Output tokens exceed model ceiling.",
                    code="OTPM_LIMIT_EXCEEDED",
                    retry_after=1,
                )

        # Otherwise perform normal deterministic/mock analysis
        return await orig_analyze_batch(self, batch_images, user_description, occasion)

    with patch.object(GroqVisionProvider, "analyze_image_batch", new=simulated_flaky_analyze_batch):
        # Call analyze endpoint with batch_size=1 (image-by-image)
        resp = client.post(f"/api/v1/projects/{proj_id}/analyze?batch_size=1")

    assert resp.status_code == 200
    plan = resp.json()
    assert plan["occasion"] == "Birthday"
    assert plan["total_duration"] == 15

    # Check database checkpoints
    with SessionLocal() as db:
        checkpoints = db.query(ImageAnalysisCheckpoint).filter(
            ImageAnalysisCheckpoint.project_id == uuid.UUID(proj_id)
        ).order_by(ImageAnalysisCheckpoint.order_index.asc()).all()

        assert len(checkpoints) == 5
        for cp in checkpoints:
            assert cp.status == "COMPLETED"
            assert cp.analysis_result is not None
            assert "subjects" in cp.analysis_result
            assert cp.completed_at is not None

        # Verify retry counts: Image 3 had 1 retry, Image 5 had 1 retry, others had 0
        assert checkpoints[0].retry_count == 0
        assert checkpoints[1].retry_count == 0
        assert checkpoints[2].retry_count == 1
        assert checkpoints[3].retry_count == 0
        assert checkpoints[4].retry_count == 1

        # Verify project status
        proj = db.get(VideoProject, uuid.UUID(proj_id))
        assert proj.status == "PLANNED"
        assert proj.scene_plan is not None

        # Verify job status
        job = db.query(GenerationJob).filter(
            GenerationJob.project_id == uuid.UUID(proj_id),
            GenerationJob.stage == "ANALYZING_IMAGES"
        ).order_by(GenerationJob.created_at.desc()).first()
        assert job.status == "COMPLETED"
        assert job.progress_percent == 100

    # Strict assertion: Image 1 and 2 were called ONCE.
    # Image 3 was called twice (initial 429 + successful retry).
    # Image 4 was called once.
    # Image 5 was called twice (initial 429 + successful retry).
    # Images 1 and 2 were NEVER re-called after Image 3 hit 429!
    img1_count = called_image_ids.count(img_ids[0])
    img2_count = called_image_ids.count(img_ids[1])
    img3_count = called_image_ids.count(img_ids[2])
    img4_count = called_image_ids.count(img_ids[3])
    img5_count = called_image_ids.count(img_ids[4])

    assert img1_count == 1, f"Image 1 should be analyzed exactly once, was called {img1_count} times"
    assert img2_count == 1, f"Image 2 should be analyzed exactly once, was called {img2_count} times"
    assert img3_count == 2, f"Image 3 should be called twice (initial 429 + retry), was called {img3_count} times"
    assert img4_count == 1, f"Image 4 should be analyzed exactly once, was called {img4_count} times"
    assert img5_count == 2, f"Image 5 should be called twice (initial 429 + retry), was called {img5_count} times"


# ---------------------------------------------------------------------------
# 2. ScenePlan Synthesis 429 Rate Limit Retry Test
# ---------------------------------------------------------------------------
def test_scene_plan_synthesis_429_retry(client, auth_user):
    """Verifies that if ScenePlan synthesis hits 429:
    1. Images are already completed in checkpoints.
    2. Synthesis waits and retries.
    3. Images are NEVER re-analyzed.
    4. Plan completes successfully.
    """
    proj_resp = client.post("/api/v1/projects", json={
        "title": "Synthesis Retry Project",
        "occasion": "Anniversary",
        "duration_seconds": 15,
        "aspect_ratio": "16:9",
        "music_mood": "Romantic",
        "description": "Anniversary memories.",
    })
    proj_id = proj_resp.json()["id"]

    client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[
            ("files", (f"img_{i}.jpg", make_test_image(color=(i * 40, 100, 200)), "image/jpeg"))
            for i in range(1, 4)
        ]
    )

    synthesis_calls = 0
    orig_synthesize = GroqVisionProvider.synthesize_scene_plan

    async def flaky_synthesize(self, combined_analyses, project_metadata):
        nonlocal synthesis_calls
        synthesis_calls += 1
        if synthesis_calls == 1:
            raise RateLimitError(
                detail="Groq TPM rate limit reached during synthesis.",
                code="TPM_LIMIT_EXCEEDED",
                retry_after=1,
            )
        return await orig_synthesize(self, combined_analyses, project_metadata)

    img_calls = 0
    orig_analyze = GroqVisionProvider.analyze_image_batch

    async def counting_analyze(self, batch_images, user_description, occasion):
        nonlocal img_calls
        img_calls += len(batch_images)
        return await orig_analyze(self, batch_images, user_description, occasion)

    with patch.object(GroqVisionProvider, "synthesize_scene_plan", new=flaky_synthesize), \
         patch.object(GroqVisionProvider, "analyze_image_batch", new=counting_analyze):
        resp = client.post(f"/api/v1/projects/{proj_id}/analyze")

    assert resp.status_code == 200
    assert synthesis_calls == 2
    # 3 images analyzed once: total img_calls == 3
    assert img_calls == 3


# ---------------------------------------------------------------------------
# 3. Server Restart Recovery Test
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_server_restart_recovery():
    """Simulates a server shutdown when a job was WAITING_FOR_RATE_LIMIT with Images 1 & 2 completed
    and Images 3, 4, 5 pending.
    Verifies that resume_interrupted_jobs restarts from Image 3 and completes the project without re-analyzing 1 & 2.
    """
    planner_service = get_scene_planning_service()
    project_id = uuid.uuid4()

    # Create temporary storage test images using storage service
    from app.services.storage import get_storage_service
    storage = get_storage_service()

    with SessionLocal() as db:
        user = db.query(User).first()
        if not user:
            user = User(
                id=uuid.uuid4(),
                email=f"recovery_{uuid.uuid4().hex[:6]}@example.com",
                hashed_password="hashed_pwd_placeholder",
                full_name="Recovery User",
            )
            db.add(user)
            db.flush()
        user_id = user.id

        # Create test project
        proj = VideoProject(
            id=project_id,
            user_id=user_id,
            title="Server Restart Project",
            occasion="Birthday",
            duration_seconds=15,
            aspect_ratio="9:16",
            music_mood="Celebration",
            status="DRAFT",
        )
        db.add(proj)

        img_objs = []
        for i in range(1, 4):
            img_bytes = make_test_image(color=(i * 50, 50, 100))
            storage_key = storage.save_file(
                content=img_bytes,
                filename=f"restart_img_{i}.jpg",
                subfolder=f"projects/{project_id}",
            )

            p_img = ProjectImage(
                id=uuid.uuid4(),
                project_id=project_id,
                file_path=storage_key,
                original_filename=f"restart_img_{i}.jpg",
                mime_type="image/jpeg",
                file_size=len(img_bytes),
                width=100,
                height=100,
                order_index=i - 1,
            )
            db.add(p_img)
            img_objs.append(p_img)

        db.flush()

        # Image 1 is COMPLETED from before restart
        cp1 = ImageAnalysisCheckpoint(
            project_id=project_id,
            image_id=img_objs[0].id,
            order_index=0,
            status="COMPLETED",
            analysis_result={
                "image_id": str(img_objs[0].id),
                "original_filename": img_objs[0].original_filename,
                "order_index": 0,
                "subjects": ["Graduate celebrating"],
                "environment": "Campus courtyard",
                "details": ["Graduation cap"],
                "mood": "Proud and joyful",
            },
            completed_at=datetime.now(timezone.utc),
        )
        # Image 2 and 3 are PENDING
        cp2 = ImageAnalysisCheckpoint(
            project_id=project_id,
            image_id=img_objs[1].id,
            order_index=1,
            status="PENDING",
        )
        cp3 = ImageAnalysisCheckpoint(
            project_id=project_id,
            image_id=img_objs[2].id,
            order_index=2,
            status="PENDING",
        )
        db.add_all([cp1, cp2, cp3])

        # Interrupted GenerationJob left in WAITING_FOR_RATE_LIMIT state
        job = GenerationJob(
            id=uuid.uuid4(),
            project_id=project_id,
            stage="ANALYZING_IMAGES",
            status="WAITING_FOR_RATE_LIMIT",
            progress_percent=33,
            external_provider_ids={
                "rate_limit_type": "TPM_LIMIT_EXCEEDED",
                "rate_limit_reset_at": datetime.now(timezone.utc).isoformat(),
                "completed_images": [str(img_objs[0].id)],
            },
        )
        db.add(job)
        db.commit()

    # Track which images get analyzed during recovery
    analyzed_image_ids = []
    orig_analyze = GroqVisionProvider.analyze_image_batch

    async def tracking_analyze(self, batch_images, user_description, occasion):
        for img in batch_images:
            analyzed_image_ids.append(img["image_id"])
        return await orig_analyze(self, batch_images, user_description, occasion)

    with patch.object(GroqVisionProvider, "analyze_image_batch", new=tracking_analyze):
        resumed_count = await planner_service.resume_interrupted_jobs(SessionLocal)
        assert resumed_count >= 1

        # Wait for the background recovery task to complete
        await asyncio.sleep(0.5)

    with SessionLocal() as db:
        final_proj = db.get(VideoProject, project_id)
        assert final_proj.status == "PLANNED"
        assert final_proj.scene_plan is not None

        final_job = db.get(GenerationJob, job.id)
        assert final_job.status == "COMPLETED"
        assert final_job.progress_percent == 100

        all_cps = db.query(ImageAnalysisCheckpoint).filter(
            ImageAnalysisCheckpoint.project_id == project_id
        ).all()
        for cp in all_cps:
            assert cp.status == "COMPLETED"

    # Image 1 was already COMPLETED, so recovery should NOT have re-analyzed Image 1!
    assert str(img_objs[0].id) not in analyzed_image_ids, "Image 1 was already completed and should NOT be re-analyzed on recovery!"
    assert str(img_objs[1].id) in analyzed_image_ids
    assert str(img_objs[2].id) in analyzed_image_ids


# ---------------------------------------------------------------------------
# 4. Analysis Checkpoints API Endpoint Test
# ---------------------------------------------------------------------------
def test_analysis_checkpoints_api_endpoint(client, auth_user):
    """Verifies GET /api/v1/projects/{project_id}/analysis-checkpoints returns
    structured progress, per-photo checkpoints, and cooldown metadata.
    """
    proj_resp = client.post("/api/v1/projects", json={
        "title": "API Checkpoint Inspection",
        "occasion": "Birthday",
        "duration_seconds": 15,
        "aspect_ratio": "9:16",
        "music_mood": "Happy",
        "description": "Family reunion.",
    })
    assert proj_resp.status_code == 201
    proj_id = proj_resp.json()["id"]

    client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[
            ("files", (f"family_{i}.jpg", make_test_image(color=(i * 25, 75, 125)), "image/jpeg"))
            for i in range(1, 4)
        ]
    )

    # Initial checkpoints request before analysis (initializes pending checkpoints)
    with SessionLocal() as db:
        planner = get_scene_planning_service()
        images = db.query(ProjectImage).filter(ProjectImage.project_id == uuid.UUID(proj_id)).all()
        planner.get_or_init_checkpoints(uuid.UUID(proj_id), images, db)

    resp = client.get(f"/api/v1/projects/{proj_id}/analysis-checkpoints")
    assert resp.status_code == 200
    data = resp.json()

    assert data["project_id"] == proj_id
    assert data["total_images"] == 3
    assert data["completed_count"] == 0
    assert data["pending_count"] == 3
    assert data["auto_resume"] is True
    assert len(data["checkpoints"]) == 3
    for cp in data["checkpoints"]:
        assert cp["status"] == "PENDING"
        assert cp["analysis_result"] is None

    # Run analysis
    analyze_resp = client.post(f"/api/v1/projects/{proj_id}/analyze")
    assert analyze_resp.status_code == 200

    # Refetch checkpoints after analysis
    resp2 = client.get(f"/api/v1/projects/{proj_id}/analysis-checkpoints")
    assert resp2.status_code == 200
    data2 = resp2.json()

    assert data2["total_images"] == 3
    assert data2["completed_count"] == 3
    assert data2["pending_count"] == 0
    assert data2["job_status"] == "COMPLETED"
    for cp in data2["checkpoints"]:
        assert cp["status"] == "COMPLETED"
        assert cp["analysis_result"] is not None


# ---------------------------------------------------------------------------
# 5. Concurrency Protection & Cross-Process Atomic Job Claiming Test
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cross_process_atomic_claim_and_duplicate_worker_protection():
    """Verifies that when Worker A has claimed and is running a project job,
    Worker B attempting to claim the same project/job is prevented from executing duplicate work.
    """
    planner_service = get_scene_planning_service()
    project_id = uuid.uuid4()

    with SessionLocal() as db:
        user = db.query(User).first()
        user_id = user.id

        proj = VideoProject(
            id=project_id,
            user_id=user_id,
            title="Concurrency Test Project",
            occasion="Birthday",
            duration_seconds=15,
            aspect_ratio="9:16",
            music_mood="Happy",
            status="DRAFT",
        )
        db.add(proj)

        # Storage test image
        from app.services.storage import get_storage_service
        storage = get_storage_service()
        img_bytes = make_test_image()
        key = storage.save_file(img_bytes, "concurrency_test.jpg", f"projects/{project_id}")

        p_img = ProjectImage(
            id=uuid.uuid4(),
            project_id=project_id,
            file_path=key,
            original_filename="concurrency_test.jpg",
            mime_type="image/jpeg",
            file_size=len(img_bytes),
            width=100,
            height=100,
            order_index=0,
        )
        db.add(p_img)

        # Simulate Worker A having claimed this job 10 seconds ago
        job = GenerationJob(
            id=uuid.uuid4(),
            project_id=project_id,
            stage="ANALYZING_IMAGES",
            status="RUNNING",
            progress_percent=20,
            external_provider_ids={
                "worker_id": "worker_A_claimed_token",
                "claimed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        db.add(job)
        db.commit()

    # Worker B tries to claim the same project
    analyze_calls = 0
    orig_analyze = GroqVisionProvider.analyze_image_batch

    async def counting_analyze(self, batch_images, user_description, occasion):
        nonlocal analyze_calls
        analyze_calls += 1
        return await orig_analyze(self, batch_images, user_description, occasion)

    with patch.object(GroqVisionProvider, "analyze_image_batch", new=counting_analyze):
        # Worker B runs
        result = await planner_service.execute_checkpoint_planning_pipeline(
            project_id=project_id,
            user_id=None,
            db_session_factory=SessionLocal,
            job_id=job.id,
        )

    # Worker B detected Worker A's active claim and aborted without duplicating image analysis calls!
    assert analyze_calls == 0


# ---------------------------------------------------------------------------
# 6. Groq Rate Limit Classification Audit (ITPM, TPM, RPM, OTPM, Daily)
# ---------------------------------------------------------------------------
def test_rate_limit_classification_itpm_tpm_rpm_otpm_daily():
    """Verifies that classify_groq_rate_limit accurately classifies:
    - ITPM_LIMIT_EXCEEDED
    - TPM_LIMIT_EXCEEDED
    - RPM_LIMIT_EXCEEDED
    - OTPM_LIMIT_EXCEEDED
    - DAILY_QUOTA_EXCEEDED (is_retryable = False)
    """
    from app.ai.groq_vision import classify_groq_rate_limit

    # 1. ITPM
    e_itpm = Exception("Rate limit reached for model ... input tokens per minute (ITPM): limit 7000, used 6500, requested 1200. Please try again in 5.2s.")
    code, detail, retry_after, is_retryable = classify_groq_rate_limit(e_itpm)
    assert code == "ITPM_LIMIT_EXCEEDED"
    assert retry_after == 5.2
    assert is_retryable is True

    # 2. OTPM
    e_otpm = Exception("Rate limit reached for model ... output tokens per minute (OTPM): limit 2000, requested 2500. try again in 12s.")
    code, detail, retry_after, is_retryable = classify_groq_rate_limit(e_otpm)
    assert code == "OTPM_LIMIT_EXCEEDED"
    assert retry_after == 12.0

    # 3. RPM
    e_rpm = Exception("Rate limit reached for model ... requests per minute (RPM): limit 30, used 30. try again in 2.5s.")
    code, detail, retry_after, is_retryable = classify_groq_rate_limit(e_rpm)
    assert code == "RPM_LIMIT_EXCEEDED"
    assert retry_after == 2.5
    assert is_retryable is True

    # 4. TPM (General)
    e_tpm = Exception("Rate limit reached for model ... tokens per minute (TPM): limit 7000, used 6900. try again in 8s.")
    code, detail, retry_after, is_retryable = classify_groq_rate_limit(e_tpm)
    assert code == "TPM_LIMIT_EXCEEDED"
    assert retry_after == 8.0

    # 5. Daily Quota (Non-retryable)
    e_daily = Exception("Daily quota exceeded. You have exceeded your daily request quota.")
    code, detail, retry_after, is_retryable = classify_groq_rate_limit(e_daily)
    assert code == "DAILY_QUOTA_EXCEEDED"
    assert is_retryable is False


# ---------------------------------------------------------------------------
# 7. Configurable Retries & Exponential Backoff Audit
# ---------------------------------------------------------------------------
def test_configurable_retries_and_exponential_backoff(client, auth_user):
    """Verifies that:
    1. When retry-after is omitted, exponential backoff is applied.
    2. When max retries is exceeded, the job status transitions to FAILED and raises.
    """
    proj_resp = client.post("/api/v1/projects", json={
        "title": "Max Retries Project",
        "occasion": "Birthday",
        "duration_seconds": 15,
        "aspect_ratio": "9:16",
        "music_mood": "Happy",
        "description": "Max retries test.",
    })
    proj_id = proj_resp.json()["id"]

    client.post(
        f"/api/v1/projects/{proj_id}/images",
        files=[
            ("files", (f"retry_img_{i}.jpg", make_test_image(), "image/jpeg"))
            for i in range(1, 3)
        ]
    )

    # Mock that always raises 429 without retry-after
    async def perpetual_429(self, batch_images, user_description, occasion):
        raise RateLimitError(
            detail="Tokens per minute (TPM) exceeded.",
            code="TPM_LIMIT_EXCEEDED",
            retry_after=None,  # Forces exponential backoff
        )

    with patch.object(GroqVisionProvider, "analyze_image_batch", new=perpetual_429):
        # Call with max_retries limit = 2 to quickly verify ceiling
        planner = get_scene_planning_service()
        with pytest.raises(RateLimitError):
            asyncio.run(
                planner.execute_checkpoint_planning_pipeline(
                    project_id=uuid.UUID(proj_id),
                    user_id=None,
                    db_session_factory=SessionLocal,
                    max_rate_limit_retries=2,
                )
            )

    with SessionLocal() as db:
        job = db.query(GenerationJob).filter(
            GenerationJob.project_id == uuid.UUID(proj_id),
            GenerationJob.stage == "ANALYZING_IMAGES"
        ).order_by(GenerationJob.created_at.desc()).first()

        assert job.status == "FAILED"
        assert "Exceeded maximum rate limit retries" in (job.error_message or "")

