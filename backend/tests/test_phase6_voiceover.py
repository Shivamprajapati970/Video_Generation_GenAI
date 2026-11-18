import os
import uuid
import struct
import wave
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.core.config import settings
from app.models.user import User
from app.models.project import VideoProject
from app.models.job import GenerationJob
from app.schemas.job import JobStage, JobStatus
from app.models.asset import GeneratedAsset
from app.ai.hf_chatterbox import HFChatterboxProvider, verify_audio_content
from app.services.voiceover import VoiceoverService
from app.core.security import hash_password
from app.core.exceptions import ServiceUnavailableError, ValidationError, NotFoundError


def _create_dummy_wav(path: str, duration_sec: float = 1.0, sample_rate: int = 24000) -> str:
    """Helper to generate a valid non-silent test WAV file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    num_frames = int(sample_rate * duration_sec)
    with wave.open(path, "wb") as wav_out:
        wav_out.setnchannels(1)
        wav_out.setsampwidth(2)
        wav_out.setframerate(sample_rate)
        # Create a simple alternating pattern (non-silent)
        frames = bytearray()
        for i in range(num_frames):
            val = int(10000 * ((i % 100) / 50.0 - 1.0))
            frames.extend(struct.pack("<h", val))
        wav_out.writeframes(frames)
    return path


# ---------------------------------------------------------------------------
# 1. Unit Tests for HFChatterboxProvider
# ---------------------------------------------------------------------------

def test_hf_chatterbox_provider_initialization_and_space_host():
    """Verify Space host resolution and default mock settings."""
    provider = HFChatterboxProvider(space_name="ResembleAI/Chatterbox", mock_mode=True)
    assert provider._get_space_host() == "resembleai-chatterbox.hf.space"
    assert provider.effective_mock_mode is True


def test_split_text_into_chunks_short_text():
    """Verify short text under 280 characters is not split."""
    provider = HFChatterboxProvider(mock_mode=True)
    short_text = "Happy 30th Birthday Sarah! Wishing you another year of pure happiness."
    chunks = provider.split_text_into_chunks(short_text, max_chars=280)
    assert len(chunks) == 1
    assert chunks[0] == short_text


def test_split_text_into_chunks_long_text_sentence_boundary():
    """Verify long text (> 280 chars) splits along sentence boundaries without losing content."""
    provider = HFChatterboxProvider(mock_mode=True)
    sentence1 = "Welcome to the celebration of a lifetime as we honor Sarah's incredible 30th milestone."
    sentence2 = "From childhood dreams to adulthood achievements, every single memory is filled with endless love and laughter."
    sentence3 = "Here's to thirty spectacular years and to many more adventures, triumphs, and unforgettable memories ahead!"
    full_text = f"{sentence1} {sentence2} {sentence3}"
    assert len(full_text) > 280

    chunks = provider.split_text_into_chunks(full_text, max_chars=180)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 180

    # Ensure all original words are present across the chunks
    combined = " ".join(chunks)
    assert "Sarah's incredible 30th milestone" in combined
    assert "endless love and laughter" in combined
    assert "unforgettable memories ahead" in combined


def test_split_text_into_chunks_no_punctuation():
    """Verify text without punctuation splits along word boundaries cleanly."""
    provider = HFChatterboxProvider(mock_mode=True)
    words = ["word" + str(i) for i in range(100)]
    long_unpunctuated = " ".join(words)
    chunks = provider.split_text_into_chunks(long_unpunctuated, max_chars=100)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 100
        assert not chunk.endswith(" ")


def test_concatenate_wav_files(tmp_path):
    """Verify concatenation of multiple WAV files produces valid combined audio."""
    provider = HFChatterboxProvider(mock_mode=True)
    f1 = str(tmp_path / "part1.wav")
    f2 = str(tmp_path / "part2.wav")
    out = str(tmp_path / "combined.wav")

    _create_dummy_wav(f1, duration_sec=1.5, sample_rate=24000)
    _create_dummy_wav(f2, duration_sec=2.5, sample_rate=24000)

    result_path = provider.concatenate_wav_files([f1, f2], out)
    assert os.path.isfile(result_path)

    meta = provider.verify_audio_content(result_path)
    assert abs(meta["duration_seconds"] - 4.0) < 0.05
    assert meta["sample_rate"] == 24000
    assert meta["channels"] == 1


def test_verify_audio_content_valid_and_corrupt(tmp_path):
    """Verify verify_audio_content handles valid and invalid files correctly."""
    provider = HFChatterboxProvider(mock_mode=True)

    # Valid
    valid_wav = str(tmp_path / "valid.wav")
    _create_dummy_wav(valid_wav, duration_sec=2.0)
    info = provider.verify_audio_content(valid_wav)
    assert info["duration_seconds"] >= 1.9

    # Silent/zero bytes
    empty_file = str(tmp_path / "empty.wav")
    with open(empty_file, "wb") as f:
        f.write(b"")
    with pytest.raises(ServiceUnavailableError):
        provider.verify_audio_content(empty_file)


@pytest.mark.asyncio
async def test_provider_generate_tts_audio_mock(tmp_path):
    """Verify generate_tts_audio runs cleanly in mock mode and outputs audio."""
    provider = HFChatterboxProvider(mock_mode=True)
    provider.output_dir = str(tmp_path)

    out_path = await provider.generate_tts_audio(
        text="A wonderful birthday celebration filled with joy and memories.",
        filename_prefix="test_speech"
    )
    assert os.path.isfile(out_path)
    assert os.path.getsize(out_path) > 1000
    meta = provider.verify_audio_content(out_path)
    assert meta["duration_seconds"] > 0


# ---------------------------------------------------------------------------
# 2. Integration Tests for VoiceoverService
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_voiceover_service_lifecycle(db_session, tmp_path):
    """Test VoiceoverService generation, DB asset creation, and caching/force behavior."""
    test_user = db_session.query(User).filter(User.email == "phase6_unit@example.com").first()
    if not test_user:
        test_user = User(
            id=uuid.uuid4(),
            email="phase6_unit@example.com",
            hashed_password=hash_password("Password123!"),
            full_name="Phase 6 Unit User"
        )
        db_session.add(test_user)
        db_session.commit()

    project = VideoProject(
        id=uuid.uuid4(),
        user_id=test_user.id,
        title="Voiceover Test Project",
        aspect_ratio="9:16",
        duration_seconds=15,
        scene_plan={
            "full_narration_script": "Celebrating 30 unforgettable years with family and friends. Here's to love and life!",
            "opening_narration": "Celebrating 30 unforgettable years.",
            "closing_narration": "Here's to love and life!",
            "scenes": [
                {"scene_number": 1, "duration_seconds": 5, "narration_text": "Celebrating 30 years"},
                {"scene_number": 2, "duration_seconds": 5, "narration_text": "With family and friends"},
                {"scene_number": 3, "duration_seconds": 5, "narration_text": "Here's to love and life!"},
            ]
        }
    )
    db_session.add(project)
    db_session.commit()

    service = VoiceoverService()
    # Mock the provider inside service to write to tmp_path
    service.tts_provider.output_dir = str(tmp_path)
    service.tts_provider._mock_mode_param = True

    # Initial generation
    job = await service.generate_project_voiceover(
        project_id=project.id,
        user_id=test_user.id,
        db_session_factory=lambda: db_session,
        force_regenerate=False,
    )
    assert job.status == JobStatus.COMPLETED
    assert job.stage == JobStage.GENERATING_VOICEOVER
    assert job.progress_percent == 100

    # Verify GeneratedAsset
    asset = service.get_existing_voiceover_asset(project.id, db_session)
    assert asset is not None
    assert asset.asset_type == "VOICEOVER"
    assert asset.duration_seconds > 0
    assert asset.file_size > 0
    assert os.path.isfile(asset.file_path)

    # Calling again without force should reuse the asset
    second_job = await service.generate_project_voiceover(
        project_id=project.id,
        user_id=test_user.id,
        db_session_factory=lambda: db_session,
        force_regenerate=False,
    )
    assert second_job.status == JobStatus.COMPLETED

    # Calling with force=True should regenerate
    force_job = await service.generate_project_voiceover(
        project_id=project.id,
        user_id=test_user.id,
        db_session_factory=lambda: db_session,
        force_regenerate=True,
    )
    assert force_job.status == JobStatus.COMPLETED


# ---------------------------------------------------------------------------
# 3. API Route Tests: POST & GET Endpoints
# ---------------------------------------------------------------------------

def test_api_generate_and_get_voiceover(client, db_session, tmp_path):
    """Test POST /projects/{id}/generate-voiceover and GET /projects/{id}/voiceover."""
    client.cookies.clear()
    reg_res = client.post("/api/v1/auth/register", json={
        "email": "phase6api@example.com",
        "password": "Password123!",
        "full_name": "Phase 6 API Tester"
    })
    if reg_res.status_code == 409:
        client.post("/api/v1/auth/login", json={
            "email": "phase6api@example.com",
            "password": "Password123!"
        })

    proj_res = client.post("/api/v1/projects", json={
        "title": "API Voiceover Project",
        "occasion": "Celebration",
        "duration_seconds": 15,
        "aspect_ratio": "9:16",
        "music_mood": "Happy"
    })
    assert proj_res.status_code == 201
    project_id = proj_res.json()["id"]

    # 1. GET before generation should return 404
    get_res = client.get(f"/api/v1/projects/{project_id}/voiceover")
    assert get_res.status_code == 404

    # 2. Attempt generating voiceover without scene plan: returns 400
    fail_gen = client.post(f"/api/v1/projects/{project_id}/generate-voiceover")
    assert fail_gen.status_code == 400

    # 3. Add scene plan to project in database
    proj_uuid = uuid.UUID(project_id)
    project_db = db_session.query(VideoProject).filter(VideoProject.id == proj_uuid).first()
    project_db.scene_plan = {
        "full_narration_script": "A wonderful journey captured across five special moments.",
        "scenes": [
            {"scene_number": 1, "duration_seconds": 5, "narration_text": "A wonderful journey"},
            {"scene_number": 2, "duration_seconds": 5, "narration_text": "Captured across five moments"},
        ]
    }
    db_session.commit()

    # 4. Trigger generation via POST (HTTP 202 Accepted)
    with patch("app.services.voiceover.VoiceoverService.generate_project_voiceover") as mock_gen:
        post_res = client.post(
            f"/api/v1/projects/{project_id}/generate-voiceover",
            json={"force": False}
        )
        assert post_res.status_code == 202
        data = post_res.json()
        assert data["stage"] == "GENERATING_VOICEOVER"
        assert data["status"] in ["QUEUED", "RUNNING"]

    # 5. Create a mock voiceover asset in DB and verify GET /projects/{id}/voiceover
    audio_file = str(tmp_path / f"voiceover_{project_id}.wav")
    _create_dummy_wav(audio_file, duration_sec=3.5, sample_rate=24000)

    asset = GeneratedAsset(
        id=uuid.uuid4(),
        project_id=proj_uuid,
        asset_type="VOICEOVER",
        file_path=audio_file,
        file_size=os.path.getsize(audio_file),
        duration_seconds=3.5,
        meta_data={"sample_rate": 24000, "channels": 1, "format": "WAV"}
    )
    db_session.add(asset)
    db_session.commit()

    get_res_2 = client.get(f"/api/v1/projects/{project_id}/voiceover")
    assert get_res_2.status_code == 200
    asset_data = get_res_2.json()
    assert asset_data["id"] == str(asset.id)
    assert asset_data["asset_type"] == "VOICEOVER"
    assert asset_data["duration_seconds"] == 3.5
    assert asset_data["sample_rate"] == 24000
    assert asset_data["stream_url"] == f"/api/v1/assets/{asset.id}/stream"


def test_api_stream_voiceover_range_and_full(client, db_session, tmp_path):
    """Test GET /api/v1/assets/{id}/stream with standard and Range requests."""
    client.cookies.clear()
    reg_res = client.post("/api/v1/auth/register", json={
        "email": "phase6stream@example.com",
        "password": "Password123!",
        "full_name": "Phase 6 Stream Tester"
    })
    if reg_res.status_code == 409:
        client.post("/api/v1/auth/login", json={
            "email": "phase6stream@example.com",
            "password": "Password123!"
        })

    proj_res = client.post("/api/v1/projects", json={
        "title": "Stream Voiceover Project",
        "occasion": "Celebration",
        "duration_seconds": 15,
        "aspect_ratio": "9:16",
        "music_mood": "Calm"
    })
    assert proj_res.status_code == 201
    project_id = uuid.UUID(proj_res.json()["id"])

    audio_file = str(tmp_path / "stream_test.wav")
    _create_dummy_wav(audio_file, duration_sec=2.0, sample_rate=24000)

    asset = GeneratedAsset(
        id=uuid.uuid4(),
        project_id=project_id,
        asset_type="VOICEOVER",
        file_path=audio_file,
        file_size=os.path.getsize(audio_file),
        duration_seconds=2.0,
        meta_data={"sample_rate": 24000, "channels": 1}
    )
    db_session.add(asset)
    db_session.commit()

    # Full request
    res = client.get(f"/api/v1/assets/{asset.id}/stream")
    assert res.status_code == 200
    assert res.headers["content-type"] in ["audio/wav", "audio/x-wav"]
    assert int(res.headers["content-length"]) == os.path.getsize(audio_file)

    # Range request (bytes=0-100)
    res_range = client.get(
        f"/api/v1/assets/{asset.id}/stream",
        headers={"Range": "bytes=0-100"}
    )
    assert res_range.status_code == 206
    assert "bytes 0-100/" in res_range.headers["content-range"]
    assert len(res_range.content) == 101


@pytest.mark.asyncio
async def test_generate_voiceover_non_mock_flow(tmp_path):
    """Verify generate_voiceover executes chunk synthesis and concatenation without returning None."""
    provider = HFChatterboxProvider(mock_mode=False)
    provider.output_dir = str(tmp_path)

    # Mock _synthesize_single_chunk to simulate successful chunk audio generation
    chunk_file = str(tmp_path / "dummy_chunk.wav")
    _create_dummy_wav(chunk_file, duration_sec=2.0, sample_rate=24000)

    with patch.object(provider, "_synthesize_single_chunk", return_value=chunk_file):
        out_path = await provider.generate_voiceover(
            narration_text="Happy birthday to our wonderful friend! May this year bring endless happiness.",
            mood="happy"
        )
        assert out_path is not None
        assert isinstance(out_path, str)
        assert os.path.isfile(out_path)
        assert os.path.getsize(out_path) > 44


def test_verify_audio_content_rejects_none_safely():
    """Verify verify_audio_content gracefully raises ServiceUnavailableError when path is None."""
    with pytest.raises(ServiceUnavailableError, match="Audio file path is invalid"):
        verify_audio_content(None)
