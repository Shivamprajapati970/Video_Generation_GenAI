import os
import uuid
import logging
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable
from sqlalchemy.orm import Session as DBSession
from sqlalchemy import select, and_, desc

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.exceptions import NotFoundError, BadRequestError, ServiceUnavailableError
from app.models.project import VideoProject
from app.models.image import ProjectImage
from app.models.job import GenerationJob
from app.models.asset import GeneratedAsset
from app.ai.base import BaseVideoProvider
from app.ai.hf_wan_video import HFWanVideoProvider
from app.services.storage.base import BaseStorageService
from app.services.storage import get_storage_service

logger = logging.getLogger("video_gen_app.video_generator")


def _get_mp4_dimensions(file_path: str) -> Optional[str]:
    """Inspects MP4 container for video dimensions."""
    try:
        with open(file_path, "rb") as f:
            data = f.read(8192)
        pos = data.find(b"tkhd")
        if pos != -1:
            p = pos + 4
            version = data[p]
            p += 4
            if version == 0:
                p += 20  # skip times and IDs
            else:
                p += 32
            p += 52  # reserved + matrix
            w = int.from_bytes(data[p:p+2], "big")
            h = int.from_bytes(data[p+4:p+6], "big")
            if w > 0 and h > 0:
                return f"{w}x{h}"
    except Exception:
        pass
    return None


