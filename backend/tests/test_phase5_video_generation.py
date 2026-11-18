import os
import uuid
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.core.config import settings
from app.models.project import VideoProject
from app.models.image import ProjectImage
from app.models.job import GenerationJob
from app.schemas.job import JobStage, JobStatus
from app.models.asset import GeneratedAsset
from app.ai.hf_wan_video import HFWanVideoProvider
from app.services.video_generator import VideoGenerationService
from app.core.database import SessionLocal


def test_hf_wan_provider_space_host_and_mock_mode():
    """Verify Space host resolution and mock clip generation."""
    provider = HFWanVideoProvider(space_name="zerogpu-aoti/wan2-2-fp8da-aoti-faster", mock_mode=True)
    assert provider._get_space_host() == "zerogpu-aoti-wan2-2-fp8da-aoti-faster.hf.space"
    assert provider.effective_mock_mode is True


@pytest.mark.asyncio
async def test_hf_wan_provider_generate_simulated_clip(tmp_path):
    """Verify simulated clip generation produces a valid MP4 file."""
    provider = HFWanVideoProvider(mock_mode=True)
    provider.output_dir = str(tmp_path)
    
    clip_path = await provider.generate_scene_clip(
        image_path="nonexistent.jpg",
        prompt="Slow forward push-in with warm sunlight",
        aspect_ratio="9:16",
        duration_seconds=5,
        scene_index=1,
    )
    assert os.path.isfile(clip_path)
    assert os.path.getsize(clip_path) > 0


