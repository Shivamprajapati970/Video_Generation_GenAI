import os
import uuid
import struct
import wave
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

from app.core.config import settings
from app.models.user import User
from app.models.project import VideoProject
from app.models.job import GenerationJob
from app.schemas.job import JobStage, JobStatus
from app.models.asset import GeneratedAsset
from app.ai.hf_acestep import HFAceStepProvider, build_music_prompt, verify_music_content
from app.services.music import MusicService
from app.services.composer import VideoCompositionService
from app.core.security import hash_password
from app.core.exceptions import ServiceUnavailableError, ValidationError, NotFoundError, BadRequestError


def _create_test_wav(path: str, duration_sec: float = 2.0, sample_rate: int = 44100, channels: int = 2) -> str:
    """Helper to generate a valid non-silent stereo test WAV file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    num_frames = int(sample_rate * duration_sec)
    with wave.open(path, "wb") as wav_out:
        wav_out.setnchannels(channels)
        wav_out.setsampwidth(2)
        wav_out.setframerate(sample_rate)
        frames = bytearray()
        for i in range(num_frames):
            val = int(8000 * ((i % 100) / 50.0 - 1.0))
            frame_sample = struct.pack("<h", val)
            for _ in range(channels):
                frames.extend(frame_sample)
        wav_out.writeframes(frames)
    return path


# ---------------------------------------------------------------------------
# 1. Unit Tests for HFAceStepProvider
# ---------------------------------------------------------------------------

def test_hf_acestep_provider_initialization_and_space_host():
    """Verify Space host resolution and default mock settings."""
    provider = HFAceStepProvider(space_name="ACE-Step/ACE-Step", mock_mode=True)
    assert provider._get_space_host() == "ace-step-ace-step.hf.space"
    assert provider.effective_mock_mode is True


def test_build_music_prompt():
    """Verify prompt and tag construction for various moods and custom prompts."""
    # 1. Standard predefined mood
    prompt = build_music_prompt(mood="Cinematic")
    assert "cinematic" in prompt.lower()
    assert "soundtrack" in prompt.lower()

    # 2. Upbeat / Happy mood
    prompt_happy = build_music_prompt(mood="Happy")
    assert "happy" in prompt_happy.lower()

    # 3. Custom prompt supplied
    custom = "Lo-fi cozy study beats with gentle rain in background"
    prompt_custom = build_music_prompt(mood="Chill", custom_prompt=custom)
    assert "chill" in prompt_custom.lower()
    assert "Lo-fi cozy" in prompt_custom

    # 4. Unknown mood fallback
    prompt_fallback = build_music_prompt(mood="NonExistentMood")
    assert "nonexistentmood" in prompt_fallback.lower()
    assert len(prompt_fallback) > 0


def test_verify_music_content_valid_and_invalid(tmp_path):
    """Verify audio file validation: valid stereo, silent, missing, and too short."""
    # 1. Valid stereo audio
    valid_path = str(tmp_path / "valid.wav")
    _create_test_wav(valid_path, duration_sec=2.5, sample_rate=44100, channels=2)
    meta = verify_music_content(valid_path)
    assert meta["channels"] == 2
    assert meta["sample_rate"] == 44100
    assert meta["duration_seconds"] >= 2.4

    # 2. Missing file
    with pytest.raises(ServiceUnavailableError, match="not created"):
        verify_music_content(str(tmp_path / "missing.wav"))

    # 3. Digitally silent file (all zero PCM bytes)
    silent_path = str(tmp_path / "silent.wav")
    with wave.open(silent_path, "wb") as wav_out:
        wav_out.setnchannels(2)
        wav_out.setsampwidth(2)
        wav_out.setframerate(44100)
        wav_out.writeframes(b"\x00" * 44100 * 4)  # 1 second of digital silence
    with pytest.raises(ServiceUnavailableError, match="digital silence"):
        verify_music_content(silent_path)

    # 4. Too short file (< 0.5s)
    short_path = str(tmp_path / "short.wav")
    _create_test_wav(short_path, duration_sec=0.2, sample_rate=44100, channels=2)
    with pytest.raises(ServiceUnavailableError, match="unnaturally short"):
        verify_music_content(short_path)


def test_generate_mock_audio(tmp_path):
    """Verify mock audio generator outputs valid stereo 44.1kHz WAV."""
    provider = HFAceStepProvider(mock_mode=True)
    out_path = str(tmp_path / "mock_output.wav")
    provider._generate_mock_audio(out_path, duration=3)

    assert os.path.isfile(out_path)
    meta = verify_music_content(out_path)
    assert meta["channels"] == 2
    assert meta["sample_rate"] == 44100
    assert meta["duration_seconds"] >= 2.9


@pytest.mark.asyncio
async def test_generate_music_error_handling_live():
    """Verify provider raises ServiceUnavailableError on live API failures (no silent fallback)."""
    provider = HFAceStepProvider(mock_mode=False)

    # Simulate network connection error to Space
    with patch("httpx.AsyncClient.post", side_effect=Exception("Connection reset by peer")):
        with pytest.raises(ServiceUnavailableError, match="ACE-Step"):
            await provider.generate_music(
                mood="Cinematic",
                duration_seconds=15
            )


# ---------------------------------------------------------------------------
# 2. Integration Tests for MusicService
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_music_service_generate_and_idempotency(db_session, tmp_path):
    """Test MusicService generation, DB asset creation, idempotency, and force regeneration."""
    test_user = db_session.query(User).filter(User.email == "phase7_unit@example.com").first()
    if not test_user:
        test_user = User(
            id=uuid.uuid4(),
            email="phase7_unit@example.com",
            hashed_password=hash_password("Password123!"),
            full_name="Phase 7 Unit User"
        )
        db_session.add(test_user)
        db_session.commit()

    project = VideoProject(
        id=uuid.uuid4(),
        user_id=test_user.id,
        title="Music Test Project",
        aspect_ratio="9:16",
        duration_seconds=15,
        music_mood="Cinematic",
        scene_plan={
            "music_mood": "Cinematic",
            "background_music_prompt": "Majestic brass and soaring strings celebrating a grand milestone",
            "scenes": [
                {"scene_number": 1, "duration_seconds": 5},
                {"scene_number": 2, "duration_seconds": 5},
                {"scene_number": 3, "duration_seconds": 5},
            ]
        }
    )
    db_session.add(project)
    db_session.commit()

    service = MusicService()
    service.music_provider.output_dir = str(tmp_path)
    service.music_provider._mock_mode_param = True

    # 1. Initial generation
    job = await service.generate_project_music(
        project_id=project.id,
        user_id=test_user.id,
        db_session_factory=lambda: db_session,
        force_regenerate=False,
    )
    assert job.status == JobStatus.COMPLETED
    assert job.stage == JobStage.GENERATING_MUSIC
    assert job.progress_percent == 100

    # Verify GeneratedAsset record
    asset = service.get_existing_music_asset(project.id, db_session)
    assert asset is not None
    assert asset.asset_type == "MUSIC"
    assert asset.duration_seconds > 0
    assert asset.file_size > 0
    assert os.path.isfile(asset.file_path)
    assert asset.meta_data.get("music_mood") == "Cinematic"

    # 2. Calling again without force should reuse the existing asset
    second_job = await service.generate_project_music(
        project_id=project.id,
        user_id=test_user.id,
        db_session_factory=lambda: db_session,
        force_regenerate=False,
    )
    assert second_job.status == JobStatus.COMPLETED
    assert second_job.progress_percent == 100

    # 3. Calling with force=True should regenerate and replace
    force_job = await service.generate_project_music(
        project_id=project.id,
        user_id=test_user.id,
        db_session_factory=lambda: db_session,
        force_regenerate=True,
    )
    assert force_job.status == JobStatus.COMPLETED

    # Verify asset is still present and valid
    refreshed_asset = service.get_existing_music_asset(project.id, db_session)
    assert refreshed_asset is not None
    assert refreshed_asset.id != asset.id


# ---------------------------------------------------------------------------
# 3. API Route Tests: POST & GET Endpoints
# ---------------------------------------------------------------------------

def test_api_generate_and_get_music(client, db_session, tmp_path):
    """Test POST /projects/{id}/generate-music and GET /projects/{id}/music."""
    client.cookies.clear()
    unique_email = f"phase7api_{uuid.uuid4().hex[:6]}@example.com"
    reg_res = client.post("/api/v1/auth/register", json={
        "email": unique_email,
        "password": "Password123!",
        "full_name": "Phase 7 API Tester"
    })
    assert reg_res.status_code == 201

    proj_res = client.post("/api/v1/projects", json={
        "title": "API Music Project",
        "occasion": "Birthday",
        "duration_seconds": 15,
        "aspect_ratio": "9:16",
        "music_mood": "Happy"
    })
    assert proj_res.status_code == 201
    project_id = proj_res.json()["id"]

    # 1. GET before generation should return 404
    get_res = client.get(f"/api/v1/projects/{project_id}/music")
    assert get_res.status_code == 404

    # 2. Trigger generation via POST (HTTP 202 Accepted)
    with patch("app.services.music.MusicService.generate_project_music") as mock_gen:
        post_res = client.post(
            f"/api/v1/projects/{project_id}/generate-music?force=false"
        )
        assert post_res.status_code == 202
        data = post_res.json()
        assert data["stage"] == "GENERATING_MUSIC"
        assert data["status"] in ["QUEUED", "RUNNING"]

    # 3. Create a mock music asset in DB and verify GET /projects/{id}/music
    proj_uuid = uuid.UUID(project_id)
    audio_file = str(tmp_path / f"music_{project_id}.wav")
    _create_test_wav(audio_file, duration_sec=15.0, sample_rate=44100, channels=2)

    asset = GeneratedAsset(
        id=uuid.uuid4(),
        project_id=proj_uuid,
        asset_type="MUSIC",
        file_path=audio_file,
        file_size=os.path.getsize(audio_file),
        duration_seconds=15.0,
        meta_data={"sample_rate": 44100, "channels": 2, "format": "WAV", "music_mood": "Happy"}
    )
    db_session.add(asset)
    db_session.commit()

    get_res_2 = client.get(f"/api/v1/projects/{project_id}/music")
    assert get_res_2.status_code == 200
    music_data = get_res_2.json()
    assert music_data["asset_type"] == "MUSIC"
    assert music_data["duration_seconds"] == 15.0
    assert music_data["music_mood"] == "Happy"
    assert f"/api/v1/assets/{asset.id}/stream" in music_data["stream_url"]


def test_api_music_ownership_and_security(client, db_session):
    """Verify user cannot generate music or view music for another user's project."""
    email_a = f"user_a_{uuid.uuid4().hex[:6]}@example.com"
    client.cookies.clear()
    reg_a = client.post("/api/v1/auth/register", json={
        "email": email_a,
        "password": "Password123!",
        "full_name": "User A"
    })
    assert reg_a.status_code == 201

    proj_res = client.post("/api/v1/projects", json={
        "title": "User A Music Project",
        "occasion": "Birthday",
        "duration_seconds": 15,
        "aspect_ratio": "9:16",
        "music_mood": "Cinematic"
    })
    assert proj_res.status_code == 201
    proj_a_id = proj_res.json()["id"]

    # Register User B
    email_b = f"user_b_{uuid.uuid4().hex[:6]}@example.com"
    client.cookies.clear()
    reg_b = client.post("/api/v1/auth/register", json={
        "email": email_b,
        "password": "Password123!",
        "full_name": "User B"
    })
    assert reg_b.status_code == 201

    # User B tries to trigger music generation for User A's project (403 Forbidden)
    gen_res = client.post(f"/api/v1/projects/{proj_a_id}/generate-music")
    assert gen_res.status_code == 403

    # User B tries to get music for User A's project (403 Forbidden)
    get_res = client.get(f"/api/v1/projects/{proj_a_id}/music")
    assert get_res.status_code == 403

    # Non-existent project returns 404
    non_existent = uuid.uuid4()
    assert client.get(f"/api/v1/projects/{non_existent}/music").status_code == 404
    assert client.post(f"/api/v1/projects/{non_existent}/generate-music").status_code == 404


