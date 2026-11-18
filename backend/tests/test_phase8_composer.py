import os
import uuid
import time
import subprocess
import pytest
import imageio_ffmpeg

from app.models.user import User
from app.models.project import VideoProject
from app.models.job import GenerationJob
from app.models.asset import GeneratedAsset
from app.core.security import hash_password
from app.core.exceptions import NotFoundError, BadRequestError, ServiceUnavailableError
from app.composer.ffmpeg_builder import (
    RESOLUTION_MAP,
    get_target_resolution,
    build_composition_command,
)
from app.composer.executor import (
    execute_composition,
    validate_video_file,
)
from app.services.composer import VideoCompositionService


# Helper to generate short synthetic test media with FFmpeg
def _create_synthetic_video_clip(path: str, duration_sec: int = 2, width: int = 640, height: int = 480):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        exe, "-y",
        "-f", "lavfi",
        "-i", f"testsrc=duration={duration_sec}:size={width}x{height}:rate=25",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        path,
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


def _create_synthetic_audio(path: str, duration_sec: int = 3, freq: int = 440):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        exe, "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency={freq}:duration={duration_sec}",
        "-c:a", "pcm_s16le",
        path,
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


# ---------------------------------------------------------------------------
# 1. FFmpeg Builder Unit Tests
# ---------------------------------------------------------------------------

def test_resolution_mapping():
    """Verify aspect ratio resolutions and fallback handling."""
    assert get_target_resolution("9:16") == (720, 1280)
    assert get_target_resolution("16:9") == (1280, 720)
    assert get_target_resolution("1:1") == (720, 720)
    assert get_target_resolution("invalid") == (720, 1280)
    assert get_target_resolution("") == (720, 1280)


def test_build_composition_command_all_aspect_ratios():
    """Verify command generation for 9:16, 16:9, and 1:1 aspect ratios."""
    clips = ["clip1.mp4", "clip2.mp4"]
    vo = "vo.wav"
    bgm = "bgm.wav"

    for ratio, (w, h) in [("9:16", (720, 1280)), ("16:9", (1280, 720)), ("1:1", (720, 720))]:
        cmd = build_composition_command(
            clip_paths=clips,
            voiceover_path=vo,
            music_path=bgm,
            output_path=f"out_{ratio.replace(':', '_')}.mp4",
            aspect_ratio=ratio,
            duration_seconds=15,
        )

        filter_str = cmd[cmd.index("-filter_complex") + 1]
        assert f"scale={w}:{h}:force_original_aspect_ratio=decrease" in filter_str
        assert f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black" in filter_str
        assert "setsar=1" in filter_str
        assert "fps=30" in filter_str
        assert "format=yuv420p" in filter_str


def test_build_composition_command_scene_ordering():
    """Verify input clips are concatenated in exact scene order."""
    clips = ["scene_1.mp4", "scene_2.mp4", "scene_3.mp4"]
    cmd = build_composition_command(
        clip_paths=clips,
        voiceover_path="vo.wav",
        music_path="bgm.wav",
        output_path="ordered.mp4",
        duration_seconds=15,
    )

    # Check input sequence
    i_indices = [i for i, flag in enumerate(cmd) if flag == "-i"]
    assert len(i_indices) == 5  # 3 clips + 1 vo + 1 bgm
    assert os.path.abspath("scene_1.mp4") == cmd[i_indices[0] + 1]
    assert os.path.abspath("scene_2.mp4") == cmd[i_indices[1] + 1]
    assert os.path.abspath("scene_3.mp4") == cmd[i_indices[2] + 1]

    # Check concatenation filter
    filter_str = cmd[cmd.index("-filter_complex") + 1]
    assert "[v0][v1][v2]concat=n=3:v=1:a=0[v_concat]" in filter_str


