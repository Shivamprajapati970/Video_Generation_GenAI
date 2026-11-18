import os
import sys
import io
import uuid
import json
import logging
from PIL import Image, ImageDraw, ImageFont

# Set environment
os.environ["ENVIRONMENT"] = "development"
os.environ["TESTING"] = "false"

from dotenv import load_dotenv
load_dotenv("backend/.env", override=True)

import app.core.config
app.core.config.settings.ENVIRONMENT = "development"

from fastapi.testclient import TestClient
from app.main import app
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.project import VideoProject
from app.ai.groq_vision import GroqVisionProvider

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("live_audit")


def create_artistic_image(label: str, bg_color: tuple, accent_color: tuple) -> bytes:
    """Creates a distinct, visual reference image with labeled graphics for the vision model."""
    img = Image.new("RGB", (320, 240), color=bg_color)
    draw = ImageDraw.Draw(img)
    # Draw geometric shapes as visual objects
    draw.rectangle([30, 40, 150, 180], fill=accent_color, outline=(255, 255, 255), width=2)
    draw.ellipse([180, 50, 290, 160], fill=(240, 220, 90), outline=(255, 255, 255), width=2)
    draw.polygon([(160, 20), (200, 70), (120, 70)], fill=(255, 100, 100))
    # Text annotation
    draw.text((40, 200), label, fill=(255, 255, 255))
    
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def run_live_audit():
    print("=" * 70)
    print("PHASE 4 GENUINE LIVE GROQ API INTEGRATION AUDIT")
    print("=" * 70)

    # 1. Environment & Config Audit
    groq_key = os.getenv("GROQ_API_KEY", "")
    hf_token = os.getenv("HF_TOKEN", "")
    
    print("\n--- 1. Environment Configuration ---")
    print(f"Backend environment: {settings.ENVIRONMENT}")
    print(f"Active Groq Vision Model: {settings.GROQ_VISION_MODEL}")
    print(f"GROQ_FALLBACK_MODEL: {settings.GROQ_FALLBACK_MODEL} (decommissioned models removed)")
    print(f"GROQ_API_KEY configured: {bool(groq_key and len(groq_key) > 20)} (Length: {len(groq_key)}, Prefix: {groq_key[:4]}...)")
    print(f"HF_TOKEN configured: {bool(hf_token and len(hf_token) > 10)} (Length: {len(hf_token)}, Prefix: {hf_token[:3]}...)")

    assert groq_key and groq_key.startswith("gsk_"), "Valid Groq API key is required"
    assert settings.GROQ_VISION_MODEL == "qwen/qwen3.8-27b", "Model must be qwen/qwen3.8-27b"

    # Configure GroqVisionProvider in strict live mode (mock_mode=False)
    GroqVisionProvider.mock_mode = False

    # 2. Test Client & Authentication
    client = TestClient(app)
    user_email = f"live_audit_{uuid.uuid4().hex[:6]}@example.com"
    user_pwd = "LivePassword123!"
    
    print("\n--- 2. User Registration & Session Auth ---")
    r_reg = client.post("/api/v1/auth/register", json={
        "email": user_email,
        "password": user_pwd,
        "full_name": "Live Audit Engineer"
    })
    assert r_reg.status_code == 201, f"Registration failed: {r_reg.text}"
    print(f"Registered test user: {user_email}")

    r_login = client.post("/api/v1/auth/login", json={
        "email": user_email,
        "password": user_pwd
    })
    assert r_login.status_code == 200, f"Login failed: {r_login.text}"
    print("Session authentication cookie acquired.")

    # 3. Create Project
    print("\n--- 3. Project Creation ---")
    duration = 20  # 20 seconds = 4 scenes of 5 seconds each
    project_payload = {
        "title": "Milestone Celebration & Adventure Odyssey",
        "occasion": "Celebration",
        "duration_seconds": duration,
        "aspect_ratio": "16:9",
        "music_mood": "Cinematic",
        "custom_music_prompt": "Uplifting orchestral soundtrack with soaring violins and warm acoustic piano",
        "description": "A vibrant celebration of life milestones: birthday cake cutting, romantic sunset toast, scenic mountain summit, sandy coastal escape, and joyous family reunion."
    }
    r_proj = client.post("/api/v1/projects", json=project_payload)
    assert r_proj.status_code == 201, f"Project creation failed: {r_proj.text}"
    proj = r_proj.json()
    proj_id = proj["id"]
    print(f"Created project ID: {proj_id}")
    print(f"Title: '{proj['title']}' | Occasion: {proj['occasion']} | Duration: {proj['duration_seconds']}s")

    # 4. Upload 5 Distinct Realistic Reference Photos
    print("\n--- 4. Reference Photos Preparation & Upload (5 Photos) ---")
    fixtures_dir = os.path.join(os.path.dirname(__file__), "tests", "fixtures", "realistic_images")
    realistic_files = [
        "photo1_birthday.jpg",
        "photo2_anniversary.jpg",
        "photo3_mountains.jpg",
        "photo4_beach_sunset.jpg",
        "photo5_family_feast.jpg",
    ]

    upload_files = []
    for fname in realistic_files:
        fpath = os.path.join(fixtures_dir, fname)
        if not os.path.exists(fpath):
            raise FileNotFoundError(f"Realistic test image not found at {fpath}. Please run download_realistic_samples.py first.")
        with open(fpath, "rb") as f:
            img_bytes = f.read()
        upload_files.append(("files", (fname, img_bytes, "image/jpeg")))

    r_upload = client.post(f"/api/v1/projects/{proj_id}/images", files=upload_files)
    assert r_upload.status_code == 201, f"Upload failed: {r_upload.text}"
    uploaded_images = r_upload.json()
    assert len(uploaded_images) == 5, f"Expected 5 uploaded images, got {len(uploaded_images)}"
    
    uploaded_map = {img["id"]: img["original_filename"] for img in uploaded_images}
    print(f"Successfully uploaded {len(uploaded_images)} realistic photographs into storage and PostgreSQL:")
    for idx, img in enumerate(uploaded_images):
        print(f"  [{idx + 1}] ID: {img['id']} | Filename: {img['original_filename']} | Size: {img['file_size']} bytes")

    # 5. Live Groq Multimodal Execution (Batching with GROQ_VISION_BATCH_SIZE)
    print("\n--- 5. Executing Live Groq Multimodal Analysis & Scene Planning ---")
    print("Calling POST /api/v1/projects/{proj_id}/analyze (mock_mode=False)...")
    print(f"Batching configuration: GROQ_VISION_BATCH_SIZE={settings.GROQ_VISION_BATCH_SIZE} (each batch sent to Groq vision, followed by ScenePlan synthesis).")

    r_analyze = None
    for retry_idx in range(3):
        r_analyze = client.post(f"/api/v1/projects/{proj_id}/analyze")
        if r_analyze.status_code == 200:
            break
        if r_analyze.status_code in (429, 503) and retry_idx < 2:
            print(f"\n[RETRY] Status {r_analyze.status_code} received on Free Tier. Waiting 35s to replenish token bucket (attempt {retry_idx + 1}/3)...")
            import time
            time.sleep(35)
            continue
        break

    if r_analyze is None or r_analyze.status_code != 200:
        print(f"\n[ERROR] Analysis failed with status {getattr(r_analyze, 'status_code', 'unknown')}:")
        print(getattr(r_analyze, "text", ""))
        sys.exit(1)

    plan = r_analyze.json()
    print("\n[SUCCESS] Real Groq API returned a valid, complete ScenePlan!")

    # 6. Detailed Inspection of the Generated Plan
    print("\n--- 6. Scene Plan Quality & Structure Verification ---")
    print(f"Video Concept: '{plan.get('video_concept')}'")
    print(f"Occasion: {plan.get('occasion')}")
    print(f"Story Summary:\n  {plan.get('story_summary')}")
    print(f"Visual Style: {plan.get('visual_style')}")
    print(f"Emotional Tone: {plan.get('emotional_tone')}")
    print(f"Total Duration: {plan.get('total_duration')}s (Expected: {duration}s)")
    print(f"Aspect Ratio: {plan.get('aspect_ratio')}")
    print(f"Opening Narration: \"{plan.get('opening_narration')}\"")
    print(f"Closing Narration: \"{plan.get('closing_narration')}\"")
    print(f"Full Narration Script:\n  \"{plan.get('full_narration_script')}\"")

    scenes = plan.get("scenes", [])
    expected_scenes = duration // 5
    print(f"\nScenes count: {len(scenes)} (Expected: {expected_scenes})")
    assert len(scenes) == expected_scenes, f"Expected {expected_scenes} scenes, got {len(scenes)}"
    
    total_scene_sec = sum(s.get("duration_seconds", 0) for s in scenes)
    print(f"Total scene durations: {total_scene_sec}s (Expected: {duration}s)")
    assert total_scene_sec == duration, f"Scene durations sum ({total_scene_sec}s) != duration ({duration}s)"

    # Verify narrations are non-empty, unique, and complete sentences (no awkward comma splits)
    narrations = [s.get("narration_text", "") for s in scenes]
    unique_narrations = set(narrations)
    print(f"\nUnique narration count: {len(unique_narrations)} / {len(scenes)}")
    assert len(unique_narrations) == len(scenes), "Narrations should be unique for each scene!"

    for s in scenes:
        nt = s.get("narration_text", "").strip()
        assert not nt.endswith(","), f"Scene {s.get('scene_number')} narration ends with a comma (awkward split): '{nt}'"
        assert len(nt.split()) >= 4, f"Narration too brief: '{nt}'"

    # Verify full_narration_script is strictly consistent with opening, scenes, and closing
    opening_narration = plan.get("opening_narration", "").strip()
    closing_narration = plan.get("closing_narration", "").strip()
    full_script = plan.get("full_narration_script", "").strip()
    expected_full = f"{opening_narration} {' '.join(n.strip() for n in narrations)} {closing_narration}".strip()
    print(f"\nVerifying Full Narration Script Consistency:")
    print(f"  Expected composite: \"{expected_full}\"")
    print(f"  Actual script:      \"{full_script}\"")
    assert full_script == expected_full, "full_narration_script must be exactly consistent with opening, scene narrations, and closing!"
    assert closing_narration != narrations[-1], "closing_narration must be distinct from the final scene voiceover line!"

    # Verify unselected images explanation if 5 images uploaded for 4 scenes (20s)
    unselected_expl = plan.get("unselected_images_explanation")
    print(f"\nUnselected Images Explanation:\n  {unselected_expl}")
    if len(uploaded_images) > len(scenes):
        assert unselected_expl is not None, "Expected explanation for unselected images when 5 images uploaded for 4 scenes"

    print("\nScene Breakdown:")
    for s in scenes:
        ref_ids = s.get("reference_image_ids", [])
        ref_names = [uploaded_map.get(rid, "UNKNOWN") for rid in ref_ids]
        print(f"  Scene #{s.get('scene_number')}: '{s.get('title')}' ({s.get('duration_seconds')}s, {s.get('transition')})")
        print(f"    - Ref Photo(s): {ref_names} (IDs: {ref_ids})")
        print(f"    - Camera Movement: {s.get('camera_movement')}")
        print(f"    - Wan 2.2 Prompt: {s.get('motion_prompt')}")
        print(f"    - Voiceover Line: \"{s.get('narration_text')}\"")
        assert s.get("duration_seconds") == 5, "Scene duration must be 5s"
        assert len(ref_ids) >= 1, "Must have reference images"
        for rid in ref_ids:
            assert rid in uploaded_map, f"Invalid reference image ID {rid}"

    # 7. Database Persistence Verification
    print("\n--- 7. PostgreSQL Database Persistence Verification ---")
    db = SessionLocal()
    try:
        db_proj = db.query(VideoProject).filter(VideoProject.id == uuid.UUID(proj_id)).first()
        assert db_proj is not None, "Project not found in PostgreSQL"
        assert db_proj.status == "PLANNED", f"Project status should be PLANNED, got {db_proj.status}"
        assert db_proj.scene_plan is not None, "scene_plan in PostgreSQL is None"
        assert isinstance(db_proj.scene_plan, dict), "scene_plan in PostgreSQL is not a dict"
        assert db_proj.scene_plan.get("video_concept") == plan.get("video_concept")
        print(f"Verified PostgreSQL database record: project {proj_id} status='PLANNED', scene_plan JSONB persisted ({len(db_proj.scene_plan['scenes'])} scenes).")
    finally:
        db.close()

    # 8. GET Endpoint Retrieval Verification
    print("\n--- 8. GET Scene Plan Endpoint Verification ---")
    r_get = client.get(f"/api/v1/projects/{proj_id}/scene-plan")
    assert r_get.status_code == 200, f"GET /scene-plan failed: {r_get.text}"
    get_plan = r_get.json()
    assert get_plan["video_concept"] == plan["video_concept"]
    assert len(get_plan["scenes"]) == len(plan["scenes"])
    print(f"Successfully retrieved verified scene plan from GET /api/v1/projects/{proj_id}/scene-plan")

    print("\n" + "=" * 70)
    print("ALL GENUINE LIVE GROQ API INTEGRATION CHECKS PASSED WITH ZERO MOCKS!")
    print("=" * 70)


if __name__ == "__main__":
    run_live_audit()