def test_api_stream_music(client, db_session, tmp_path):
    """Test GET /api/v1/assets/{id}/stream with music WAV asset."""
    client.cookies.clear()
    unique_email = f"phase7stream_{uuid.uuid4().hex[:6]}@example.com"
    reg_res = client.post("/api/v1/auth/register", json={
        "email": unique_email,
        "password": "Password123!",
        "full_name": "Phase 7 Stream Tester"
    })
    assert reg_res.status_code == 201

    proj_res = client.post("/api/v1/projects", json={
        "title": "Stream Music Project",
        "occasion": "Celebration",
        "duration_seconds": 15,
        "aspect_ratio": "9:16",
        "music_mood": "Cinematic"
    })
    assert proj_res.status_code == 201
    project_id = uuid.UUID(proj_res.json()["id"])

    audio_file = str(tmp_path / "stream_music_test.wav")
    _create_test_wav(audio_file, duration_sec=5.0, sample_rate=44100, channels=2)

    asset = GeneratedAsset(
        id=uuid.uuid4(),
        project_id=project_id,
        asset_type="MUSIC",
        file_path=audio_file,
        file_size=os.path.getsize(audio_file),
        duration_seconds=5.0,
        meta_data={"sample_rate": 44100, "channels": 2, "format": "WAV"}
    )
    db_session.add(asset)
    db_session.commit()

    # Full stream request
    res = client.get(f"/api/v1/assets/{asset.id}/stream")
    assert res.status_code == 200
    assert res.headers["content-type"] in ["audio/wav", "audio/x-wav"]
    assert int(res.headers["content-length"]) == os.path.getsize(audio_file)