@pytest.mark.asyncio
async def test_video_generation_service_multi_scene_flow(db_session):
    """Test full VideoGenerationService lifecycle: sequential generation, asset persistence, and scene skipping."""
    from app.models.user import User
    from app.core.security import hash_password

    test_user = db_session.query(User).filter(User.email == "phase5_unit@example.com").first()
    if not test_user:
        test_user = User(
            id=uuid.uuid4(),
            email="phase5_unit@example.com",
            hashed_password=hash_password("Password123!"),
            full_name="Phase 5 Unit User"
        )
        db_session.add(test_user)
        db_session.commit()
    test_user_id = test_user.id

    # 1. Create a test project with scene plan
    project = VideoProject(
        id=uuid.uuid4(),
        user_id=test_user_id,
        title="Phase 5 Test Video Project",
        occasion="Birthday",
        duration_seconds=15,
        aspect_ratio="9:16",
        music_mood="Happy",
        scene_plan={
            "video_concept": "Phase 5 Test Celebration",
            "occasion": "Birthday",
            "story_summary": "Testing multi-scene video generation.",
            "visual_style": "Warm and cinematic",
            "emotional_tone": "Joyful",
            "total_duration": 15,
            "aspect_ratio": "9:16",
            "full_narration_script": "Celebrating wonderful memories together.",
            "opening_narration": "Happy memories.",
            "closing_narration": "Cherished moments.",
            "music_mood": "Happy",
            "scenes": [
                {
                    "scene_number": 1,
                    "title": "Scene One Joy",
                    "duration_seconds": 5,
                    "reference_image_ids": ["img-1"],
                    "visual_description": "Smiling person",
                    "scene_objective": "Opening atmosphere",
                    "camera_movement": "Slow push-in",
                    "motion_prompt": "Gentle smile and head turn",
                    "transition": "fade",
                    "narration_text": "A wonderful start."
                },
                {
                    "scene_number": 2,
                    "title": "Scene Two Wishes",
                    "duration_seconds": 5,
                    "reference_image_ids": ["img-2"],
                    "visual_description": "Birthday cake",
                    "scene_objective": "Intimate ritual",
                    "camera_movement": "Gentle zoom",
                    "motion_prompt": "Flickering candle flames",
                    "transition": "dissolve",
                    "narration_text": "Making wishes come true."
                },
                {
                    "scene_number": 3,
                    "title": "Scene Three Reflection",
                    "duration_seconds": 5,
                    "reference_image_ids": ["img-3"],
                    "visual_description": "Outdoor peaceful scenery",
                    "scene_objective": "Closing reflection",
                    "camera_movement": "Slow tilt up",
                    "motion_prompt": "Gentle breeze in hair",
                    "transition": "fade",
                    "narration_text": "Looking to the future."
                }
            ]
        }
    )
    db_session.add(project)

    # Add 3 ProjectImage records
    images = []
    dummy_files = []
    for idx in range(1, 4):
        dummy_name = f"test_photo_{idx}_{uuid.uuid4().hex[:6]}.jpg"
        dummy_full_path = os.path.join(settings.UPLOAD_DIR, dummy_name)
        with open(dummy_full_path, "wb") as f:
            f.write(b"dummy image bytes")
        dummy_files.append(dummy_full_path)

        img = ProjectImage(
            id=uuid.uuid4(),
            project_id=project.id,
            original_filename=f"photo_{idx}.jpg",
            file_path=dummy_name,
            mime_type="image/jpeg",
            file_size=10240,
            order_index=idx - 1
        )
        images.append(img)
        db_session.add(img)

    # Link scene reference_image_ids to real image UUIDs
    scenes = project.scene_plan["scenes"]
    for idx, s in enumerate(scenes):
        s["reference_image_ids"] = [str(images[idx].id)]
    project.scene_plan = dict(project.scene_plan)
    db_session.commit()

    service = VideoGenerationService()
    # Force mock mode for fast and deterministic unit testing
    service.video_provider._mock_mode_param = True

    # 2. Run generation for all scenes
    job = await service.generate_project_clips(
        project_id=project.id,
        user_id=test_user_id,
        db_session_factory=lambda: db_session
    )

    assert job.status == JobStatus.COMPLETED
    assert job.progress_percent == 100

    # 3. Verify GeneratedAsset records were created for all 3 scenes
    assets = db_session.query(GeneratedAsset).filter(GeneratedAsset.project_id == project.id).all()
    assert len(assets) == 3
    scene_numbers = [a.meta_data.get("scene_number") for a in assets]
    assert sorted(scene_numbers) == [1, 2, 3]

    # Verify each asset has valid path and metadata
    for a in assets:
        assert a.asset_type == "CLIP"
        assert a.duration_seconds == 5.0
        assert a.file_size > 0
        assert "motion_prompt" in a.meta_data
        assert "resolution" in a.meta_data

    # 4. Run generation AGAIN: should skip all 3 scenes without regenerating
    initial_asset_ids = [str(a.id) for a in assets]
    second_job = await service.generate_project_clips(
        project_id=project.id,
        user_id=test_user_id,
        db_session_factory=lambda: db_session
    )
    assert second_job.status == JobStatus.COMPLETED
    assert second_job.progress_percent == 100

    assets_after = db_session.query(GeneratedAsset).filter(GeneratedAsset.project_id == project.id).all()
    assert len(assets_after) == 3
    after_ids = [str(a.id) for a in assets_after]
    assert sorted(initial_asset_ids) == sorted(after_ids)

    # 5. Independent scene retry: force retry Scene 2 only
    retry_job = await service.generate_project_clips(
        project_id=project.id,
        user_id=test_user_id,
        force_retry_scene=2,
        db_session_factory=lambda: db_session
    )
    assert retry_job.status == JobStatus.COMPLETED

    # Verify Scene 2 was replaced with a new asset record
    assets_retry = db_session.query(GeneratedAsset).filter(GeneratedAsset.project_id == project.id).all()
    scene_2_assets = [a for a in assets_retry if a.meta_data.get("scene_number") == 2]
    assert len(scene_2_assets) >= 1

    # Cleanup test records and dummy files
    for df in dummy_files:
        if os.path.exists(df):
            try:
                os.remove(df)
            except Exception:
                pass
    db_session.query(GeneratedAsset).filter(GeneratedAsset.project_id == project.id).delete()
    db_session.query(GenerationJob).filter(GenerationJob.project_id == project.id).delete()
    db_session.query(ProjectImage).filter(ProjectImage.project_id == project.id).delete()
    db_session.query(VideoProject).filter(VideoProject.id == project.id).delete()
    db_session.commit()