def test_build_composition_command_audio_mixing_and_volume():
    """Verify voiceover (1.0), background music (0.22), looping, and fade-out."""
    cmd = build_composition_command(
        clip_paths=["c1.mp4"],
        voiceover_path="vo.wav",
        music_path="bgm.wav",
        output_path="out.mp4",
        duration_seconds=15,
    )

    # Check music stream looping
    stream_loop_idx = cmd.index("-stream_loop")
    assert cmd[stream_loop_idx + 1] == "-1"
    assert cmd[stream_loop_idx + 2] == "-i"
    assert os.path.abspath("bgm.wav") == cmd[stream_loop_idx + 3]

    filter_str = cmd[cmd.index("-filter_complex") + 1]
    # Check voiceover volume=1.0 and fade out at 14.0s
    assert "volume=1.00" in filter_str
    assert "afade=t=out:st=14.00:d=1.00[vo]" in filter_str
    # Check bgm volume=0.22 and fade out at 14.0s
    assert "volume=0.22" in filter_str
    assert "afade=t=out:st=14.00:d=1.00[bgm]" in filter_str
    # Check audio mixing
    assert "[vo][bgm]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[a_mixed]" in filter_str

    # Check encoding and web playback parameters
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert cmd[cmd.index("-crf") + 1] == "22"
    assert cmd[cmd.index("-preset") + 1] == "fast"
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert cmd[cmd.index("-b:a") + 1] == "192k"
    assert cmd[cmd.index("-ar") + 1] == "44100"
    assert cmd[cmd.index("-ac") + 1] == "2"
    assert cmd[cmd.index("-t") + 1] == "15"
    assert cmd[cmd.index("-movflags") + 1] == "+faststart"


def test_build_composition_command_validation_errors():
    """Verify builder raises ValueError on invalid inputs."""
    with pytest.raises(ValueError, match="At least one video clip"):
        build_composition_command([], "vo.wav", "bgm.wav", "out.mp4")

    with pytest.raises(ValueError, match="Voiceover audio path"):
        build_composition_command(["c1.mp4"], "", "bgm.wav", "out.mp4")

    with pytest.raises(ValueError, match="Background music path"):
        build_composition_command(["c1.mp4"], "vo.wav", "", "out.mp4")

    with pytest.raises(ValueError, match="Duration must be a positive integer"):
        build_composition_command(["c1.mp4"], "vo.wav", "bgm.wav", "out.mp4", duration_seconds=0)


# ---------------------------------------------------------------------------
# 2. FFmpeg Executor Unit Tests
# ---------------------------------------------------------------------------

def test_execute_composition_and_validation(tmp_path):
    """Test full execution and validation using synthetic media files."""
    clip1 = str(tmp_path / "c1.mp4")
    clip2 = str(tmp_path / "c2.mp4")
    vo = str(tmp_path / "vo.wav")
    bgm = str(tmp_path / "bgm.wav")
    out_mp4 = str(tmp_path / "final.mp4")

    _create_synthetic_video_clip(clip1, duration_sec=2, width=1280, height=720)
    _create_synthetic_video_clip(clip2, duration_sec=2, width=640, height=480)
    _create_synthetic_audio(vo, duration_sec=3, freq=440)
    _create_synthetic_audio(bgm, duration_sec=5, freq=880)

    cmd = build_composition_command(
        clip_paths=[clip1, clip2],
        voiceover_path=vo,
        music_path=bgm,
        output_path=out_mp4,
        aspect_ratio="9:16",
        duration_seconds=4,
    )

    # 1. Execute composition
    execute_composition(cmd=cmd, output_path=out_mp4)
    assert os.path.isfile(out_mp4)
    assert os.path.getsize(out_mp4) > 1024

    # 2. Validate output
    meta = validate_video_file(
        file_path=out_mp4,
        expected_duration=4.0,
        expected_ratio="9:16",
    )
    assert meta["width"] == 720
    assert meta["height"] == 1280
    assert meta["video_codec"] == "h264"
    assert meta["audio_codec"] == "aac"
    assert abs(meta["duration_seconds"] - 4.0) <= 0.5


def test_execute_composition_handles_failure_and_cleans_up(tmp_path):
    """Verify executor raises ServiceUnavailableError and deletes corrupt partial file on failure."""
    bad_output = str(tmp_path / "bad.mp4")
    bad_cmd = ["non_existent_ffmpeg_executable_xyz", "-i", "fake.mp4", bad_output]

    with pytest.raises(ServiceUnavailableError):
        execute_composition(cmd=bad_cmd, output_path=bad_output)

    assert not os.path.exists(bad_output)


def test_validate_video_file_errors(tmp_path):
    """Verify validation raises BadRequestError on missing or invalid video files."""
    # Non-existent file
    with pytest.raises(BadRequestError, match="does not exist"):
        validate_video_file(str(tmp_path / "missing.mp4"))

    # Empty / tiny file
    empty_file = str(tmp_path / "empty.mp4")
    with open(empty_file, "wb") as f:
        f.write(b"not a video")
    with pytest.raises(BadRequestError, match="too small"):
        validate_video_file(empty_file)


# ---------------------------------------------------------------------------
# 3. VideoCompositionService Tests
# ---------------------------------------------------------------------------

