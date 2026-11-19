import os
import sys
import uuid
import json
import time
import asyncio
import subprocess
from unittest.mock import AsyncMock, patch

# Ensure backend package resolution
sys.path.insert(0, os.path.abspath("backend"))

import imageio_ffmpeg
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.user import User
from app.models.project import VideoProject
from app.models.image import ProjectImage
from app.models.asset import GeneratedAsset
from app.models.job import GenerationJob
from app.api.deps import get_current_user
from app.core.exceptions import ServiceUnavailableError
from app.ai.ffmpeg_slideshow import FFmpegSlideshowProvider
from app.services.video_generator import VideoGenerationService, get_video_generation_service
from app.services.composer import VideoCompositionService, get_composition_service
from app.composer.executor import execute_composition


def probe_media_file(file_path: str):
    """Probes video file using ffmpeg -i to inspect streams, duration, resolution, fps."""
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [exe, "-i", file_path]
    res = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    output = res.stderr

    duration = None
    width = None
    height = None
    fps = None
    vcodec = None
    acodec = None

    for line in output.splitlines():
        line = line.strip()
        if "Duration:" in line:
            parts = line.split("Duration:")[1].split(",")[0].strip()
            # hh:mm:ss.xx
            h, m, s = parts.split(":")
            duration = float(h) * 3600 + float(m) * 60 + float(s)
        if "Video:" in line:
            if "h264" in line.lower():
                vcodec = "h264"
            elif "avc" in line.lower():
                vcodec = "avc1"
            else:
                vcodec = "other"
            # find resolution like 720x1280
            import re
            m_res = re.search(r"(\d{3,4})x(\d{3,4})", line)
            if m_res:
                width = int(m_res.group(1))
                height = int(m_res.group(2))
            m_fps = re.search(r"(\d+(?:\.\d+)?) fps", line)
            if m_fps:
                fps = float(m_fps.group(1))
        if "Audio:" in line:
            if "aac" in line.lower():
                acodec = "aac"
            elif "mp3" in line.lower():
                acodec = "mp3"
            else:
                acodec = "audio"

    return {
        "duration": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "vcodec": vcodec,
        "acodec": acodec,
    }


