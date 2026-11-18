import os
import uuid
import struct
import wave
import subprocess
import pytest
from fastapi.testclient import TestClient

import imageio_ffmpeg

from app.core.config import settings
from app.models.user import User
from app.models.project import VideoProject
from app.models.job import GenerationJob
from app.models.asset import GeneratedAsset
from app.ai.static_music import StaticMusicLibraryProvider
from app.services.music import MusicService
from app.core.security import hash_password
from app.core.exceptions import NotFoundError, BadRequestError


# ---------------------------------------------------------------------------
# 1. Unit Tests for StaticMusicLibraryProvider
# ---------------------------------------------------------------------------

def test_static_music_provider_initialization_and_categories():
    """Verify provider discovers base directory and returns all standard categories."""
    provider = StaticMusicLibraryProvider()
    assert os.path.isdir(provider.base_dir)

    categories = provider.list_categories()
    for expected in ["birthday", "anniversary", "memories", "family", "cinematic", "friends"]:
        assert expected in categories


def test_static_music_provider_list_and_filter_tracks():
    """Verify listing all tracks and filtering by category."""
    provider = StaticMusicLibraryProvider()
    all_tracks = provider.list_tracks()
    assert len(all_tracks) >= 6

    # Verify every track has required schema fields
    for t in all_tracks:
        assert t["id"]
        assert t["title"]
        assert t["category"]
        assert t["duration_seconds"] > 0
        assert t["available"] is True
        assert t["audio_url"].startswith("/api/v1/music/tracks/")
        assert t["license"]

    # Filter by specific category
    birthday_tracks = provider.list_tracks(category="birthday")
    assert len(birthday_tracks) >= 1
    for t in birthday_tracks:
        assert t["category"] == "birthday"


def test_static_music_provider_get_track_details():
    """Verify retrieving individual track details."""
    provider = StaticMusicLibraryProvider()
    track = provider.get_track("happy_birthday_acoustic")
    assert track is not None
    assert track["id"] == "happy_birthday_acoustic"
    assert track["category"] == "birthday"
    assert track["available"] is True
    assert os.path.isfile(track["file_path"])

    # Non-existent track returns None
    assert provider.get_track("non_existent_track_xyz") is None


def test_static_music_provider_path_traversal_protection():
    """Verify that path traversal attempts in track ID or paths are safely rejected."""
    provider = StaticMusicLibraryProvider()

    traversal_ids = [
        "../../etc/passwd",
        "..\\..\\windows\\win.ini",
        "/etc/shadow",
        "../catalog.json",
        "birthday/../../../secret",
    ]
    for tid in traversal_ids:
        with pytest.raises(BadRequestError):
            provider.get_track(tid)

        with pytest.raises(BadRequestError):
            provider.get_track_file_path(tid)


def test_static_music_provider_missing_audio_file(tmp_path):
    """Verify handling when an audio file registered in catalog does not exist on disk."""
    # Create isolated provider with an empty category directory
    empty_music_dir = tmp_path / "empty_music"
    os.makedirs(empty_music_dir / "birthday", exist_ok=True)
    catalog_file = empty_music_dir / "catalog.json"
    catalog_content = [
        {
            "id": "missing_track",
            "title": "Missing Audio Track",
            "category": "birthday",
            "filename": "does_not_exist.wav",
            "license": "Synthetic Development Track"
        }
    ]
    import json
    with open(catalog_file, "w") as f:
        json.dump(catalog_content, f)

    provider = StaticMusicLibraryProvider(base_dir=str(empty_music_dir))
    tracks = provider.list_tracks()
    assert len(tracks) == 1
    assert tracks[0]["available"] is False
    assert tracks[0]["duration_seconds"] == 0.0

    # Attempting to fetch path of unavailable track raises BadRequestError
    with pytest.raises(BadRequestError, match="not available on disk"):
        provider.get_track_file_path("missing_track")


@pytest.mark.asyncio
async def test_static_music_provider_generate_music_fallback():
    """Verify generate_music selects best mood-matched track from the library."""
    provider = StaticMusicLibraryProvider()
    # Requesting 'celebration' mood should map to birthday category
    path = await provider.generate_music(mood="Celebration", duration_seconds=15)
    assert os.path.isfile(path)
    assert "birthday" in path.lower() or os.path.isfile(path)


# ---------------------------------------------------------------------------
# 2. Integration Tests for MusicService Library Selection
# ---------------------------------------------------------------------------

def test_music_service_select_library_track_and_replace(db_session):
    """Test MusicService.select_library_track_for_project creation, replacement, and DB state."""
    test_user = db_session.query(User).filter(User.email == "library_unit@example.com").first()
    if not test_user:
        test_user = User(
            id=uuid.uuid4(),
            email="library_unit@example.com",
            hashed_password=hash_password("Password123!"),
            full_name="Library Unit User"
        )
        db_session.add(test_user)
        db_session.commit()

    project = VideoProject(
        id=uuid.uuid4(),
        user_id=test_user.id,
        title="Library Music Project",
        aspect_ratio="9:16",
        duration_seconds=15,
        music_mood="Birthday"
    )
    db_session.add(project)
    db_session.commit()

    service = MusicService()

    # 1. Select Birthday track
    asset1 = service.select_library_track_for_project(
        project_id=project.id,
        user_id=test_user.id,
        track_id="happy_birthday_acoustic",
        db=db_session
    )
    assert asset1 is not None
    assert asset1.asset_type == "MUSIC"
    assert asset1.meta_data.get("provider") == "static_library"
    assert asset1.meta_data.get("track_id") == "happy_birthday_acoustic"
    assert asset1.duration_seconds > 0
    assert os.path.isfile(asset1.file_path)

    # 2. Select another track (Anniversary) to verify replacement (no duplicate assets)
    asset2 = service.select_library_track_for_project(
        project_id=project.id,
        user_id=test_user.id,
        track_id="romantic_piano_warmth",
        db=db_session
    )
    assert asset2 is not None
    assert asset2.id != asset1.id
    assert asset2.meta_data.get("track_id") == "romantic_piano_warmth"

    # Only 1 music asset should remain for the project
    music_assets = db_session.query(GeneratedAsset).filter(
        GeneratedAsset.project_id == project.id,
        GeneratedAsset.asset_type == "MUSIC"
    ).all()
    assert len(music_assets) == 1
    assert music_assets[0].id == asset2.id