# ---------------------------------------------------------------------------
# 4. Acceptance Criteria Tests: Conditional Music Selection & AI Generation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_criterion_1_selecting_predefined_music_does_not_call_acestep(db_session, tmp_path):
    """Criterion 1: Selecting predefined music uses static track and NEVER calls ACE-Step."""
    test_user = db_session.query(User).filter(User.email == "crit1@example.com").first()
    if not test_user:
        test_user = User(
            id=uuid.uuid4(),
            email="crit1@example.com",
            hashed_password=hash_password("Password123!"),
            full_name="Crit 1 User"
        )
        db_session.add(test_user)
        db_session.commit()

    project = VideoProject(
        id=uuid.uuid4(),
        user_id=test_user.id,
        title="Predefined Music Project",
        occasion="Birthday",
        duration_seconds=15,
        aspect_ratio="9:16",
        music_mood="Happy",
        scene_plan={"music_mood": "Happy"}
    )
    db_session.add(project)
    db_session.commit()

    mock_acestep = MagicMock(spec=HFAceStepProvider)
    mock_acestep.generate_music = AsyncMock()

    service = MusicService(music_provider=mock_acestep)

    # 1. User selects predefined music track
    asset = service.select_library_track_for_project(
        project_id=project.id,
        user_id=test_user.id,
        track_id="happy_birthday_acoustic",
        db=db_session
    )
    assert asset is not None
    assert asset.meta_data.get("provider") == "static_library"

    # Verify ACE-Step was NOT called during selection
    mock_acestep.generate_music.assert_not_called()

    # 2. Trigger generate_project_music for this project
    job = await service.generate_project_music(
        project_id=project.id,
        user_id=test_user.id,
        db_session_factory=lambda: db_session,
        force_regenerate=False,
    )

    # Verify job completed using the predefined static track and ACE-Step was still NEVER called
    assert job.status == JobStatus.COMPLETED
    assert job.progress_percent == 100
    assert job.external_provider_ids.get("provider") == "static_library"
    assert job.external_provider_ids.get("track_id") == "happy_birthday_acoustic"
    mock_acestep.generate_music.assert_not_called()


