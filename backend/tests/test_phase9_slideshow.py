import os
import uuid
import pytest
import subprocess
import imageio_ffmpeg
from unittest.mock import patch, AsyncMock
from sqlalchemy import select

from app.core.config import settings
from app.models.user import User
from app.models.project import VideoProject
from app.models.image import ProjectImage
from app.models.job import GenerationJob
from app.models.asset import GeneratedAsset
from app.core.security import hash_password
from app.core.exceptions import BadRequestError, ServiceUnavailableError
from app.ai.ffmpeg_slideshow import FFmpegSlideshowProvider
from app.services.video_generator import VideoGenerationService
from app.composer.ffmpeg_builder import build_composition_command, get_target_resolution
from app.composer.executor import execute_composition


def _create_test_image(file_path: str, width: int = 1200, height: int = 1600):
    """Generates a small test JPEG using FFmpeg lavfi."""
    os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        exe, "-y",
        "-f", "lavfi",
        "-i", f"testsrc=size={width}x{height}:rate=1",
        "-vframes", "1",
        file_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _create_test_audio(file_path: str, duration: int = 5):
    """Generates a small test sine-wave audio file using FFmpeg lavfi."""
    os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        exe, "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency=440:sample_rate=44100:duration={duration}",
        "-c:a", "aac",
        file_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


# ---------------------------------------------------------------------------
# Test 1: Successful slideshow generation from an uploaded image
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_slideshow_successful_generation(tmp_path):
    img_path = str(tmp_path / "test_photo.jpg")
    _create_test_image(img_path)

    provider = FFmpegSlideshowProvider(output_dir=str(tmp_path))
    clip_path = await provider.generate_scene_clip(
        image_path=img_path,
        prompt="Slow forward push-in with warm cinematic light",
        aspect_ratio="9:16",
        duration_seconds=3,
        scene_index=1,
    )

    assert os.path.isfile(clip_path)
    assert os.path.getsize(clip_path) > 0
    assert "slideshow_scene_1_" in os.path.basename(clip_path)
    assert provider.provider_name == "ffmpeg_slideshow"
    assert 1 in provider.last_run_metrics
    assert provider.last_run_metrics[1]["resolution"] == "720x1280"


# ---------------------------------------------------------------------------
# Test 2: Zoom-in and zoom-out animation filters
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_slideshow_zoom_in_and_zoom_out_filters(tmp_path):
    img_path = str(tmp_path / "test_photo_zoom.jpg")
    _create_test_image(img_path)

    provider = FFmpegSlideshowProvider(output_dir=str(tmp_path))

    # Zoom in
    filter_in = provider.build_filtergraph(
        movement="zoom_in", duration_seconds=3.0, target_width=720, target_height=1280
    )
    assert "zoom+0.0015" in filter_in
    clip_in = await provider.generate_scene_clip(
        image_path=img_path,
        camera_movement="zoom_in",
        duration_seconds=2,
        scene_index=1,
    )
    assert os.path.isfile(clip_in)

    # Zoom out
    filter_out = provider.build_filtergraph(
        movement="zoom_out", duration_seconds=3.0, target_width=720, target_height=1280
    )
    assert "zoom-0.0015" in filter_out
    clip_out = await provider.generate_scene_clip(
        image_path=img_path,
        camera_movement="zoom_out",
        duration_seconds=2,
        scene_index=2,
    )
    assert os.path.isfile(clip_out)


# ---------------------------------------------------------------------------
# Test 3: Correct aspect-ratio output (9:16, 16:9, 1:1)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_slideshow_aspect_ratio_outputs(tmp_path):
    img_path = str(tmp_path / "test_ar.jpg")
    _create_test_image(img_path, 1920, 1080)

    provider = FFmpegSlideshowProvider(output_dir=str(tmp_path))

    for ratio, expected_dims in [("9:16", "720x1280"), ("16:9", "1280x720"), ("1:1", "720x720")]:
        clip = await provider.generate_scene_clip(
            image_path=img_path,
            aspect_ratio=ratio,
            duration_seconds=1,
            scene_index=1,
        )
        assert os.path.isfile(clip)
        assert provider.last_run_metrics[1]["resolution"] == expected_dims