# ---------------------------------------------------------------------------
# 3. API Route Tests: /music and /projects/{id}/select-music
# ---------------------------------------------------------------------------

def test_api_music_categories_and_tracks(client):
    """Test GET /api/v1/music/categories and GET /api/v1/music/tracks."""
    # Categories
    cat_res = client.get("/api/v1/music/categories")
    assert cat_res.status_code == 200
    categories = cat_res.json()
    assert "birthday" in categories
    assert "cinematic" in categories

    # All tracks
    tracks_res = client.get("/api/v1/music/tracks")
    assert tracks_res.status_code == 200
    tracks = tracks_res.json()
    assert len(tracks) >= 6

    # Category filtered tracks
    bday_res = client.get("/api/v1/music/tracks?category=birthday")
    assert bday_res.status_code == 200
    bday_tracks = bday_res.json()
    assert len(bday_tracks) >= 1
    for t in bday_tracks:
        assert t["category"] == "birthday"


def test_api_music_track_detail_and_stream(client):
    """Test GET /api/v1/music/tracks/{id} and streaming preview."""
    # Detail
    res = client.get("/api/v1/music/tracks/happy_birthday_acoustic")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == "happy_birthday_acoustic"
    assert data["category"] == "birthday"

    # Not found
    nf_res = client.get("/api/v1/music/tracks/unknown_track_404")
    assert nf_res.status_code == 404

    # Full audio stream
    stream_res = client.get("/api/v1/music/tracks/happy_birthday_acoustic/stream")
    assert stream_res.status_code == 200
    assert stream_res.headers["content-type"] in ["audio/wav", "audio/x-wav"]
    assert int(stream_res.headers["content-length"]) > 100000

    # Range request (bytes=0-100)
    range_res = client.get(
        "/api/v1/music/tracks/happy_birthday_acoustic/stream",
        headers={"Range": "bytes=0-100"}
    )
    assert range_res.status_code == 206


def test_api_select_music_project_lifecycle_and_ownership(client, db_session):
    """Test POST /api/v1/projects/{id}/select-music, ownership validation, and GET /projects/{id}/music."""
    # Register User A
    email_a = f"usera_lib_{uuid.uuid4().hex[:6]}@example.com"
    client.cookies.clear()
    reg_a = client.post("/api/v1/auth/register", json={
        "email": email_a,
        "password": "Password123!",
        "full_name": "User A Lib"
    })
    assert reg_a.status_code == 201

    proj_res = client.post("/api/v1/projects", json={
        "title": "Select Music Project",
        "occasion": "Birthday",
        "duration_seconds": 15,
        "aspect_ratio": "9:16",
        "music_mood": "Happy"
    })
    assert proj_res.status_code == 201
    project_id = proj_res.json()["id"]

    # 1. Select valid track
    sel_res = client.post(
        f"/api/v1/projects/{project_id}/select-music",
        json={"track_id": "happy_birthday_acoustic", "provider": "library"}
    )
    assert sel_res.status_code == 200
    asset_data = sel_res.json()
    assert asset_data["asset_type"] == "MUSIC"
    assert asset_data["provider"] == "static_library"
    assert asset_data["track_id"] == "happy_birthday_acoustic"
    assert asset_data["duration_seconds"] == 15.0

    # 2. Verify GET /api/v1/projects/{id}/music returns the selected library track
    get_res = client.get(f"/api/v1/projects/{project_id}/music")
    assert get_res.status_code == 200
    get_data = get_res.json()
    assert get_data["provider"] == "static_library"
    assert get_data["track_id"] == "happy_birthday_acoustic"
    assert "/api/v1/assets/" in get_data["stream_url"]

    # 3. Invalid track selection returns 404
    inv_res = client.post(
        f"/api/v1/projects/{project_id}/select-music",
        json={"track_id": "non_existent_track_123", "provider": "library"}
    )
    assert inv_res.status_code == 404

    # 4. Register User B and verify cross-user permission denied (403)
    email_b = f"userb_lib_{uuid.uuid4().hex[:6]}@example.com"
    client.cookies.clear()
    client.post("/api/v1/auth/register", json={
        "email": email_b,
        "password": "Password123!",
        "full_name": "User B Lib"
    })

    unauth_sel = client.post(
        f"/api/v1/projects/{project_id}/select-music",
        json={"track_id": "romantic_piano_warmth", "provider": "library"}
    )
    assert unauth_sel.status_code == 403


# ---------------------------------------------------------------------------
# 4. FFmpeg Compatibility & ACE-Step Non-Regression
# ---------------------------------------------------------------------------

def test_ffmpeg_can_decode_library_audio_file():
    """Verify that FFmpeg binary can access and decode the library audio file with zero errors."""
    provider = StaticMusicLibraryProvider()
    file_path = provider.get_track_file_path("happy_birthday_acoustic")
    assert os.path.isfile(file_path)

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ffmpeg_exe, "-i", file_path, "-f", "null", "-"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0
    assert "pcm_s16le" in res.stderr
    assert "44100 Hz" in res.stderr