def test_criterion_2_selected_library_track_saved_correctly_with_metadata(db_session):
    """Criterion 2: Selected library track is saved with complete metadata and scene_plan linkage."""
    test_user = db_session.query(User).filter(User.email == "crit2@example.com").first()
    if not test_user:
        test_user = User(
            id=uuid.uuid4(),
            email="crit2@example.com",
            hashed_password=hash_password("Password123!"),
            full_name="Crit 2 User"
        )
        db_session.add(test_user)
        db_session.commit()

    project = VideoProject(
        id=uuid.uuid4(),
        user_id=test_user.id,
        title="Metadata Check Project",
        occasion="Birthday",
        duration_seconds=15,
        aspect_ratio="9:16",
        music_mood="Happy",
        scene_plan={"music_mood": "Happy"}
    )
    db_session.add(project)
    db_session.commit()

    service = MusicService()
    asset = service.select_library_track_for_project(
        project_id=project.id,
        user_id=test_user.id,
        track_id="happy_birthday_acoustic",
        db=db_session
    )

    # Verify GeneratedAsset record
    assert asset.asset_type == "MUSIC"
    assert asset.project_id == project.id
    assert asset.duration_seconds > 0
    assert asset.file_size > 0
    assert os.path.isfile(asset.file_path)

    # Verify metadata fields
    meta = asset.meta_data
    assert meta["provider"] == "static_library"
    assert meta["track_id"] == "happy_birthday_acoustic"
    assert meta["title"] == "Happy Birthday Acoustic Motif"
    assert meta["category"] == "birthday"
    assert meta["format"] == "wav"
    assert meta["sample_rate"] == 44100
    assert meta["channels"] == 2
    assert "license" in meta

    # Verify project scene_plan records selected_music_track_id
    db_session.refresh(project)
    assert project.scene_plan.get("selected_music_track_id") == "happy_birthday_acoustic"


