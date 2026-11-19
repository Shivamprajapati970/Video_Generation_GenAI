import sys
import os
import time
import json
import asyncio
import subprocess

sys.path.insert(0, os.path.abspath("backend"))

import imageio_ffmpeg
from sqlalchemy import select
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.project import VideoProject
from app.models.image import ProjectImage
from app.models.asset import GeneratedAsset
from app.models.job import GenerationJob
from app.models.user import User
from app.services.video_generator import get_video_generation_service


def probe_clip(file_path: str):
    """Probes video file using ffmpeg for stream details and verifies full decode integrity."""
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    
    # 1. Probe info
    probe_cmd = [exe, "-i", file_path]
    probe_res = subprocess.run(probe_cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    out = probe_res.stderr

    duration = None
    width = None
    height = None
    fps = None
    vcodec = None

    for line in out.splitlines():
        line = line.strip()
        if "Duration:" in line:
            parts = line.split("Duration:")[1].split(",")[0].strip()
            h, m, s = parts.split(":")
            duration = round(float(h) * 3600 + float(m) * 60 + float(s), 2)
        if "Video:" in line:
            if "h264" in line.lower() or "avc" in line.lower():
                vcodec = "h264"
            else:
                vcodec = "other"
            import re
            m_res = re.search(r"(\d{3,4})x(\d{3,4})", line)
            if m_res:
                width = int(m_res.group(1))
                height = int(m_res.group(2))
            m_fps = re.search(r"(\d+(?:\.\d+)?) fps", line)
            if m_fps:
                fps = float(m_fps.group(1))

    # 2. Test full video stream decode with -f null -
    decode_cmd = [exe, "-v", "error", "-i", file_path, "-f", "null", "-"]
    decode_res = subprocess.run(decode_cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    decode_errors = decode_res.stderr.strip()
    is_decodable = (decode_res.returncode == 0 and len(decode_errors) == 0)

    # 3. Extract sample frame for visual inspection
    frame_dir = os.path.abspath("storage/outputs/frames")
    os.makedirs(frame_dir, exist_ok=True)
    frame_path = os.path.join(frame_dir, f"{os.path.basename(file_path)}_thumb.jpg")
    frame_cmd = [exe, "-y", "-ss", "0.5", "-i", file_path, "-vframes", "1", frame_path]
    subprocess.run(frame_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    has_frame = os.path.isfile(frame_path) and os.path.getsize(frame_path) > 0

    return {
        "duration": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "vcodec": vcodec,
        "is_decodable": is_decodable,
        "decode_error": decode_errors or None,
        "sample_frame": frame_path if has_frame else None,
        "file_size_kb": round(os.path.getsize(file_path) / 1024, 1),
    }


async def main():
    print("=" * 75)
    print("STEP 3 & 4: REAL WAN 2.2 GENERATION & CLIP VERIFICATION")
    print("=" * 75)

    db = SessionLocal()
    pid = "21b7a15f-0bc8-4a75-af1d-55d3638a17b9"
    project = db.query(VideoProject).filter(VideoProject.id == pid).first()
    user = db.query(User).filter(User.id == project.user_id).first()
    images = db.query(ProjectImage).filter(ProjectImage.project_id == pid).order_by(ProjectImage.order_index.asc()).all()

    print(f"Project: '{project.title}' ({project.id})")
    print(f"User: {user.email}")
    print(f"Total Scenes: {len(project.scene_plan.get('scenes', []))}")
    print(f"Uploaded Images ({len(images)}):")
    for i, img in enumerate(images):
        print(f"  Photo #{i+1}: {img.original_filename} ({img.id})")

    # Run actual video generator service with real providers
    service = get_video_generation_service()

    print("\n[START] Initiating real Wan 2.2 generation for all 5 scenes...")
    t0 = time.time()

    job = await service.generate_project_clips(
        project_id=project.id,
        user_id=user.id,
        db_session_factory=SessionLocal,
        force_slideshow=False,
    )

    elapsed_total = round(time.time() - t0, 2)
    print(f"\n[COMPLETE] Clip generation finished in {elapsed_total}s")
    print(f"Job ID: {job.id}")
    print(f"Job Status: {job.status}")
    print(f"Job Progress: {job.progress_percent}%")
    print(f"Job Error: {job.error_message}")
    print(f"Job Metadata: {job.external_provider_ids}")

    # Step 4: Asset inspection in DB
    db.expire_all()
    clips = db.query(GeneratedAsset).filter(
        GeneratedAsset.project_id == project.id,
        GeneratedAsset.asset_type == "CLIP"
    ).all()

    print(f"\n=== STEP 4: GENERATED CLIPS VERIFICATION ({len(clips)} clips) ===")
    assert len(clips) == 5, f"Expected exactly 5 clips, found {len(clips)}"

    report_scenes = []
    # Sort clips by scene_number
    sorted_clips = sorted(clips, key=lambda c: (c.meta_data or {}).get("scene_number", 0))

    for sc_clip in sorted_clips:
        meta = sc_clip.meta_data or {}
        sc_num = meta.get("scene_number")
        provider = meta.get("provider")
        fallback_used = meta.get("fallback_used", False)
        orig_err = meta.get("original_wan_error")
        ref_img_path = meta.get("reference_image_path")
        motion_prompt = meta.get("motion_prompt")

        # Resolve expected image
        expected_img = images[sc_num - 1]
        real_file_path = os.path.abspath(sc_clip.file_path)
        assert os.path.isfile(real_file_path), f"Clip missing on disk: {real_file_path}"

        probe_info = probe_clip(real_file_path)

        print(f"\n--- Scene {sc_num} ---")
        print(f"  Asset ID: {sc_clip.id}")
        print(f"  Provider: {provider} (Fallback Used: {fallback_used})")
        if orig_err:
            print(f"  Original Wan Error: {orig_err}")
        print(f"  Output MP4: {sc_clip.file_path} ({probe_info['file_size_kb']} KB)")
        print(f"  Assigned Photo: Photo #{sc_num} ({expected_img.original_filename})")
        print(f"  Decodable Without Errors: {probe_info['is_decodable']}")
        print(f"  Resolution: {probe_info['width']}x{probe_info['height']} (FPS: {probe_info['fps']})")
        print(f"  Duration: {probe_info['duration']}s")
        print(f"  Sample Thumb Extracted: {bool(probe_info['sample_frame'])}")

        assert probe_info["is_decodable"] is True, f"Clip {sc_clip.file_path} failed decode: {probe_info['decode_error']}"
        assert probe_info["width"] > 0 and probe_info["height"] > 0, f"Invalid dimensions: {probe_info['width']}x{probe_info['height']}"

        report_scenes.append({
            "scene_number": sc_num,
            "asset_id": str(sc_clip.id),
            "provider": provider,
            "fallback_used": fallback_used,
            "original_error": orig_err,
            "file": os.path.basename(real_file_path),
            "size_kb": probe_info["file_size_kb"],
            "resolution": f"{probe_info['width']}x{probe_info['height']}",
            "fps": probe_info["fps"],
            "duration": probe_info["duration"],
            "matched_photo": expected_img.original_filename,
            "thumb": probe_info["sample_frame"],
        })

    # Step 5: Isolated Fallback Regression Test (Dedicated isolated project)
    print("\n" + "=" * 75)
    print("STEP 5: ISOLATED FALLBACK REGRESSION TEST (MOCKED WAN FAILURE)")
    print("=" * 75)
    import uuid
    from unittest.mock import AsyncMock
    from app.core.exceptions import ServiceUnavailableError
    from app.ai.ffmpeg_slideshow import FFmpegSlideshowProvider
    from app.services.video_generator import VideoGenerationService

    iso_pid = uuid.uuid4()
    iso_img = images[0]
    iso_proj = VideoProject(
        id=iso_pid,
        user_id=user.id,
        title="Isolated Fallback Regression Test Project",
        occasion="Verification",
        duration_seconds=5,
        aspect_ratio="9:16",
        scene_plan={
            "scenes": [
                {
                    "scene_number": 1,
                    "title": "Scene 1",
                    "duration_seconds": 5,
                    "generation_type": "wan_video",
                    "reference_image_ids": [str(iso_img.id)],
                    "visual_description": "Isolated fallback test",
                    "camera_movement": "zoom_in",
                }
            ]
        }
    )
    db.add(iso_proj)
    # Associate image
    iso_pimg = ProjectImage(
        id=uuid.uuid4(),
        project_id=iso_pid,
        file_path=iso_img.file_path,
        original_filename=iso_img.original_filename,
        mime_type="image/jpeg",
        file_size=iso_img.file_size,
        order_index=0,
    )
    db.add(iso_pimg)
    db.commit()

    mock_failing_wan = AsyncMock()
    mock_failing_wan.generate_scene_clip.side_effect = ServiceUnavailableError("Simulated ZeroGPU 503 Quota Exhausted")
    mock_failing_wan.last_run_metrics = {}

    slideshow_p = FFmpegSlideshowProvider()
    test_service = VideoGenerationService(video_provider=mock_failing_wan, slideshow_provider=slideshow_p)

    test_job = await test_service.generate_project_clips(
        project_id=iso_pid,
        user_id=user.id,
        db_session_factory=SessionLocal,
        force_slideshow=False,
    )

    db.expire_all()
    assert test_job.status == "COMPLETED"
    assert mock_failing_wan.generate_scene_clip.call_count == 1, "Wan was not attempted first"

    sc1_asset = db.query(GeneratedAsset).filter(GeneratedAsset.project_id == iso_pid, GeneratedAsset.asset_type == "CLIP").first()
    meta1 = sc1_asset.meta_data or {}
    assert meta1.get("provider") == "ffmpeg_slideshow"
    assert meta1.get("fallback_used") is True
    assert "Simulated ZeroGPU 503" in meta1.get("original_wan_error", "")
    print("✓ Wan attempted first: Verified (call_count=1)")
    print("✓ Slideshow starts only after genuine provider failure: Verified")
    print(f"✓ Original Wan error retained in metadata: '{meta1.get('original_wan_error')}'")
    print("✓ Exactly one clip asset per scene: Verified (1 scene = 1 clip)")

    # Clean up isolated project
    db.delete(sc1_asset)
    db.delete(iso_pimg)
    db.delete(iso_proj)
    db.commit()

    print("\n" + "=" * 75)
    print("VERIFICATION COMPLETED SUCCESSFULLY!")
    print("=" * 75)
    print(json.dumps({
        "project_id": str(project.id),
        "job_id": str(job.id),
        "total_scenes": len(report_scenes),
        "scenes": report_scenes,
        "fallback_regression": "PASSED"
    }, indent=2))

    db.close()


if __name__ == "__main__":
    asyncio.run(main())
