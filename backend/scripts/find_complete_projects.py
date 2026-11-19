import sys, os
sys.path.insert(0, os.path.abspath("backend"))
from app.core.database import SessionLocal
from app.models.project import VideoProject
from app.models.image import ProjectImage
from app.models.asset import GeneratedAsset

db = SessionLocal()
projects = db.query(VideoProject).all()
print(f"Total projects: {len(projects)}")
for p in projects:
    imgs = db.query(ProjectImage).filter(ProjectImage.project_id == p.id).count()
    if imgs >= 3:
        vo = db.query(GeneratedAsset).filter(GeneratedAsset.project_id == p.id, GeneratedAsset.asset_type == "VOICEOVER").count()
        mus = db.query(GeneratedAsset).filter(GeneratedAsset.project_id == p.id, GeneratedAsset.asset_type == "MUSIC").count()
        if vo > 0 or mus > 0:
            clips = db.query(GeneratedAsset).filter(GeneratedAsset.project_id == p.id, GeneratedAsset.asset_type == "CLIP").count()
            print(f"Project: {p.id} | Title: {p.title} | Status: {p.status} | Imgs: {imgs} | VO: {vo} | Music: {mus} | Clips: {clips}")
db.close()