@pytest.mark.asyncio
async def test_criterion_3_no_predefined_selection_triggers_acestep(db_session, tmp_path):
    """Criterion 3: When no predefined music is selected, ACE-Step AI generation is automatically invoked."""
    test_user = db_session.query(User).filter(User.email == "crit3@example.com").first()
    if not test_user:
        test_user = User(
            id=uuid.uuid4(),
            email="crit3@example.com",
            hashed_password=hash_password("Password123!"),
            full_name="Crit 3 User"
        )
        db_session.add(test_user)
        db_session.commit()

    project = VideoProject(
        id=uuid.uuid4(),
        user_id=test_user.id,
        title="AI Music Project",
        occasion="Celebration",
        duration_seconds=15,
        aspect_ratio="9:16",
        music_mood="Cinematic",
        scene_plan={
            "music_mood": "Cinematic",
            "background_music_prompt": "Epic orchestral score with soaring brass",
        }
    )
    db_session.add(project)
    db_session.commit()

    # Pre-generate valid WAV that the mock will return
    mock_audio = str(tmp_path / "acestep_crit3.wav")
    _create_test_wav(mock_audio, duration_sec=15.0, sample_rate=44100, channels=2)

    mock_acestep = MagicMock(spec=HFAceStepProvider)
    mock_acestep.generate_music = AsyncMock(return_value=mock_audio)

    service = MusicService(music_provider=mock_acestep)

    # Ensure no predefined track is selected
    assert service.has_predefined_music_selected(project, db_session) is None

    job = await service.generate_project_music(
        project_id=project.id,
        user_id=test_user.id,
        db_session_factory=lambda: db_session,
        force_regenerate=False,
    )

    # Verify ACE-Step was called once with the project's parameters
    mock_acestep.generate_music.assert_called_once()
    call_kwargs = mock_acestep.generate_music.call_args.kwargs
    assert call_kwargs["mood"] == "Cinematic"
    assert "Epic orchestral score" in call_kwargs["custom_prompt"]
    assert call_kwargs["duration_seconds"] == 15
    assert job.status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_criterion_4_successful_ai_generation_saves_music_asset(db_session, tmp_path):
    """Criterion 4: Successful AI generation saves generated music asset and completes job."""
    test_user = db_session.query(User).filter(User.email == "crit4@example.com").first()
    if not test_user:
        test_user = User(
            id=uuid.uuid4(),
            email="crit4@example.com",
            hashed_password=hash_password("Password123!"),
            full_name="Crit 4 User"
        )
        db_session.add(test_user)
        db_session.commit()

    project = VideoProject(
        id=uuid.uuid4(),
        user_id=test_user.id,
        title="Asset Save AI Project",
        occasion="Anniversary",
        duration_seconds=15,
        aspect_ratio="9:16",
        music_mood="Romantic",
        scene_plan={"music_mood": "Romantic"}
    )
    db_session.add(project)
    db_session.commit()

    mock_audio = str(tmp_path / "acestep_crit4.wav")
    _create_test_wav(mock_audio, duration_sec=15.0, sample_rate=44100, channels=2)

    mock_acestep = MagicMock(spec=HFAceStepProvider)
    mock_acestep.generate_music = AsyncMock(return_value=mock_audio)

    service = MusicService(music_provider=mock_acestep)
    job = await service.generate_project_music(
        project_id=project.id,
        user_id=test_user.id,
        db_session_factory=lambda: db_session,
    )

    assert job.status == JobStatus.COMPLETED
    assert job.progress_percent == 100

    asset = service.get_existing_music_asset(project.id, db_session)
    assert asset is not None
    assert asset.asset_type == "MUSIC"
    assert asset.meta_data.get("provider") == "ACE-Step/ACE-Step"
    assert asset.meta_data.get("music_mood") == "Romantic"
    assert asset.duration_seconds == pytest.approx(15.0, rel=0.1)
    assert os.path.isfile(asset.file_path)