@pytest.fixture
def composer_user(db_session):
    user = User(
        id=uuid.uuid4(),
        email=f"composer_test_{uuid.uuid4().hex[:6]}@example.com",
        full_name="Composer Test User",
        hashed_password=hash_password("Password123!")
    )
    db_session.add(user)
    db_session.commit()
    return user


def test_composer_service_missing_clips_validation(db_session, composer_user):
    """Verify service fails when project has no video clips."""
    project = VideoProject(
        id=uuid.uuid4(),
        user_id=composer_user.id,
        title="No Clips Project",
        aspect_ratio="9:16",
        duration_seconds=15,
    )
    db_session.add(project)
    db_session.commit()

    service = VideoCompositionService()
    with pytest.raises(BadRequestError, match="No video clips found"):
        service._run_composition_sync(
            project_id=project.id,
            user_id=composer_user.id,
            db_session_factory=lambda: db_session,
        )


def test_composer_service_missing_scene_clip_validation(db_session, composer_user, tmp_path):
    """Verify service fails when a required scene from the scene plan is missing."""
    clip1 = str(tmp_path / "clip1.mp4")
    _create_synthetic_video_clip(clip1, duration_sec=2)

    project = VideoProject(
        id=uuid.uuid4(),
        user_id=composer_user.id,
        title="Missing Scene Clip Project",
        aspect_ratio="9:16",
        duration_seconds=15,
        scene_plan={
            "scenes": [
                {"scene_number": 1, "narration_text": "Scene 1"},
                {"scene_number": 2, "narration_text": "Scene 2"},
            ]
        }
    )
    db_session.add(project)
    db_session.commit()

    # Only add clip for scene 1
    asset1 = GeneratedAsset(
        id=uuid.uuid4(),
        project_id=project.id,
        asset_type="CLIP",
        file_path=clip1,
        meta_data={"scene_number": 1},
    )
    db_session.add(asset1)
    db_session.commit()

    service = VideoCompositionService()
    with pytest.raises(BadRequestError, match="Missing video clip for scene 2"):
        service._run_composition_sync(
            project_id=project.id,
            user_id=composer_user.id,
            db_session_factory=lambda: db_session,
        )


def test_composer_service_missing_voiceover_validation(db_session, composer_user, tmp_path):
    """Verify service fails when voiceover asset is missing."""
    clip1 = str(tmp_path / "clip1.mp4")
    _create_synthetic_video_clip(clip1, duration_sec=2)

    project = VideoProject(
        id=uuid.uuid4(),
        user_id=composer_user.id,
        title="Missing Voiceover Project",
        aspect_ratio="9:16",
        duration_seconds=15,
    )
    db_session.add(project)
    db_session.commit()

    asset1 = GeneratedAsset(
        id=uuid.uuid4(),
        project_id=project.id,
        asset_type="CLIP",
        file_path=clip1,
        meta_data={"scene_number": 1},
    )
    db_session.add(asset1)
    db_session.commit()

    service = VideoCompositionService()
    with pytest.raises(BadRequestError, match="Voiceover audio is missing"):
        service._run_composition_sync(
            project_id=project.id,
            user_id=composer_user.id,
            db_session_factory=lambda: db_session,
        )


def test_composer_service_missing_music_validation(db_session, composer_user, tmp_path):
    """Verify service fails when background music asset is missing."""
    clip1 = str(tmp_path / "clip1.mp4")
    vo = str(tmp_path / "vo.wav")
    _create_synthetic_video_clip(clip1, duration_sec=2)
    _create_synthetic_audio(vo, duration_sec=2)

    project = VideoProject(
        id=uuid.uuid4(),
        user_id=composer_user.id,
        title="Missing Music Project",
        aspect_ratio="9:16",
        duration_seconds=15,
    )
    db_session.add(project)
    db_session.commit()

    asset_clip = GeneratedAsset(
        id=uuid.uuid4(),
        project_id=project.id,
        asset_type="CLIP",
        file_path=clip1,
        meta_data={"scene_number": 1},
    )
    asset_vo = GeneratedAsset(
        id=uuid.uuid4(),
        project_id=project.id,
        asset_type="VOICEOVER",
        file_path=vo,
    )
    db_session.add_all([asset_clip, asset_vo])
    db_session.commit()

    service = VideoCompositionService()
    with pytest.raises(BadRequestError, match="Background music is missing"):
        service._run_composition_sync(
            project_id=project.id,
            user_id=composer_user.id,
            db_session_factory=lambda: db_session,
        )