# ---------------------------------------------------------------------------
# Test 4: Correct camera movement resolution and scene ordering
# ---------------------------------------------------------------------------
def test_slideshow_camera_movement_resolution():
    assert FFmpegSlideshowProvider.resolve_camera_movement("Smooth pull-back establishing shot", 1) == "zoom_out"
    assert FFmpegSlideshowProvider.resolve_camera_movement("Gentle upward crane tilt", 1) == "tilt_up"
    assert FFmpegSlideshowProvider.resolve_camera_movement("Tracking pan left across room", 1) == "pan_left"
    assert FFmpegSlideshowProvider.resolve_camera_movement("Slow pan right", 1) == "pan_right"
    assert FFmpegSlideshowProvider.resolve_camera_movement("Close-up slow push-in", 1) == "zoom_in"
    # Fallback cycling based on scene_index
    assert FFmpegSlideshowProvider.resolve_camera_movement(None, 1) == "zoom_in"
    assert FFmpegSlideshowProvider.resolve_camera_movement(None, 2) == "pan_left"
    assert FFmpegSlideshowProvider.resolve_camera_movement(None, 3) == "zoom_out"
    assert FFmpegSlideshowProvider.resolve_camera_movement(None, 4) == "pan_right"


# ---------------------------------------------------------------------------
# Test 5: Requested duration limits
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_slideshow_duration_limits(tmp_path):
    img_path = str(tmp_path / "test_dur.jpg")
    _create_test_image(img_path)

    provider = FFmpegSlideshowProvider(output_dir=str(tmp_path))

    # Reject invalid duration <= 0
    with pytest.raises(BadRequestError):
        await provider.generate_scene_clip(image_path=img_path, duration_seconds=0)

    # Valid duration
    clip_path = await provider.generate_scene_clip(image_path=img_path, duration_seconds=3)
    assert os.path.isfile(clip_path)
    assert provider.last_run_metrics[1]["duration_seconds"] == 3


# ---------------------------------------------------------------------------
# Test 6 & 7: GeneratedAsset database persistence & Job status updates
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_slideshow_asset_db_persistence_and_job_status(db_session, tmp_path):
    test_user = db_session.query(User).filter(User.email == "phase9_user@example.com").first()
    if not test_user:
        test_user = User(
            id=uuid.uuid4(),
            email="phase9_user@example.com",
            hashed_password=hash_password("Password123!"),
            full_name="Phase 9 User",
        )
        db_session.add(test_user)
        db_session.commit()

    img_file = str(tmp_path / "scene_img.jpg")
    _create_test_image(img_file)

    project = VideoProject(
        id=uuid.uuid4(),
        user_id=test_user.id,
        title="Phase 9 Persistence Test",
        occasion="Celebration",
        duration_seconds=15,
        aspect_ratio="9:16",
        scene_plan={
            "scenes": [
                {
                    "scene_number": 1,
                    "title": "Scene 1",
                    "duration_seconds": 5,
                    "visual_description": "Family celebration",
                    "camera_movement": "zoom_in",
                    "generation_type": "animated_still",
                    "reference_image_ids": [],
                }
            ]
        },
    )
    db_session.add(project)
    db_session.flush()

    p_img = ProjectImage(
        id=uuid.uuid4(),
        project_id=project.id,
        file_path=img_file,
        original_filename="scene_img.jpg",
        mime_type="image/jpeg",
        file_size=os.path.getsize(img_file),
        order_index=0,
    )
    db_session.add(p_img)
    db_session.commit()

    slideshow_provider = FFmpegSlideshowProvider(output_dir=str(tmp_path))
    service = VideoGenerationService(slideshow_provider=slideshow_provider)

    job = await service.generate_project_clips(
        project_id=project.id,
        user_id=test_user.id,
        db_session_factory=lambda: db_session,
        force_slideshow=True,
    )

    # Job assertions
    assert job.status == "COMPLETED"
    assert job.progress_percent == 100
    assert job.external_provider_ids.get("providers", {}).get("1") == "ffmpeg_slideshow"

    # Asset assertions
    assets = db_session.execute(
        select(GeneratedAsset).where(GeneratedAsset.project_id == project.id)
    ).scalars().all()
    assert len(assets) == 1
    asset = assets[0]
    assert asset.asset_type == "CLIP"
    assert asset.meta_data.get("provider") == "ffmpeg_slideshow"
    assert asset.meta_data.get("fallback_used") is True
    assert asset.duration_seconds == 5.0
    assert asset.file_size > 0