@pytest.mark.asyncio
async def test_criterion_5_acestep_failure_reported_correctly_no_silent_fallback(db_session):
    """Criterion 5: ACE-Step failure marks job FAILED and raises without silent fallback to static music."""
    test_user = db_session.query(User).filter(User.email == "crit5@example.com").first()
    if not test_user:
        test_user = User(
            id=uuid.uuid4(),
            email="crit5@example.com",
            hashed_password=hash_password("Password123!"),
            full_name="Crit 5 User"
        )
        db_session.add(test_user)
        db_session.commit()

    project = VideoProject(
        id=uuid.uuid4(),
        user_id=test_user.id,
        title="Failing AI Project",
        occasion="Birthday",
        duration_seconds=15,
        aspect_ratio="9:16",
        music_mood="Happy",
        scene_plan={"music_mood": "Happy"}
    )
    db_session.add(project)
    db_session.commit()

    # Mock ACE-Step provider that fails with ServiceUnavailableError
    mock_acestep = MagicMock(spec=HFAceStepProvider)
    mock_acestep.generate_music = AsyncMock(
        side_effect=ServiceUnavailableError(detail="Hugging Face Space ZeroGPU queue exhausted")
    )

    service = MusicService(music_provider=mock_acestep)

    # Must raise the error and NOT silently fall back to static library
    with pytest.raises(ServiceUnavailableError, match="ZeroGPU queue exhausted"):
        await service.generate_project_music(
            project_id=project.id,
            user_id=test_user.id,
            db_session_factory=lambda: db_session,
        )

    # Verify job status in DB is FAILED
    job = db_session.query(GenerationJob).filter(
        GenerationJob.project_id == project.id,
        GenerationJob.stage == "GENERATING_MUSIC"
    ).order_by(GenerationJob.created_at.desc()).first()

    assert job is not None
    assert job.status == JobStatus.FAILED
    assert "ZeroGPU queue exhausted" in (job.error_message or "")

    # Confirm NO static library fallback asset was created
    asset = service.get_existing_music_asset(project.id, db_session)
    assert asset is None


def test_criterion_6_duplicate_requests_prevented(client, db_session):
    """Criterion 6: Duplicate requests while job is running/queued are rejected with HTTP 400."""
    client.cookies.clear()
    unique_email = f"crit6_{uuid.uuid4().hex[:6]}@example.com"
    reg_res = client.post("/api/v1/auth/register", json={
        "email": unique_email,
        "password": "Password123!",
        "full_name": "Crit 6 Tester"
    })
    assert reg_res.status_code == 201

    proj_res = client.post("/api/v1/projects", json={
        "title": "Duplicate Guard Project",
        "occasion": "Birthday",
        "duration_seconds": 15,
        "aspect_ratio": "9:16",
        "music_mood": "Happy"
    })
    assert proj_res.status_code == 201
    project_id = proj_res.json()["id"]

    # Insert an active running music job
    active_job = GenerationJob(
        project_id=uuid.UUID(project_id),
        stage="GENERATING_MUSIC",
        status="RUNNING",
        progress_percent=40,
    )
    db_session.add(active_job)
    db_session.commit()

    # Attempt to trigger duplicate generation -> must return HTTP 400 BadRequest
    dup_res = client.post(f"/api/v1/projects/{project_id}/generate-music")
    assert dup_res.status_code == 400
    res_json = dup_res.json()
    error_msg = res_json.get("error", {}).get("message") or res_json.get("detail", "")
    assert "already in progress" in error_msg.lower()