def main():
    print("=" * 70)
    print("PHASE 9: FINAL LIVE INTEGRATION VERIFICATION")
    print("=" * 70)

    db = SessionLocal()
    results = {}

    try:
        # Load test user Shiva
        user = db.query(User).filter(User.email == "shiva@gmail.com").first()
        if not user:
            user = db.query(User).first()
        print(f"[SETUP] Using User: {user.email} ({user.id})")

        # Project 9489efce-4a6b-4ff0-a63e-9eec8b31f729
        project_id = uuid.UUID("9489efce-4a6b-4ff0-a63e-9eec8b31f729")
        project = db.query(VideoProject).filter(VideoProject.id == project_id).first()
        assert project is not None, "Project 9489efce-4a6b-4ff0-a63e-9eec8b31f729 not found"
        print(f"[SETUP] Using Project: '{project.title}' ({project.id})")
        print(f"        Occasion: {project.occasion}, Duration: {project.duration_seconds}s, Aspect: {project.aspect_ratio}")

        # Check existing images
        images = db.query(ProjectImage).filter(ProjectImage.project_id == project.id).order_by(ProjectImage.order_index).all()
        print(f"[SETUP] Found {len(images)} reference images:")
        for img in images:
            img_real_path = os.path.join(settings.UPLOAD_DIR, img.file_path) if not os.path.isabs(img.file_path) else img.file_path
            print(f"        - Img #{img.order_index}: {img.file_path} (exists on disk: {os.path.exists(img_real_path)})")
            assert os.path.exists(img_real_path), f"Image file missing: {img_real_path}"

        # Override dependency for TestClient
        app.dependency_overrides[get_current_user] = lambda: user
        client = TestClient(app)

        # -------------------------------------------------------------------
        # TEST 1: Manual Slideshow Mode
        # -------------------------------------------------------------------
        print("\n" + "=" * 70)
        print("TEST 1: Manual Slideshow Mode (force_slideshow=true)")
        print("=" * 70)

        # Trigger POST /api/v1/projects/{project_id}/generate-clips?force_slideshow=true
        t0 = time.time()
        resp = client.post(f"/api/v1/projects/{project.id}/generate-clips?force_slideshow=true")
        print(f"[TEST 1] HTTP POST /generate-clips?force_slideshow=true status: {resp.status_code}")
        assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
        job_data = resp.json()
        print(f"[TEST 1] Returned Job ID: {job_data.get('id')}, Stage: {job_data.get('stage')}, Status: {job_data.get('status')}")

        # In TestClient, BackgroundTasks run synchronously before post returns!
        # Refresh database session and verify GenerationJob
        db.expire_all()
        job = db.query(GenerationJob).filter(GenerationJob.id == uuid.UUID(job_data["id"])).first()
        print(f"[TEST 1] Completed Job Status: {job.status}, Progress: {job.progress_percent}%")
        assert job.status == "COMPLETED", f"Expected COMPLETED, got {job.status} (error: {job.error_message})"
        assert job.progress_percent == 100

        # Query GeneratedAsset for clips
        clips = db.query(GeneratedAsset).filter(
            GeneratedAsset.project_id == project.id,
            GeneratedAsset.asset_type == "CLIP"
        ).all()
        print(f"[TEST 1] Total CLIP assets in DB: {len(clips)}")
        assert len(clips) == 3, f"Expected 3 clips, got {len(clips)}"

        scene_verifications = []
        for idx, sc_clip in enumerate(sorted(clips, key=lambda c: (c.meta_data or {}).get("scene_number", 0))):
            sc_meta = sc_clip.meta_data or {}
            sc_num = sc_meta.get("scene_number", idx + 1)
            clip_real_path = os.path.abspath(sc_clip.file_path)
            assert os.path.isfile(clip_real_path), f"Clip file missing on disk: {clip_real_path}"
            assert os.path.getsize(clip_real_path) > 0, "Clip file is 0 bytes"
            assert sc_meta.get("provider") == "ffmpeg_slideshow", f"Expected provider ffmpeg_slideshow, got {sc_meta.get('provider')}"
            assert sc_meta.get("fallback_used") is True
            assert bool(sc_meta.get("camera_movement")), "Camera movement metadata missing"
            resolved_movement = (sc_meta.get("timing_metrics") or {}).get("movement") or sc_meta.get("camera_movement")

            # Probe with FFmpeg
            probe = probe_media_file(clip_real_path)
            print(f"  ✓ Scene {sc_num}:")
            print(f"      Path: {sc_clip.file_path}")
            print(f"      Provider: {sc_meta.get('provider')}, Fallback Used: {sc_meta.get('fallback_used')}")
            print(f"      Camera Motion: {sc_meta.get('camera_movement')}")
            print(f"      Resolution: {probe['width']}x{probe['height']} (Aspect: 9:16)")
            print(f"      Duration: {probe['duration']}s (Expected ~5.0s)")
            print(f"      Framerate: {probe['fps']} FPS, Codec: {probe['vcodec']}")
            print(f"      Filesize: {os.path.getsize(clip_real_path) / 1024:.1f} KB")

            assert probe["width"] == 720 and probe["height"] == 1280, f"Incorrect dimensions: {probe['width']}x{probe['height']}"
            assert probe["fps"] == 30.0, f"Incorrect framerate: {probe['fps']}"
            assert abs(probe["duration"] - 5.0) <= 0.1, f"Incorrect duration: {probe['duration']}"

            scene_verifications.append({
                "scene_number": sc_num,
                "provider": sc_meta.get("provider"),
                "movement": sc_meta.get("camera_movement"),
                "file": os.path.basename(clip_real_path),
                "resolution": f"{probe['width']}x{probe['height']}",
                "fps": probe["fps"],
                "duration": probe["duration"],
                "size_kb": round(os.path.getsize(clip_real_path) / 1024, 1),
            })

        print(f"[TEST 1] Elapsed time: {time.time() - t0:.2f}s")
        results["test1"] = {
            "status": "PASSED",
            "job_id": str(job.id),
            "job_status": job.status,
            "scenes": scene_verifications,
        }

        # -------------------------------------------------------------------
        # TEST 2: Automatic Wan Failure Fallback (Simulated 503)
        # -------------------------------------------------------------------
        print("\n" + "=" * 70)
        print("TEST 2: Automatic Wan Failure Fallback (Simulated HTTP 503)")
        print("=" * 70)

        # Create a mock Wan provider that raises ServiceUnavailableError
        mock_wan_failing = AsyncMock()
        mock_wan_failing.generate_scene_clip.side_effect = ServiceUnavailableError(
            "HuggingFace ZeroGPU quota exhausted / 503 Service Unavailable"
        )
        mock_wan_failing.last_run_metrics = {}

        slideshow_prov = FFmpegSlideshowProvider()
        test2_service = VideoGenerationService(
            video_provider=mock_wan_failing,
            slideshow_provider=slideshow_prov
        )

        # Force regeneration of scene 1 with force_slideshow=False
        test2_job = asyncio.run(
            test2_service.generate_project_clips(
                project_id=project.id,
                user_id=user.id,
                db_session_factory=SessionLocal,
                force_retry_scene=1,
                force_slideshow=False,
            )
        )

        db.expire_all()
        assert test2_job.status == "COMPLETED"
        assert mock_wan_failing.generate_scene_clip.call_count >= 1, "Wan was not attempted first"

        # Check asset 1
        clip1 = db.query(GeneratedAsset).filter(
            GeneratedAsset.project_id == project.id,
            GeneratedAsset.asset_type == "CLIP"
        ).all()
        # Find scene 1 clip
        sc1_asset = [c for c in clip1 if (c.meta_data or {}).get("scene_number") == 1][0]
        sc1_meta = sc1_asset.meta_data or {}
        print(f"[TEST 2] Wan attempted: Yes ({mock_wan_failing.generate_scene_clip.call_count} calls)")
        print(f"[TEST 2] Recorded original Wan error: {sc1_meta.get('original_wan_error')}")
        print(f"[TEST 2] Fallback provider engaged: {sc1_meta.get('provider')}")
        print(f"[TEST 2] Fallback used flag: {sc1_meta.get('fallback_used')}")
        print(f"[TEST 2] Clip exists: {os.path.exists(sc1_asset.file_path)}")
        print(f"[TEST 2] Total clips in DB: {len(clip1)} (No duplicates: 3 scenes = 3 clips)")

        assert "503" in sc1_meta.get("original_wan_error", "")
        assert sc1_meta.get("provider") == "ffmpeg_slideshow"
        assert sc1_meta.get("fallback_used") is True
        assert len(clip1) == 3, f"Duplicate assets created! Found {len(clip1)} clips instead of 3"
        assert test2_job.status == "COMPLETED"

        results["test2"] = {
            "status": "PASSED",
            "wan_attempted_first": True,
            "original_error_recorded": sc1_meta.get("original_wan_error"),
            "fallback_provider": sc1_meta.get("provider"),
            "fallback_used": sc1_meta.get("fallback_used"),
            "total_assets_after_retry": len(clip1),
            "no_duplicates": True,
            "job_status": test2_job.status,
        }

        # -------------------------------------------------------------------
        # TEST 3: Successful Wan Generation (Bypasses Fallback)
        # -------------------------------------------------------------------
        print("\n" + "=" * 70)
        print("TEST 3: Successful Wan Generation (Bypasses Fallback Provider)")
        print("=" * 70)

        # Create a mock Wan that succeeds and a mock slideshow provider
        mock_wan_success = AsyncMock()
        dummy_clip_path = os.path.abspath("storage/outputs/clips/dummy_wan_clip.mp4")
        os.makedirs(os.path.dirname(dummy_clip_path), exist_ok=True)
        # Touch or create a small valid MP4
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        subprocess.run([exe, "-y", "-f", "lavfi", "-i", "testsrc=size=720x1280:rate=30", "-t", "5", "-c:v", "libx264", dummy_clip_path], check=True, capture_output=True)

        mock_wan_success.generate_scene_clip.return_value = dummy_clip_path
        mock_wan_success.last_run_metrics = {}

        mock_slideshow = AsyncMock()

        test3_service = VideoGenerationService(
            video_provider=mock_wan_success,
            slideshow_provider=mock_slideshow
        )

        test3_job = asyncio.run(
            test3_service.generate_project_clips(
                project_id=project.id,
                user_id=user.id,
                db_session_factory=SessionLocal,
                force_retry_scene=1,
                force_slideshow=False,
            )
        )

        print(f"[TEST 3] Wan Provider called: {mock_wan_success.generate_scene_clip.call_count} times")
        print(f"[TEST 3] Slideshow Provider called: {mock_slideshow.generate_scene_clip.call_count} times")
        assert mock_wan_success.generate_scene_clip.call_count == 1
        assert mock_slideshow.generate_scene_clip.call_count == 0, "Slideshow provider was invoked despite successful Wan!"

        db.expire_all()
        sc1_asset_wan = [c for c in db.query(GeneratedAsset).filter(GeneratedAsset.project_id == project.id, GeneratedAsset.asset_type == "CLIP").all() if (c.meta_data or {}).get("scene_number") == 1][0]
        assert sc1_asset_wan.meta_data.get("provider") == "wan_video"
        assert sc1_asset_wan.meta_data.get("fallback_used") is False
        print(f"[TEST 3] Asset Provider: {sc1_asset_wan.meta_data.get('provider')}, Fallback Used: {sc1_asset_wan.meta_data.get('fallback_used')}")

        # Regenerate scene 1 back to slideshow so Test 4 has 100% slideshow clips
        print("[TEST 3] Re-generating Scene 1 back to slideshow mode for Test 4...")
        real_slideshow = FFmpegSlideshowProvider()
        test3_restore_service = VideoGenerationService(slideshow_provider=real_slideshow)
        asyncio.run(
            test3_restore_service.generate_project_clips(
                project_id=project.id,
                user_id=user.id,
                db_session_factory=SessionLocal,
                force_retry_scene=1,
                force_slideshow=True,
            )
        )
        print("  ✓ Scene 1 restored to slideshow mode.")

        results["test3"] = {
            "status": "PASSED",
            "wan_calls": mock_wan_success.generate_scene_clip.call_count,
            "slideshow_calls": mock_slideshow.generate_scene_clip.call_count,
            "provider_saved": "wan_video",
            "fallback_bypassed": True,
        }

        # -------------------------------------------------------------------
        # TEST 4: Phase 8 Compatibility (Final Composition of Slideshow Clips)
        # -------------------------------------------------------------------
        print("\n" + "=" * 70)
        print("TEST 4: Phase 8 Compatibility (Final Composition with Voiceover & Music)")
        print("=" * 70)

        # Verify voiceover and music exist
        vo_asset = db.query(GeneratedAsset).filter(GeneratedAsset.project_id == project.id, GeneratedAsset.asset_type == "VOICEOVER").first()
        mus_asset = db.query(GeneratedAsset).filter(GeneratedAsset.project_id == project.id, GeneratedAsset.asset_type == "MUSIC").first()
        print(f"[TEST 4] Project Voiceover: {vo_asset.file_path} (exists: {os.path.exists(vo_asset.file_path)})")
        print(f"[TEST 4] Project Music: {mus_asset.file_path} (exists: {os.path.exists(mus_asset.file_path)})")
        assert vo_asset and os.path.exists(vo_asset.file_path)
        assert mus_asset and os.path.exists(mus_asset.file_path)

        # Trigger POST /api/v1/projects/{project_id}/compose?force=true
        t_compose = time.time()
        compose_resp = client.post(f"/api/v1/projects/{project.id}/compose?force=true")
        print(f"[TEST 4] HTTP POST /compose?force=true status: {compose_resp.status_code}")
        assert compose_resp.status_code == 202, f"Expected 202, got {compose_resp.status_code}: {compose_resp.text}"

        db.expire_all()
        final_job = db.query(GenerationJob).filter(
            GenerationJob.project_id == project.id,
            GenerationJob.stage == "COMPOSING"
        ).order_by(GenerationJob.created_at.desc()).first()

        print(f"[TEST 4] Composition Job Status: {final_job.status}, Progress: {final_job.progress_percent}%")
        assert final_job.status == "COMPLETED", f"Composition failed with error: {final_job.error_message}"

        # Fetch FINAL_VIDEO asset
        final_video_asset = db.query(GeneratedAsset).filter(
            GeneratedAsset.project_id == project.id,
            GeneratedAsset.asset_type == "FINAL_VIDEO"
        ).order_by(GeneratedAsset.created_at.desc()).first()

        assert final_video_asset is not None
        final_path = os.path.abspath(final_video_asset.file_path)
        assert os.path.isfile(final_path), f"Final video file missing: {final_path}"
        assert os.path.getsize(final_path) > 0

        # Probe final MP4
        final_probe = probe_media_file(final_path)
        print(f"[TEST 4] Generated Final Video MP4 Specifications:")
        print(f"      File: {final_video_asset.file_path}")
        print(f"      Dimensions: {final_probe['width']}x{final_probe['height']} (Aspect: 9:16)")
        print(f"      Duration: {final_probe['duration']}s (Target: ~15.0s)")
        print(f"      Framerate: {final_probe['fps']} FPS")
        print(f"      Video Codec: {final_probe['vcodec']}")
        print(f"      Audio Codec: {final_probe['acodec']}")
        print(f"      Filesize: {os.path.getsize(final_path) / (1024 * 1024):.2f} MB")
        print(f"      Composition Time: {time.time() - t_compose:.2f}s")

        assert final_probe["width"] == 720 and final_probe["height"] == 1280
        assert final_probe["fps"] == 30.0
        assert abs(final_probe["duration"] - 15.0) <= 1.0
        assert final_probe["vcodec"] in ["h264", "avc1"]
        assert final_probe["acodec"] == "aac"

        results["test4"] = {
            "status": "PASSED",
            "final_video_file": os.path.basename(final_path),
            "resolution": f"{final_probe['width']}x{final_probe['height']}",
            "duration": final_probe["duration"],
            "fps": final_probe["fps"],
            "vcodec": final_probe["vcodec"],
            "acodec": final_probe["acodec"],
            "filesize_mb": round(os.path.getsize(final_path) / (1024 * 1024), 2),
            "job_status": final_job.status,
        }

        # -------------------------------------------------------------------
        # TEST 5: Frontend Verification
        # -------------------------------------------------------------------
        print("\n" + "=" * 70)
        print("TEST 5: Frontend Integration Verification")
        print("=" * 70)

        page_tsx_path = os.path.abspath("frontend/src/app/projects/[id]/page.tsx")
        api_ts_path = os.path.abspath("frontend/src/lib/api.ts")

        with open(api_ts_path, "r", encoding="utf-8") as f:
            api_content = f.read()

        with open(page_tsx_path, "r", encoding="utf-8") as f:
            page_content = f.read()

        # Check API client method signature
        assert "forceSlideshow?: boolean" in api_content, "api.ts missing forceSlideshow parameter"
        assert "provider?: string" in api_content, "ClipAsset missing provider"
        assert "fallback_used?: boolean" in api_content, "ClipAsset missing fallback_used"
        print("  ✓ api.ts includes forceSlideshow query parameter and fallback metadata types")

        # Check UI components
        assert "Wan 2.2 + Auto Fallback" in page_content, "Missing 'Wan 2.2 + Auto Fallback' action button"
        assert "Slideshow Mode (Instant)" in page_content, "Missing 'Slideshow Mode (Instant)' action button"
        assert "Animated Photo Slideshow (Fallback)" in page_content or "Animated Slideshow (Fallback)" in page_content
        assert "Wan 2.2 AI Generated" in page_content
        assert "Animated Photo Slideshow Fallback Active" in page_content
        assert "handleStartClipGeneration(undefined, true)" in page_content
        assert "handleStartClipGeneration(undefined, false)" in page_content
        assert "handleStartClipGeneration(scene.scene_number, true)" in page_content
        assert "handleStartClipGeneration(scene.scene_number, false)" in page_content
        print("  ✓ Action Buttons: 'Wan 2.2 + Auto Fallback' and '⚡ Slideshow Mode (Instant)' present")
        print("  ✓ Provider Badges: Emerald '✓ Wan 2.2 AI Generated' and Amber '🖼️ Animated Slideshow (Fallback)' present")
        print("  ✓ Informative Fallback Notice Box present with dynamic Ken Burns movement detail")
        print("  ✓ Per-Scene Individual Controls: '🔄 Wan 2.2' and '⚡ Slideshow' present")

        results["test5"] = {
            "status": "PASSED",
            "api_contract_verified": True,
            "dual_action_buttons": True,
            "provider_badges": True,
            "fallback_notice_box": True,
            "per_scene_controls": True,
        }

        print("\n" + "=" * 70)
        print("ALL 5 TESTS PASSED SUCCESSFULLY!")
        print("=" * 70)
        print(json.dumps(results, indent=2))

    finally:
        db.close()


if __name__ == "__main__":
    main()