def test_composer_service_end_to_end_composition(db_session, composer_user, tmp_path):
    """End-to-end integration test: compose complete project video with clips, voiceover, and music."""
    clip1 = str(tmp_path / "c1.mp4")
    clip2 = str(tmp_path / "c2.mp4")
    vo = str(tmp_path / "vo.wav")
    bgm = str(tmp_path / "bgm.wav")

    _create_synthetic_video_clip(clip1, duration_sec=2, width=1280, height=720)
    _create_synthetic_video_clip(clip2, duration_sec=2, width=640, height=480)
    _create_synthetic_audio(vo, duration_sec=3, freq=440)
    _create_synthetic_audio(bgm, duration_sec=5, freq=880)

    project = VideoProject(
        id=uuid.uuid4(),
        user_id=composer_user.id,
        title="End-to-End Composition Test",
        aspect_ratio="9:16",
        duration_seconds=4,
        scene_plan={
            "scenes": [
                {"scene_number": 1, "narration_text": "First scene"},
                {"scene_number": 2, "narration_text": "Second scene"},
            ]
        }
    )
    db_session.add(project)
    db_session.commit()

    # Intentionally add clips in reverse order to verify service orders them by scene_number
    asset_clip2 = GeneratedAsset(
        id=uuid.uuid4(),
        project_id=project.id,
        asset_type="CLIP",
        file_path=clip2,
        meta_data={"scene_number": 2},
    )
    asset_clip1 = GeneratedAsset(
        id=uuid.uuid4(),
        project_id=project.id,
        asset_type="CLIP",
        file_path=clip1,
        meta_data={"scene_number": 1},
    )
    asset_vo = GeneratedAsset(
        id=uuid.uuid4(),
        project_id=project.id,
        asset_type="VOICEOVER",
        file_path=vo,
        meta_data={"narration_text": "Hello world"},
    )
    asset_music = GeneratedAsset(
        id=uuid.uuid4(),
        project_id=project.id,
        asset_type="MUSIC",
        file_path=bgm,
        meta_data={"track_id": "happy_birthday_fun", "provider": "static_library"},
    )
    db_session.add_all([asset_clip2, asset_clip1, asset_vo, asset_music])
    db_session.commit()

    service = VideoCompositionService()

    # Run composition
    job = service._run_composition_sync(
        project_id=project.id,
        user_id=composer_user.id,
        db_session_factory=lambda: db_session,
    )

    # 1. Verify Job status
    assert job is not None
    assert job.stage == "COMPOSING"
    assert job.status == "COMPLETED"
    assert job.progress_percent == 100
    assert job.completed_at is not None
    assert job.external_provider_ids.get("final_video_asset_id") is not None

    # 2. Verify Project status
    db_session.refresh(project)
    assert project.status == "COMPLETED"

    # 3. Verify GeneratedAsset
    final_asset = db_session.query(GeneratedAsset).filter(
        GeneratedAsset.project_id == project.id,
        GeneratedAsset.asset_type == "FINAL_VIDEO"
    ).first()
    assert final_asset is not None
    assert final_asset.duration_seconds > 0
    assert final_asset.file_size > 1024
    assert final_asset.meta_data.get("resolution") == "720x1280"
    assert final_asset.meta_data.get("aspect_ratio") == "9:16"
    assert final_asset.meta_data.get("scene_count") == 2
    assert final_asset.meta_data.get("video_codec") == "h264"
    assert final_asset.meta_data.get("audio_codec") == "aac"

    # 4. Verify physical video file exists and can be decoded
    resolved_path = service._resolve_file_path(final_asset.file_path)
    assert resolved_path is not None
    assert os.path.isfile(resolved_path)

    # Decode test with FFmpeg
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    res = subprocess.run([exe, "-i", resolved_path, "-f", "null", "-"], capture_output=True, text=True)
    assert res.returncode == 0

    # 5. Verify caching / reuse on subsequent call without force_regenerate
    job2 = service._run_composition_sync(
        project_id=project.id,
        user_id=composer_user.id,
        db_session_factory=lambda: db_session,
        force_regenerate=False,
    )
    assert job2.status == "COMPLETED"
    assert job2.external_provider_ids.get("reused_asset_id") == str(final_asset.id)


# ---------------------------------------------------------------------------
# 4. Phase 8 Step 3: API Integration Tests
# ---------------------------------------------------------------------------

def test_api_compose_unauthorized(client):
    """Verify POST /api/v1/projects/{id}/compose requires authentication."""
    client.cookies.clear()
    res = client.post(f"/api/v1/projects/{uuid.uuid4()}/compose")
    assert res.status_code == 401


