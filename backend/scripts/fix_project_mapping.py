import sys
import os

sys.path.insert(0, os.path.abspath("backend"))

from app.core.database import SessionLocal
from app.models.project import VideoProject
from app.models.image import ProjectImage
from app.models.asset import GeneratedAsset
from sqlalchemy.orm.attributes import flag_modified

db = SessionLocal()
pid = "21b7a15f-0bc8-4a75-af1d-55d3638a17b9"
p = db.query(VideoProject).filter(VideoProject.id == pid).first()
images = db.query(ProjectImage).filter(ProjectImage.project_id == pid).order_by(ProjectImage.order_index.asc()).all()

print(f"Project found: {p.title}")
print("Images in order:")
for i, img in enumerate(images):
    print(f"  Photo #{i+1}: {img.id} ({img.original_filename})")

scenes = p.scene_plan.get("scenes", [])
for i, sc in enumerate(scenes):
    if i < len(images):
        sc["reference_image_ids"] = [str(images[i].id)]
    sc["generation_type"] = "wan_video"
    print(f"  Scene {sc.get('scene_number')}: Image={sc.get('reference_image_ids')}, type={sc.get('generation_type')}")

p.scene_plan["scenes"] = scenes
flag_modified(p, "scene_plan")

# Remove any old clips with repeated photos
clips = db.query(GeneratedAsset).filter(GeneratedAsset.project_id == pid, GeneratedAsset.asset_type == "CLIP").all()
print(f"Clearing {len(clips)} old clips so all 5 fresh clips can be generated with distinct images...")
for c in clips:
    db.delete(c)

db.commit()
print("Project 21b7a15f successfully updated and ready for generation!")
db.close()
