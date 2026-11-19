import sys
import os

sys.path.insert(0, os.path.abspath("backend"))

from app.core.database import SessionLocal
from app.models.project import VideoProject
from app.models.image import ProjectImage
from app.models.asset import GeneratedAsset
from app.models.job import GenerationJob

db = SessionLocal()
pid = "21b7a15f-0bc8-4a75-af1d-55d3638a17b9"
p = db.query(VideoProject).filter(VideoProject.id == pid).first()
images = db.query(ProjectImage).filter(ProjectImage.project_id == pid).order_by(ProjectImage.order_index.asc()).all()

print("=== STEP 1: SCENE PLANNING VERIFICATION ===")
print(f"Project ID: {p.id}")
print(f"Title: {p.title}")
print(f"Duration: {p.duration_seconds}s, Aspect Ratio: {p.aspect_ratio}")
print(f"Images count in DB: {len(images)}")

for i, img in enumerate(images):
    print(f"  Image #{i+1} (order_index={img.order_index}): id={img.id}, filename={img.original_filename}, path={img.file_path}")

scenes = p.scene_plan.get("scenes", []) if p.scene_plan else []
print(f"\nTotal scenes in scene_plan: {len(scenes)}")

all_assigned_ids = []
for i, sc in enumerate(scenes):
    ref_ids = sc.get("reference_image_ids", [])
    all_assigned_ids.extend([str(r) for r in ref_ids])
    gen_type = sc.get("generation_type")
    matched_img = [img for img in images if str(img.id) in [str(r) for r in ref_ids]]
    matched_name = matched_img[0].original_filename if matched_img else "NO MATCH"
    matched_order = matched_img[0].order_index + 1 if matched_img else "N/A"
    print(f"  Scene {sc.get('scene_number')}:")
    print(f"    Title: {sc.get('title')}")
    print(f"    Duration: {sc.get('duration_seconds')}s")
    print(f"    Generation Type: {gen_type}")
    print(f"    Reference Image IDs: {ref_ids}")
    print(f"    Matched Image: Photo #{matched_order} ({matched_name})")
    print(f"    Camera Movement: {sc.get('camera_movement')}")
    print(f"    Motion Prompt: {sc.get('motion_prompt')}")

print("\n=== STEP 1 ASSERTIONS ===")
assert len(scenes) == 5, f"Expected 5 scenes, got {len(scenes)}"
assert len(images) == 5, f"Expected 5 images, got {len(images)}"
assert len(set(all_assigned_ids)) == 5, f"Expected 5 distinct image IDs, got {len(set(all_assigned_ids))}"

for i, sc in enumerate(scenes):
    expected_img = images[i]
    assert sc.get("reference_image_ids") == [str(expected_img.id)], f"Scene {i+1} does not map to Image {i+1}"
    assert sc.get("generation_type") == "wan_video", f"Scene {i+1} generation_type is not wan_video"

print("✓ All 5 scenes exist.")
print("✓ Each scene references a different uploaded image.")
print("✓ Scene 1 -> Image 1, Scene 2 -> Image 2, Scene 3 -> Image 3, Scene 4 -> Image 4, Scene 5 -> Image 5.")
print("✓ No image is duplicated or omitted.")
print("✓ Every scene has generation_type = 'wan_video'.")

db.close()