def test_api_compose_project_ownership(client, db_session):
    """Verify user cannot compose another user's project (403 Forbidden)."""
    # User A
    client.cookies.clear()
    email_a = f"owner_a_{uuid.uuid4().hex[:6]}@example.com"
    client.post("/api/v1/auth/register", json={
        "email": email_a,
        "password": "Password123!",
        "full_name": "User A"
    })
    proj_res = client.post("/api/v1/projects", json={
        "title": "User A Project",
        "occasion": "Birthday",
        "duration_seconds": 15,
        "aspect_ratio": "9:16"
    })
    assert proj_res.status_code == 201
    project_id = proj_res.json()["id"]

    # User B
    client.cookies.clear()
    email_b = f"intruder_b_{uuid.uuid4().hex[:6]}@example.com"
    client.post("/api/v1/auth/register", json={
        "email": email_b,
        "password": "Password123!",
        "full_name": "User B"
    })

    # User B attempts to compose User A's project
    res = client.post(f"/api/v1/projects/{project_id}/compose")
    assert res.status_code == 403


def test_api_compose_missing_clips(client):
    """Verify composition endpoint fails with 400 when clips are missing."""
    client.cookies.clear()
    email = f"noclips_{uuid.uuid4().hex[:6]}@example.com"
    client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Password123!",
        "full_name": "No Clips User"
    })
    proj_res = client.post("/api/v1/projects", json={
        "title": "No Clips Project",
        "occasion": "Birthday",
        "duration_seconds": 15,
        "aspect_ratio": "9:16"
    })
    project_id = proj_res.json()["id"]

    res = client.post(f"/api/v1/projects/{project_id}/compose")
    assert res.status_code == 400
    assert "video clips" in res.json()["error"]["message"].lower()


def test_api_compose_missing_scene_clip(client, db_session, tmp_path):
    """Verify composition endpoint fails when a specific scene from scene_plan is missing."""
    client.cookies.clear()
    email = f"missingscene_{uuid.uuid4().hex[:6]}@example.com"
    client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Password123!",
        "full_name": "Missing Scene User"
    })
    proj_res = client.post("/api/v1/projects", json={
        "title": "Missing Scene Project",
        "occasion": "Birthday",
        "duration_seconds": 15,
        "aspect_ratio": "9:16"
    })
    project_id = uuid.UUID(proj_res.json()["id"])

    # Update scene plan with 2 scenes
    project = db_session.get(VideoProject, project_id)
    project.scene_plan = {
        "scenes": [
            {"scene_number": 1, "narration_text": "Scene 1"},
            {"scene_number": 2, "narration_text": "Scene 2"},
        ]
    }
    # Only add scene 1 clip
    clip1 = str(tmp_path / "c1.mp4")
    _create_synthetic_video_clip(clip1, duration_sec=2)
    asset1 = GeneratedAsset(
        id=uuid.uuid4(),
        project_id=project_id,
        asset_type="CLIP",
        file_path=clip1,
        meta_data={"scene_number": 1},
    )
    db_session.add(asset1)
    db_session.commit()

    res = client.post(f"/api/v1/projects/{project_id}/compose")
    assert res.status_code == 400
    assert "scene 2" in res.json()["error"]["message"].lower()


def test_api_compose_missing_voiceover(client, db_session, tmp_path):
    """Verify composition endpoint fails with 400 when voiceover is missing."""
    client.cookies.clear()
    email = f"novoice_{uuid.uuid4().hex[:6]}@example.com"
    client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Password123!",
        "full_name": "No Voice User"
    })
    proj_res = client.post("/api/v1/projects", json={
        "title": "No Voice Project",
        "occasion": "Birthday",
        "duration_seconds": 15,
        "aspect_ratio": "9:16"
    })
    project_id = uuid.UUID(proj_res.json()["id"])

    # Add clip only
    clip1 = str(tmp_path / "c1_only.mp4")
    _create_synthetic_video_clip(clip1, duration_sec=2)
    asset1 = GeneratedAsset(
        id=uuid.uuid4(),
        project_id=project_id,
        asset_type="CLIP",
        file_path=clip1,
        meta_data={"scene_number": 1},
    )
    db_session.add(asset1)
    db_session.commit()

    res = client.post(f"/api/v1/projects/{project_id}/compose")
    assert res.status_code == 400
    assert "voiceover" in res.json()["error"]["message"].lower()