def test_api_generate_clips_endpoints(client, db_session):
    """Test POST /generate-clips returns 202, GET /jobs/latest, GET /clips, and stream endpoint."""
    # 1. Register test user and create project
    client.cookies.clear()
    reg_res = client.post("/api/v1/auth/register", json={
        "email": "phase5user@example.com",
        "password": "Password123!",
        "full_name": "Phase 5 Tester"
    })
    if reg_res.status_code == 409:
        client.post("/api/v1/auth/login", json={
            "email": "phase5user@example.com",
            "password": "Password123!"
        })

    proj_res = client.post("/api/v1/projects", json={
        "title": "Phase 5 API Test Project",
        "occasion": "Celebration",
        "duration_seconds": 15,
        "aspect_ratio": "9:16",
        "music_mood": "Happy"
    })
    assert proj_res.status_code == 201
    project_id = proj_res.json()["id"]

    # 2. Attempt generating clips without scene plan: returns 400
    fail_gen = client.post(f"/api/v1/projects/{project_id}/generate-clips")
    assert fail_gen.status_code == 400

    # 3. Add scene plan to project in database
    proj_uuid = uuid.UUID(project_id)
    project_db = db_session.query(VideoProject).filter(VideoProject.id == proj_uuid).first()
    project_db.scene_plan = {
        "video_concept": "API Test",
        "total_duration": 15,
        "aspect_ratio": "9:16",
        "scenes": [
            {
                "scene_number": 1,
                "title": "Scene 1",
                "duration_seconds": 5,
                "reference_image_ids": [],
                "visual_description": "V1",
                "scene_objective": "O1",
                "camera_movement": "C1",
                "motion_prompt": "M1",
                "transition": "fade",
                "narration_text": "N1"
            }
        ]
    }
    db_session.commit()

    # 4. Trigger clip generation: returns HTTP 202 Accepted
    with patch("app.services.video_generator.VideoGenerationService.generate_project_clips") as mock_generate:
        async def dummy_generate(*args, **kwargs):
            return None
        mock_generate.side_effect = dummy_generate

        gen_res = client.post(f"/api/v1/projects/{project_id}/generate-clips")
        assert gen_res.status_code == 202
        gen_data = gen_res.json()
        assert "id" in gen_data
        assert gen_data["status"] == "QUEUED"

    # 5. Query latest job: returns JobResponse
    job_res = client.get(f"/api/v1/projects/{project_id}/jobs/latest")
    assert job_res.status_code == 200
    job_data = job_res.json()
    assert job_data["stage"] == "GENERATING_CLIPS"

    # 6. Query project clips: returns list of ClipAssetResponse
    # Create dummy video file on disk for streaming test and validation
    test_clip_abs = os.path.join(settings.OUTPUT_DIR, "clips", "test_clip_1.mp4")
    os.makedirs(os.path.dirname(test_clip_abs), exist_ok=True)
    with open(test_clip_abs, "wb") as f:
        f.write(b"A" * 1024)

    # Create an artificial GeneratedAsset in DB
    asset = GeneratedAsset(
        id=uuid.uuid4(),
        project_id=proj_uuid,
        asset_type="CLIP",
        file_path="storage/outputs/clips/test_clip_1.mp4",
        duration_seconds=5.0,
        file_size=1024,
        meta_data={
            "scene_number": 1,
            "scene_title": "Scene 1",
            "resolution": "640x832",
            "motion_prompt": "M1"
        }
    )
    db_session.add(asset)
    db_session.commit()

    clips_res = client.get(f"/api/v1/projects/{project_id}/clips")
    assert clips_res.status_code == 200
    clips_data = clips_res.json()
    assert len(clips_data) == 1
    assert clips_data[0]["scene_number"] == 1
    assert clips_data[0]["resolution"] == "640x832"
    assert clips_data[0]["stream_url"] == f"/api/v1/assets/{asset.id}/stream"

    # 7. Test video streaming endpoint GET /api/v1/assets/{id}/stream
    stream_res = client.get(f"/api/v1/assets/{asset.id}/stream")
    assert stream_res.status_code == 200
    assert stream_res.headers["content-type"] == "video/mp4"

    # Test Range request (HTTP 206 Partial Content)
    range_res = client.get(
        f"/api/v1/assets/{asset.id}/stream",
        headers={"Range": "bytes=0-499"}
    )
    assert range_res.status_code == 206
    assert range_res.headers["content-range"].startswith("bytes 0-499/")
    assert len(range_res.content) == 500

    # Cleanup
    if os.path.exists(test_clip_abs):
        os.remove(test_clip_abs)
    db_session.query(GeneratedAsset).filter(GeneratedAsset.project_id == proj_uuid).delete()
    db_session.query(GenerationJob).filter(GenerationJob.project_id == proj_uuid).delete()
    db_session.query(VideoProject).filter(VideoProject.id == proj_uuid).delete()
    db_session.commit()