# ---------------------------------------------------------------------------
# Test 8: Wan failure triggers slideshow fallback for recognized provider failures
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_wan_failure_triggers_slideshow_fallback(db_session, tmp_path):
    test_user = db_session.query(User).filter(User.email == "phase9_user@example.com").first()

    img_file = str(tmp_path / "fallback_img.jpg")
    _create_test_image(img_file)

    project = VideoProject(
        id=uuid.uuid4(),
        user_id=test_user.id,
        title="Phase 9 Wan Fallback Test",
        occasion="Birthday",
        duration_seconds=15,
        aspect_ratio="9:16",
        scene_plan={
            "scenes": [
                {
                    "scene_number": 1,
                    "title": "Scene 1",
                    "duration_seconds": 5,
                    "visual_description": "Birthday cake",
                    "camera_movement": "pan_left",
                    "generation_type": "wan_video",
                    "reference_image_ids": [],
                }
            ]
        },
    )
    db_session.add(project)
    db_session.flush()

    p_img = ProjectImage(
        id=uuid.uuid4(),
        project_id=project.id,
        file_path=img_file,
        original_filename="fallback_img.jpg",
        mime_type="image/jpeg",
        file_size=os.path.getsize(img_file),
        order_index=0,
    )
    db_session.add(p_img)
    db_session.commit()

    # Mock Wan provider that fails with ServiceUnavailableError (e.g. ZeroGPU quota exhausted)
    mock_wan = AsyncMock()
    mock_wan.generate_scene_clip.side_effect = ServiceUnavailableError(
        detail="HuggingFace ZeroGPU quota exhausted on account"
    )

    slideshow_provider = FFmpegSlideshowProvider(output_dir=str(tmp_path))
    service = VideoGenerationService(video_provider=mock_wan, slideshow_provider=slideshow_provider)

    job = await service.generate_project_clips(
        project_id=project.id,
        user_id=test_user.id,
        db_session_factory=lambda: db_session,
    )

    # Job completed due to successful fallback
    assert job.status == "COMPLETED"
    assert job.external_provider_ids.get("fallback_scenes") == [1]
    assert "quota exhausted" in job.external_provider_ids.get("wan_errors", {}).get("1", "")

    # Asset metadata records fallback details
    asset = db_session.execute(
        select(GeneratedAsset).where(GeneratedAsset.project_id == project.id)
    ).scalars().first()
    assert asset is not None
    assert asset.meta_data.get("provider") == "ffmpeg_slideshow"
    assert asset.meta_data.get("fallback_used") is True
    assert "quota exhausted" in asset.meta_data.get("original_wan_error", "")


# ---------------------------------------------------------------------------
# Test 9: No fallback for unrelated validation or programming errors
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_fallback_for_unrelated_validation_errors(db_session, tmp_path):
    test_user = db_session.query(User).filter(User.email == "phase9_user@example.com").first()

    project = VideoProject(
        id=uuid.uuid4(),
        user_id=test_user.id,
        title="Phase 9 Validation Error Test",
        occasion="Birthday",
        duration_seconds=15,
        aspect_ratio="9:16",
        scene_plan={
            "scenes": [
                {
                    "scene_number": 1,
                    "title": "Scene 1",
                    "duration_seconds": 5,
                    "visual_description": "Nonexistent image",
                    "camera_movement": "zoom_in",
                    "generation_type": "wan_video",
                    "reference_image_ids": [str(uuid.uuid4())],
                }
            ]
        },
    )
    db_session.add(project)
    db_session.commit()

    # Note: No ProjectImage added to database!
    slideshow_provider = FFmpegSlideshowProvider(output_dir=str(tmp_path))
    service = VideoGenerationService(slideshow_provider=slideshow_provider)

    job = await service.generate_project_clips(
        project_id=project.id,
        user_id=test_user.id,
        db_session_factory=lambda: db_session,
    )

    # Should FAIL with BadRequestError (image not found), NOT trigger fallback
    assert job.status == "FAILED"
    assert "No valid image file found" in (job.error_message or "")


# ---------------------------------------------------------------------------
# Test 10: Existing successful Wan generation does not trigger fallback
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_successful_wan_does_not_trigger_fallback(db_session, tmp_path):
    test_user = db_session.query(User).filter(User.email == "phase9_user@example.com").first()

    img_file = str(tmp_path / "wan_success_img.jpg")
    _create_test_image(img_file)
    mock_wan_clip = str(tmp_path / "mock_wan_clip.mp4")
    _create_test_image(mock_wan_clip)  # create dummy file

    project = VideoProject(
        id=uuid.uuid4(),
        user_id=test_user.id,
        title="Phase 9 Wan Success Test",
        occasion="Birthday",
        duration_seconds=15,
        aspect_ratio="9:16",
        scene_plan={
            "scenes": [
                {
                    "scene_number": 1,
                    "title": "Scene 1",
                    "duration_seconds": 5,
                    "visual_description": "Celebration",
                    "camera_movement": "zoom_in",
                    "generation_type": "wan_video",
                    "reference_image_ids": [],
                }
            ]
        },
    )
    db_session.add(project)
    db_session.flush()

    p_img = ProjectImage(
        id=uuid.uuid4(),
        project_id=project.id,
        file_path=img_file,
        original_filename="wan_success_img.jpg",
        mime_type="image/jpeg",
        file_size=os.path.getsize(img_file),
        order_index=0,
    )
    db_session.add(p_img)
    db_session.commit()

    mock_wan = AsyncMock()
    mock_wan.generate_scene_clip.return_value = mock_wan_clip
    mock_wan.effective_mock_mode = True
    mock_wan.last_run_metrics = {}

    slideshow_provider = FFmpegSlideshowProvider(output_dir=str(tmp_path))
    service = VideoGenerationService(video_provider=mock_wan, slideshow_provider=slideshow_provider)

    job = await service.generate_project_clips(
        project_id=project.id,
        user_id=test_user.id,
        db_session_factory=lambda: db_session,
    )

    assert job.status == "COMPLETED"
    assert job.external_provider_ids.get("providers", {}).get("1") == "wan_video"
    assert len(job.external_provider_ids.get("fallback_scenes", [])) == 0

    asset = db_session.execute(
        select(GeneratedAsset).where(GeneratedAsset.project_id == project.id)
    ).scalars().first()
    assert asset.meta_data.get("provider") == "wan_video"
    assert asset.meta_data.get("fallback_used") is False