def test_api_compose_missing_music(client, db_session, tmp_path):
    """Verify composition endpoint fails with 400 when background music is missing."""
    client.cookies.clear()
    email = f"nomusic_{uuid.uuid4().hex[:6]}@example.com"
    client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Password123!",
        "full_name": "No Music User"
    })
    proj_res = client.post("/api/v1/projects", json={
        "title": "No Music Project",
        "occasion": "Birthday",
        "duration_seconds": 15,
        "aspect_ratio": "9:16"
    })
    project_id = uuid.UUID(proj_res.json()["id"])

    # Add clip and voiceover
    clip1 = str(tmp_path / "clip_a.mp4")
    vo1 = str(tmp_path / "vo_a.wav")
    _create_synthetic_video_clip(clip1, duration_sec=2)
    _create_synthetic_audio(vo1, duration_sec=2)

    asset_clip = GeneratedAsset(
        id=uuid.uuid4(),
        project_id=project_id,
        asset_type="CLIP",
        file_path=clip1,
        meta_data={"scene_number": 1},
    )
    asset_vo = GeneratedAsset(
        id=uuid.uuid4(),
        project_id=project_id,
        asset_type="VOICEOVER",
        file_path=vo1,
    )
    db_session.add_all([asset_clip, asset_vo])
    db_session.commit()

    res = client.post(f"/api/v1/projects/{project_id}/compose")
    assert res.status_code == 400
    assert "music" in res.json()["error"]["message"].lower()


def test_api_compose_successful_dispatch_and_job_status(client, db_session, tmp_path):
    """Verify successful POST /compose returns 202 and tracks progress in GenerationJob."""
    client.cookies.clear()
    email = f"compose_success_{uuid.uuid4().hex[:6]}@example.com"
    client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Password123!",
        "full_name": "Compose User"
    })
    proj_res = client.post("/api/v1/projects", json={
        "title": "Complete Composition Project",
        "occasion": "Birthday",
        "duration_seconds": 15,
        "aspect_ratio": "9:16"
    })
    project_id = uuid.UUID(proj_res.json()["id"])

    # Prepare all required assets
    clip1 = str(tmp_path / "comp_c1.mp4")
    vo1 = str(tmp_path / "comp_vo.wav")
    bgm1 = str(tmp_path / "comp_bgm.wav")
    _create_synthetic_video_clip(clip1, duration_sec=2)
    _create_synthetic_audio(vo1, duration_sec=2)
    _create_synthetic_audio(bgm1, duration_sec=4)

    asset_clip = GeneratedAsset(
        id=uuid.uuid4(),
        project_id=project_id,
        asset_type="CLIP",
        file_path=clip1,
        meta_data={"scene_number": 1},
    )
    asset_vo = GeneratedAsset(
        id=uuid.uuid4(),
        project_id=project_id,
        asset_type="VOICEOVER",
        file_path=vo1,
    )
    asset_bgm = GeneratedAsset(
        id=uuid.uuid4(),
        project_id=project_id,
        asset_type="MUSIC",
        file_path=bgm1,
    )
    db_session.add_all([asset_clip, asset_vo, asset_bgm])
    db_session.commit()

    # 1. Post compose request
    res = client.post(f"/api/v1/projects/{project_id}/compose")
    assert res.status_code == 202
    job_data = res.json()
    assert job_data["stage"] == "COMPOSING"
    assert job_data["status"] == "QUEUED"
    assert job_data["project_id"] == str(project_id)

    # 2. Check latest job status endpoint
    job_res = client.get(f"/api/v1/projects/{project_id}/jobs/latest")
    assert job_res.status_code == 200
    assert job_res.json()["stage"] == "COMPOSING"


def test_api_compose_duplicate_prevention(client, db_session, tmp_path):
    """Verify duplicate simultaneous composition jobs are rejected with 400 Bad Request."""
    client.cookies.clear()
    email = f"dup_{uuid.uuid4().hex[:6]}@example.com"
    client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Password123!",
        "full_name": "Dup User"
    })
    proj_res = client.post("/api/v1/projects", json={
        "title": "Duplicate Project",
        "occasion": "Birthday",
        "duration_seconds": 15,
        "aspect_ratio": "9:16"
    })
    project_id = uuid.UUID(proj_res.json()["id"])

    # Inject an active running job
    active_job = GenerationJob(
        project_id=project_id,
        stage="COMPOSING",
        status="RUNNING",
        progress_percent=45,
    )
    db_session.add(active_job)
    db_session.commit()

    res = client.post(f"/api/v1/projects/{project_id}/compose")
    assert res.status_code == 400
    assert "already in progress" in res.json()["error"]["message"].lower()