def test_criterion_7_ffmpeg_composition_resolves_selected_or_generated_music(db_session, tmp_path):
    """Criterion 7: FFmpeg composition pipeline successfully resolves selected library or generated music."""
    test_user = db_session.query(User).filter(User.email == "crit7@example.com").first()
    if not test_user:
        test_user = User(
            id=uuid.uuid4(),
            email="crit7@example.com",
            hashed_password=hash_password("Password123!"),
            full_name="Crit 7 User"
        )
        db_session.add(test_user)
        db_session.commit()

    project = VideoProject(
        id=uuid.uuid4(),
        user_id=test_user.id,
        title="Composition Resolution Project",
        occasion="Birthday",
        duration_seconds=15,
        aspect_ratio="9:16",
        music_mood="Birthday",
        scene_plan={"music_mood": "Birthday", "scenes": [{"scene_number": 1}]}
    )
    db_session.add(project)
    db_session.commit()

    music_service = MusicService()

    # Case A: Static Library Track Selected
    static_asset = music_service.select_library_track_for_project(
        project_id=project.id,
        user_id=test_user.id,
        track_id="happy_birthday_acoustic",
        db=db_session
    )

    composer = VideoCompositionService()
    resolved_static_path = composer._resolve_file_path(static_asset.file_path)
    assert resolved_static_path is not None
    assert os.path.isfile(resolved_static_path)
    assert "happy_birthday_acoustic" in resolved_static_path

    # Case B: AI Generated Track
    ai_wav = str(tmp_path / "ai_resolved.wav")
    _create_test_wav(ai_wav, duration_sec=15.0, sample_rate=44100, channels=2)
    ai_asset = GeneratedAsset(
        id=uuid.uuid4(),
        project_id=project.id,
        asset_type="MUSIC",
        file_path=ai_wav,
        file_size=os.path.getsize(ai_wav),
        duration_seconds=15.0,
        meta_data={"provider": "ACE-Step/ACE-Step", "music_mood": "Birthday"}
    )
    db_session.add(ai_asset)
    db_session.commit()

    # The latest asset is now the AI asset
    resolved_ai_path = composer._resolve_file_path(ai_asset.file_path)
    assert resolved_ai_path is not None
    assert os.path.isfile(resolved_ai_path)
    assert os.path.samefile(resolved_ai_path, ai_wav)


def test_unselect_music_and_switch_workflow(client, db_session):
    """Verify selecting a static track, then clearing/unselecting it via API."""
    client.cookies.clear()
    unique_email = f"unselect_{uuid.uuid4().hex[:6]}@example.com"
    reg_res = client.post("/api/v1/auth/register", json={
        "email": unique_email,
        "password": "Password123!",
        "full_name": "Unselect Tester"
    })
    assert reg_res.status_code == 201

    proj_res = client.post("/api/v1/projects", json={
        "title": "Switch Test Project",
        "occasion": "Birthday",
        "duration_seconds": 15,
        "aspect_ratio": "9:16",
        "music_mood": "Happy"
    })
    assert proj_res.status_code == 201
    project_id = proj_res.json()["id"]

    # 1. Select predefined music track
    sel_res = client.post(
        f"/api/v1/projects/{project_id}/select-music",
        json={"track_id": "happy_birthday_acoustic", "provider": "library"}
    )
    assert sel_res.status_code == 200
    assert sel_res.json()["provider"] == "static_library"
    assert sel_res.json()["track_id"] == "happy_birthday_acoustic"

    # 2. Clear predefined selection by passing track_id: null
    clear_res = client.post(
        f"/api/v1/projects/{project_id}/select-music",
        json={"track_id": None, "provider": "library"}
    )
    assert clear_res.status_code == 200
    assert clear_res.json() is None

    # Verify GET /music returns 404 now that selection is cleared
    get_res = client.get(f"/api/v1/projects/{project_id}/music")
    assert get_res.status_code == 404

    # 3. Test DELETE /music endpoint
    # Re-select
    client.post(
        f"/api/v1/projects/{project_id}/select-music",
        json={"track_id": "happy_birthday_acoustic", "provider": "library"}
    )
    del_res = client.delete(f"/api/v1/projects/{project_id}/music")
    assert del_res.status_code == 204
    assert client.get(f"/api/v1/projects/{project_id}/music").status_code == 404
