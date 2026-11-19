import os
import sys

sys.path.insert(0, os.path.abspath("backend"))

from app.core.database import SessionLocal
from app.models.project import VideoProject
from app.models.image import ProjectImage
from app.models.asset import GeneratedAsset
from app.models.job import GenerationJob

db = SessionLocal()
try:
    projects = db.query(VideoProject).order_by(VideoProject.created_at.desc()).all()
    print(f"Total projects: {len(projects)}")
    count = 0
    for p in projects:
        images = db.query(ProjectImage).filter(ProjectImage.project_id == p.id).order_by(ProjectImage.order_index).all()
        if len(images) >= 3:
            count += 1
            assets = db.query(GeneratedAsset).filter(GeneratedAsset.project_id == p.id).all()
            jobs = db.query(GenerationJob).filter(GenerationJob.project_id == p.id).all()
            print(f"\n--- Project {p.id} ---")
            print(f"  Title: {p.title}")
            print(f"  Status: {p.status}")
            print(f"  Duration: {p.duration_seconds}s, Aspect: {p.aspect_ratio}")
            print(f"  Scene plan: {bool(p.scene_plan)}")
            print(f"  Images ({len(images)}):")
            for img in images:
                print(f"    Img {img.order_index}: {img.file_path} (exists: {os.path.exists(img.file_path)})")
            print(f"  Assets ({len(assets)}):")
            for a in assets:
                a_type = a.asset_type.value if hasattr(a.asset_type, 'value') else a.asset_type
                p_exists = os.path.exists(a.file_path) if hasattr(a, 'file_path') else (os.path.exists(a.storage_path) if hasattr(a, 'storage_path') else False)
                print(f"    Asset {a_type}: {getattr(a, 'file_path', getattr(a, 'storage_path', ''))} (exists: {p_exists})")
            print(f"  Jobs ({len(jobs)}):")
            for j in jobs:
                j_type = j.job_type.value if hasattr(j.job_type, 'value') else j.job_type
                j_stat = j.status.value if hasattr(j.status, 'value') else j.status
                print(f"    Job {j_type}: {j_stat}")
            if count >= 5:
                break
finally:
    db.close()