def test_api_final_video_endpoint_states(client, db_session, tmp_path):
    """Verify GET /api/v1/projects/{id}/final-video reports correct state before and after composition."""
    client.cookies.clear()
    email = f"finalvid_{uuid.uuid4().hex[:6]}@example.com"
    client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Password123!",
        "full_name": "Final Video User"
    })
    proj_res = client.post("/api/v1/projects", json={
        "title": "Final Video State Project",
        "occasion": "Birthday",
        "duration_seconds": 15,
        "aspect_ratio": "9:16"
    })
    project_id = uuid.UUID(proj_res.json()["id"])

    # 1. State: NOT_STARTED
    res_initial = client.get(f"/api/v1/projects/{project_id}/final-video")
    assert res_initial.status_code == 200
    data_initial = res_initial.json()
    assert data_initial["status"] == "NOT_STARTED"
    assert data_initial["id"] is None
    assert data_initial["stream_url"] is None

    # 2. State: COMPLETED
    final_file = str(tmp_path / "final_out.mp4")
    _create_synthetic_video_clip(final_file, duration_sec=3, width=720, height=1280)

    final_asset = GeneratedAsset(
        id=uuid.uuid4(),
        project_id=project_id,
        asset_type="FINAL_VIDEO",
        file_path=final_file,
        duration_seconds=3.0,
        file_size=os.path.getsize(final_file),
        meta_data={
            "resolution": "720x1280",
            "video_codec": "h264",
            "audio_codec": "aac",
            "scene_count": 1,
        }
    )
    db_session.add(final_asset)
    db_session.commit()

    res_completed = client.get(f"/api/v1/projects/{project_id}/final-video")
    assert res_completed.status_code == 200
    data_completed = res_completed.json()
    assert data_completed["status"] == "COMPLETED"
    assert data_completed["id"] == str(final_asset.id)
    assert data_completed["resolution"] == "720x1280"
    assert data_completed["video_codec"] == "h264"
    assert data_completed["audio_codec"] == "aac"
    assert data_completed["stream_url"] == f"/api/v1/assets/{final_asset.id}/stream"
    assert data_completed["download_url"] == f"/api/v1/assets/{final_asset.id}/download"


def test_api_final_video_streaming_and_range_requests(client, db_session, tmp_path):
    """Verify streaming endpoint supports video/mp4, HTTP Range requests (206), and download."""
    client.cookies.clear()
    email = f"stream_{uuid.uuid4().hex[:6]}@example.com"
    client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Password123!",
        "full_name": "Stream User"
    })
    proj_res = client.post("/api/v1/projects", json={
        "title": "Stream Project",
        "occasion": "Birthday",
        "duration_seconds": 15,
        "aspect_ratio": "9:16"
    })
    project_id = uuid.UUID(proj_res.json()["id"])

    # Create real small mp4 file
    mp4_file = str(tmp_path / "streamable.mp4")
    _create_synthetic_video_clip(mp4_file, duration_sec=2, width=640, height=360)
    total_size = os.path.getsize(mp4_file)

    asset = GeneratedAsset(
        id=uuid.uuid4(),
        project_id=project_id,
        asset_type="FINAL_VIDEO",
        file_path=mp4_file,
        duration_seconds=2.0,
        file_size=total_size,
    )
    db_session.add(asset)
    db_session.commit()

    # 1. Full stream
    full_res = client.get(f"/api/v1/assets/{asset.id}/stream")
    assert full_res.status_code == 200
    assert full_res.headers["content-type"] == "video/mp4"
    assert full_res.headers.get("accept-ranges") == "bytes"
    assert int(full_res.headers["content-length"]) == total_size

    # 2. HTTP Range request (bytes=0-100) -> 206 Partial Content
    range_res = client.get(
        f"/api/v1/assets/{asset.id}/stream",
        headers={"Range": "bytes=0-100"}
    )
    assert range_res.status_code == 206
    assert "bytes 0-100/" in range_res.headers.get("content-range", "")
    assert len(range_res.content) == 101

    # 3. Download endpoint
    dl_res = client.get(f"/api/v1/assets/{asset.id}/download")
    assert dl_res.status_code == 200
    assert "attachment" in dl_res.headers.get("content-disposition", "")

    # 4. Unauthorized stream request
    client.cookies.clear()
    unauth_res = client.get(f"/api/v1/assets/{asset.id}/stream")
    assert unauth_res.status_code == 401