# ---------------------------------------------------------------------------
# Test 11: Partial scene failure and retry handling
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_partial_scene_failure_and_retry_handling(db_session, tmp_path):
    test_user = db_session.query(User).filter(User.email == "phase9_user@example.com").first()

    img1 = str(tmp_path / "partial_img1.jpg")
    img2 = str(tmp_path / "partial_img2.jpg")
    _create_test_image(img1)
    _create_test_image(img2)

    project = VideoProject(
        id=uuid.uuid4(),
        user_id=test_user.id,
        title="Phase 9 Partial Retry Test",
        occasion="Anniversary",
        duration_seconds=15,
        aspect_ratio="9:16",
        scene_plan={
            "scenes": [
                {"scene_number": 1, "duration_seconds": 5, "generation_type": "animated_still", "reference_image_ids": []},
                {"scene_number": 2, "duration_seconds": 5, "generation_type": "animated_still", "reference_image_ids": []},
            ]
        },
    )
    db_session.add(project)
    db_session.flush()

    p_img1 = ProjectImage(
        id=uuid.uuid4(),
        project_id=project.id,
        file_path=img1,
        original_filename="p1.jpg",
        mime_type="image/jpeg",
        file_size=os.path.getsize(img1),
        order_index=0,
    )
    p_img2 = ProjectImage(
        id=uuid.uuid4(),
        project_id=project.id,
        file_path=img2,
        original_filename="p2.jpg",
        mime_type="image/jpeg",
        file_size=os.path.getsize(img2),
        order_index=1,
    )
    db_session.add_all([p_img1, p_img2])
    db_session.commit()

    slideshow_provider = FFmpegSlideshowProvider(output_dir=str(tmp_path))
    service = VideoGenerationService(slideshow_provider=slideshow_provider)

    # First run generates both scenes
    job1 = await service.generate_project_clips(
        project_id=project.id,
        user_id=test_user.id,
        db_session_factory=lambda: db_session,
    )
    assert job1.status == "COMPLETED"

    assets_before = service.get_existing_scene_assets(project.id, db_session)
    assert len(assets_before) == 2
    asset_1_id = assets_before[1].id

    # Retry ONLY Scene 2
    job2 = await service.generate_project_clips(
        project_id=project.id,
        user_id=test_user.id,
        db_session_factory=lambda: db_session,
        force_retry_scene=2,
    )
    assert job2.status == "COMPLETED"

    assets_after = service.get_existing_scene_assets(project.id, db_session)
    assert len(assets_after) == 2
    # Scene 1 was preserved (not re-generated)
    assert assets_after[1].id == asset_1_id


# ---------------------------------------------------------------------------
# Test 12: Compatibility with Phase 8 FFmpeg composition
# ---------------------------------------------------------------------------
def test_slideshow_clips_compatibility_with_phase8_composer(tmp_path):
    """Verifies that slideshow clips seamlessly feed into Phase 8 FFmpeg composition."""
    img_path = str(tmp_path / "comp_img.jpg")
    _create_test_image(img_path)

    provider = FFmpegSlideshowProvider(output_dir=str(tmp_path))

    # Generate 2 slideshow clips
    import asyncio
    clip1 = asyncio.run(provider.generate_scene_clip(image_path=img_path, duration_seconds=3, scene_index=1))
    clip2 = asyncio.run(provider.generate_scene_clip(image_path=img_path, duration_seconds=3, scene_index=2))

    vo_path = str(tmp_path / "voiceover.aac")
    bgm_path = str(tmp_path / "bgm.aac")
    _create_test_audio(vo_path, duration=6)
    _create_test_audio(bgm_path, duration=6)

    output_path = str(tmp_path / "composed_final.mp4")

    cmd = build_composition_command(
        clip_paths=[clip1, clip2],
        voiceover_path=vo_path,
        music_path=bgm_path,
        output_path=output_path,
        aspect_ratio="9:16",
        duration_seconds=6,
    )

    execute_composition(cmd)
    assert os.path.isfile(output_path)
    assert os.path.getsize(output_path) > 0