class VideoGenerationService:
    """Orchestrates multi-scene video generation using Wan 2.2 and records asset lifecycle."""

    def __init__(
        self,
        video_provider: Optional[BaseVideoProvider] = None,
        storage_service: Optional[BaseStorageService] = None,
    ):
        self.video_provider = video_provider or HFWanVideoProvider()
        self.storage_service = storage_service or get_storage_service()

    def get_existing_scene_assets(self, project_id: uuid.UUID, db: DBSession) -> Dict[int, GeneratedAsset]:
        """Returns map of {scene_number: GeneratedAsset} for completed clips that exist on disk."""
        stmt = (
            select(GeneratedAsset)
            .where(
                and_(
                    GeneratedAsset.project_id == project_id,
                    GeneratedAsset.asset_type == "CLIP"
                )
            )
            .order_by(GeneratedAsset.created_at.asc())
        )
        assets = db.execute(stmt).scalars().all()
        scene_assets: Dict[int, GeneratedAsset] = {}
        for a in assets:
            sc_num = (a.meta_data or {}).get("scene_number")
            if sc_num is not None:
                # Verify file still exists on disk
                clean_path = a.file_path.replace("\\", "/").lstrip("/")
                candidates = [
                    os.path.abspath(clean_path),
                    os.path.abspath(os.path.join("storage", clean_path)),
                    os.path.abspath(os.path.join(settings.OUTPUT_DIR, clean_path.replace("outputs/", ""))),
                    os.path.abspath(os.path.join(settings.OUTPUT_DIR, "clips", os.path.basename(clean_path))),
                ]
                try:
                    candidates.append(self.storage_service.get_full_path(clean_path))
                except Exception:
                    pass
                for c in candidates:
                    if os.path.isfile(c):
                        scene_assets[int(sc_num)] = a
                        break
        return scene_assets

    async def generate_project_clips(
        self,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        db_session_factory: Callable[[], DBSession] = SessionLocal,
        job_id: Optional[uuid.UUID] = None,
        force_retry_scene: Optional[int] = None,
    ) -> GenerationJob:
        """Asynchronously synthesizes video clips for all scenes in the project's ScenePlan."""
        db: DBSession = db_session_factory()
        job: Optional[GenerationJob] = None

        try:
            # 1. Fetch project
            project = db.get(VideoProject, project_id)
            if not project or str(project.user_id) != str(user_id):
                raise NotFoundError(detail="Project not found or access denied")

            scene_plan = project.scene_plan
            if not scene_plan or not isinstance(scene_plan, dict) or not scene_plan.get("scenes"):
                raise BadRequestError(detail="Project has no ScenePlan. Run scene planning first.")

            scenes = scene_plan.get("scenes", [])
            total_scenes = len(scenes)

            # 2. Setup or locate GenerationJob
            if job_id:
                job = db.get(GenerationJob, job_id)
            if not job:
                job = GenerationJob(
                    project_id=project.id,
                    stage="GENERATING_CLIPS",
                    status="RUNNING",
                    progress_percent=0,
                    started_at=datetime.utcnow(),
                    external_provider_ids={"total_scenes": total_scenes},
                )
                db.add(job)
            else:
                job.stage = "GENERATING_CLIPS"
                job.status = "RUNNING"
                job.started_at = job.started_at or datetime.utcnow()
                job.error_message = None

            db.commit()
            db.refresh(job)

            # 3. Check existing assets on disk to avoid redundant generation
            existing_assets = self.get_existing_scene_assets(project.id, db)
            images_map = {
                str(img.id): img
                for img in db.execute(
                    select(ProjectImage).where(ProjectImage.project_id == project.id)
                ).scalars().all()
            }

            completed_scenes = []

            # 4. Generate clips sequentially
            for scene_data in scenes:
                sc_num = scene_data.get("scene_number", 1)
                sc_title = scene_data.get("title", f"Scene {sc_num}")
                duration = scene_data.get("duration_seconds", 5)
                motion_prompt = scene_data.get("motion_prompt") or scene_data.get("visual_description", "")
                ref_img_ids = scene_data.get("reference_image_ids", [])

                # Check if this scene should be skipped
                if sc_num in existing_assets and force_retry_scene != sc_num:
                    logger.info(f"Scene {sc_num} already exists on disk as asset {existing_assets[sc_num].id}. Skipping.")
                    completed_scenes.append(sc_num)
                    continue

                logger.info(f"Starting generation for Scene {sc_num}/{total_scenes}: '{sc_title}'")

                # Resolve reference image path
                image_path = None
                for ref_id in ref_img_ids:
                    img_rec = images_map.get(str(ref_id))
                    if img_rec:
                        clean_img_path = img_rec.file_path.replace("\\", "/").lstrip("/")
                        img_candidates = [
                            os.path.abspath(clean_img_path),
                            os.path.abspath(os.path.join(settings.UPLOAD_DIR, clean_img_path)),
                            os.path.abspath(os.path.join("..", settings.UPLOAD_DIR, clean_img_path)),
                            os.path.abspath(os.path.join(settings.UPLOAD_DIR, os.path.basename(clean_img_path))),
                            os.path.abspath(os.path.join("..", settings.UPLOAD_DIR, os.path.basename(clean_img_path))),
                            os.path.abspath(os.path.join("storage", "uploads", clean_img_path)),
                            os.path.abspath(os.path.join("..", "storage", "uploads", clean_img_path)),
                        ]
                        try:
                            img_candidates.append(self.storage_service.get_full_path(clean_img_path))
                        except Exception:
                            pass
                        for cand in img_candidates:
                            if os.path.isfile(cand):
                                image_path = cand
                                break
                    if image_path:
                        break

                if not image_path:
                    # Fallback to first available image in project
                    for img_rec in images_map.values():
                        clean_img_path = img_rec.file_path.replace("\\", "/").lstrip("/")
                        fallback_candidates = [
                            os.path.abspath(clean_img_path),
                            os.path.abspath(os.path.join(settings.UPLOAD_DIR, clean_img_path)),
                            os.path.abspath(os.path.join("..", settings.UPLOAD_DIR, clean_img_path)),
                            os.path.abspath(os.path.join(settings.UPLOAD_DIR, os.path.basename(clean_img_path))),
                            os.path.abspath(os.path.join("..", settings.UPLOAD_DIR, os.path.basename(clean_img_path))),
                            os.path.abspath(os.path.join("storage", "uploads", clean_img_path)),
                            os.path.abspath(os.path.join("..", "storage", "uploads", clean_img_path)),
                        ]
                        try:
                            fallback_candidates.append(self.storage_service.get_full_path(clean_img_path))
                        except Exception:
                            pass
                        for cand in fallback_candidates:
                            if os.path.isfile(cand):
                                image_path = cand
                                break
                        if image_path:
                            break

                if not image_path:
                    raise BadRequestError(detail=f"No valid image file found on disk for Scene {sc_num}")

                # Update job progress
                current_percent = int(((len(completed_scenes)) / total_scenes) * 100)
                job.progress_percent = current_percent
                job.external_provider_ids = {
                    **(job.external_provider_ids or {}),
                    "current_scene": sc_num,
                    "completed_scenes": completed_scenes,
                }
                db.commit()

                # Synthesize clip with Wan 2.2
                clip_path = await self.video_provider.generate_scene_clip(
                    image_path=image_path,
                    prompt=motion_prompt,
                    aspect_ratio=project.aspect_ratio,
                    duration_seconds=duration,
                    scene_index=sc_num,
                )

                file_size = os.path.getsize(clip_path) if os.path.isfile(clip_path) else 0
                resolution = _get_mp4_dimensions(clip_path)

                # Persist or update GeneratedAsset record
                # Relative path for portable storage reference
                try:
                    rel_path = os.path.relpath(clip_path, start=".").replace("\\", "/")
                except ValueError:
                    rel_path = clip_path.replace("\\", "/")

                # Remove older asset for this scene if retrying
                if sc_num in existing_assets:
                    old_asset = existing_assets[sc_num]
                    try:
                        db.delete(old_asset)
                        db.commit()
                    except Exception:
                        db.rollback()

                asset = GeneratedAsset(
                    id=uuid.uuid4(),
                    project_id=project.id,
                    asset_type="CLIP",
                    file_path=rel_path,
                    duration_seconds=float(duration),
                    file_size=file_size,
                    meta_data={
                        "scene_number": sc_num,
                        "scene_title": sc_title,
                        "motion_prompt": motion_prompt,
                        "resolution": resolution,
                        "reference_image_path": image_path,
                        "camera_movement": scene_data.get("camera_movement", ""),
                        "scene_objective": scene_data.get("scene_objective", ""),
                        "narration_text": scene_data.get("narration_text", ""),
                        "timing_metrics": getattr(self.video_provider, "last_run_metrics", {}).get(sc_num, {}),
                    },
                )
                db.add(asset)
                db.commit()
                db.refresh(asset)

                completed_scenes.append(sc_num)
                existing_assets[sc_num] = asset
                logger.info(f"Successfully saved GeneratedAsset {asset.id} for Scene {sc_num} ({file_size} bytes, {resolution})")

                # Respectful ZeroGPU lease cooldown between consecutive scenes
                if sc_num < total_scenes and not self.video_provider.effective_mock_mode:
                    cooldown_s = 5.0
                    logger.info(f"ZeroGPU cooldown: waiting {cooldown_s}s before requesting scene {sc_num + 1}...")
                    await asyncio.sleep(cooldown_s)

            # 5. Mark job as COMPLETED
            job.status = "COMPLETED"
            job.progress_percent = 100
            job.completed_at = datetime.utcnow()
            job.external_provider_ids = {
                **(job.external_provider_ids or {}),
                "completed_scenes": completed_scenes,
                "current_scene": None,
            }
            db.commit()
            db.refresh(job)
            logger.info(f"Video clip generation job {job.id} completed successfully for project {project.id}")
            return job

        except Exception as exc:
            logger.error(f"Video clip generation failed for project {project_id}: {exc}", exc_info=True)
            if job:
                job.status = "FAILED"
                job.error_message = str(exc)
                job.completed_at = datetime.utcnow()
                try:
                    db.commit()
                    db.refresh(job)
                except Exception:
                    db.rollback()
            return job
        finally:
            db.close()


def get_video_generation_service() -> VideoGenerationService:
    """Dependency provider for VideoGenerationService."""
    return VideoGenerationService()