def test_api_final_video_streaming_forbidden(client, db_session, tmp_path):
    """Verify User B cannot stream User A's final video (403 Forbidden)."""
    # User A
    client.cookies.clear()
    email_a = f"usera_stream_{uuid.uuid4().hex[:6]}@example.com"
    client.post("/api/v1/auth/register", json={
        "email": email_a,
        "password": "Password123!",
        "full_name": "User A Stream"
    })
    proj_res = client.post("/api/v1/projects", json={
        "title": "Stream Ownership Project",
        "occasion": "Birthday",
        "duration_seconds": 15,
        "aspect_ratio": "9:16"
    })
    project_id = uuid.UUID(proj_res.json()["id"])

    mp4_file = str(tmp_path / "private.mp4")
    _create_synthetic_video_clip(mp4_file, duration_sec=1)
    asset = GeneratedAsset(
        id=uuid.uuid4(),
        project_id=project_id,
        asset_type="FINAL_VIDEO",
        file_path=mp4_file,
    )
    db_session.add(asset)
    db_session.commit()

    # User B
    client.cookies.clear()
    email_b = f"userb_stream_{uuid.uuid4().hex[:6]}@example.com"
    client.post("/api/v1/auth/register", json={
        "email": email_b,
        "password": "Password123!",
        "full_name": "User B Stream"
    })

    # User B attempts to stream User A's asset
    res = client.get(f"/api/v1/assets/{asset.id}/stream")
    assert res.status_code == 403


def test_api_failed_composition_job_status(client, db_session):
    """Verify failed composition status and error message are correctly reported."""
    client.cookies.clear()
    email = f"failedcomp_{uuid.uuid4().hex[:6]}@example.com"
    client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Password123!",
        "full_name": "Failed Comp User"
    })
    proj_res = client.post("/api/v1/projects", json={
        "title": "Failed Comp Project",
        "occasion": "Birthday",
        "duration_seconds": 15,
        "aspect_ratio": "9:16"
    })
    project_id = uuid.UUID(proj_res.json()["id"])

    job = GenerationJob(
        project_id=project_id,
        stage="COMPOSING",
        status="FAILED",
        progress_percent=50,
        error_message="Simulated FFmpeg out of memory error"
    )
    db_session.add(job)
    db_session.commit()

    # Check /jobs/latest
    job_res = client.get(f"/api/v1/projects/{project_id}/jobs/latest")
    assert job_res.status_code == 200
    assert job_res.json()["status"] == "FAILED"
    assert job_res.json()["error_message"] == "Simulated FFmpeg out of memory error"

    # Check /final-video
    fv_res = client.get(f"/api/v1/projects/{project_id}/final-video")
    assert fv_res.status_code == 200
    assert fv_res.json()["status"] == "FAILED"


def test_api_existing_asset_streaming_regression(client, db_session, tmp_path):
    """Regression test: verify CLIP, VOICEOVER, and MUSIC assets stream correctly without regression."""
    client.cookies.clear()
    email = f"regr_{uuid.uuid4().hex[:6]}@example.com"
    client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Password123!",
        "full_name": "Regression User"
    })
    proj_res = client.post("/api/v1/projects", json={
        "title": "Regression Project",
        "occasion": "Birthday",
        "duration_seconds": 15,
        "aspect_ratio": "9:16"
    })
    project_id = uuid.UUID(proj_res.json()["id"])

    clip_file = str(tmp_path / "regr_clip.mp4")
    vo_file = str(tmp_path / "regr_vo.wav")
    music_file = str(tmp_path / "regr_music.wav")
    _create_synthetic_video_clip(clip_file, duration_sec=1)
    _create_synthetic_audio(vo_file, duration_sec=1)
    _create_synthetic_audio(music_file, duration_sec=1)

    clip_asset = GeneratedAsset(id=uuid.uuid4(), project_id=project_id, asset_type="CLIP", file_path=clip_file)
    vo_asset = GeneratedAsset(id=uuid.uuid4(), project_id=project_id, asset_type="VOICEOVER", file_path=vo_file)
    music_asset = GeneratedAsset(id=uuid.uuid4(), project_id=project_id, asset_type="MUSIC", file_path=music_file)
    db_session.add_all([clip_asset, vo_asset, music_asset])
    db_session.commit()

    # CLIP stream
    res_clip = client.get(f"/api/v1/assets/{clip_asset.id}/stream")
    assert res_clip.status_code == 200
    assert res_clip.headers["content-type"] == "video/mp4"

    # VOICEOVER stream
    res_vo = client.get(f"/api/v1/assets/{vo_asset.id}/stream")
    assert res_vo.status_code == 200
    assert res_vo.headers["content-type"] in ["audio/wav", "audio/x-wav"]

    # MUSIC stream
    res_music = client.get(f"/api/v1/assets/{music_asset.id}/stream")
    assert res_music.status_code == 200
    assert res_music.headers["content-type"] in ["audio/wav", "audio/x-wav"]

