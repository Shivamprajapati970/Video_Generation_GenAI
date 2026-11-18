import os
import time
import uuid
import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Callable
from sqlalchemy.orm import Session as DBSession
from sqlalchemy import select, and_, desc

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.exceptions import NotFoundError, BadRequestError, ServiceUnavailableError
from app.models.project import VideoProject
from app.models.job import GenerationJob
from app.models.asset import GeneratedAsset
from app.composer.ffmpeg_builder import build_composition_command
from app.composer.executor import execute_composition, validate_video_file
from app.services.storage.base import BaseStorageService
from app.services.storage import get_storage_service

logger = logging.getLogger("video_gen_app.composer")


class VideoCompositionService:
    """Orchestrates end-to-end multi-scene video and audio composition using FFmpeg."""

    def __init__(
        self,
        storage_service: Optional[BaseStorageService] = None,
        ffmpeg_exe: Optional[str] = None,
    ):
        self.storage_service = storage_service or get_storage_service()
        self.ffmpeg_exe = ffmpeg_exe
        self.output_dir = os.path.join(settings.OUTPUT_DIR, "videos")
        os.makedirs(self.output_dir, exist_ok=True)

    def _resolve_file_path(self, file_path: str) -> Optional[str]:
        """Resolves file path to an absolute path on disk across multiple candidate locations."""
        if not file_path:
            return None

        clean_path = file_path.replace("\\", "/").lstrip("/")
        candidates = [
            os.path.abspath(clean_path),
            os.path.abspath(os.path.join("storage", clean_path)),
            os.path.abspath(os.path.join(settings.OUTPUT_DIR, clean_path.replace("outputs/", ""))),
            os.path.abspath(os.path.join(settings.OUTPUT_DIR, "clips", os.path.basename(clean_path))),
            os.path.abspath(os.path.join(settings.OUTPUT_DIR, "audio", os.path.basename(clean_path))),
            os.path.abspath(os.path.join(settings.OUTPUT_DIR, "music", os.path.basename(clean_path))),
            os.path.abspath(os.path.join(settings.OUTPUT_DIR, "videos", os.path.basename(clean_path))),
            os.path.abspath(os.path.join("backend", clean_path)),
            os.path.abspath(os.path.join("media", "music", clean_path.replace("media/music/", ""))),
            os.path.abspath(os.path.join("backend", "media", "music", clean_path.replace("media/music/", ""))),
            # Also check relative to project root if running from backend/ subdirectory
            os.path.abspath(os.path.join("..", clean_path)),
            os.path.abspath(os.path.join("..", "storage", clean_path)),
            os.path.abspath(os.path.join("..", "storage", "outputs", "clips", os.path.basename(clean_path))),
            os.path.abspath(os.path.join("..", "storage", "outputs", "audio", os.path.basename(clean_path))),
            os.path.abspath(os.path.join("..", "storage", "outputs", "music", os.path.basename(clean_path))),
            os.path.abspath(os.path.join("..", "storage", "outputs", "videos", os.path.basename(clean_path))),
            os.path.abspath(os.path.join("..", "backend", clean_path)),
            os.path.abspath(os.path.join("..", "backend", "media", "music", clean_path.replace("media/music/", ""))),
        ]
        try:
            candidates.append(self.storage_service.get_full_path(clean_path))
        except Exception:
            pass

        for c in candidates:
            if os.path.isfile(c) and os.path.getsize(c) > 0:
                return c

        return None

    def get_existing_final_video_asset(self, project_id: uuid.UUID, db: DBSession) -> Optional[GeneratedAsset]:
        """Returns the latest valid GeneratedAsset for project final video that exists on disk."""
        stmt = (
            select(GeneratedAsset)
            .where(
                and_(
                    GeneratedAsset.project_id == project_id,
                    GeneratedAsset.asset_type == "FINAL_VIDEO"
                )
            )
            .order_by(GeneratedAsset.created_at.desc())
        )
        asset = db.execute(stmt).scalars().first()
        if not asset:
            return None

        resolved = self._resolve_file_path(asset.file_path)
        if resolved:
            return asset

        return None

    def _run_composition_sync(
        self,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        db_session_factory: Callable[[], DBSession] = SessionLocal,
        job_id: Optional[uuid.UUID] = None,
        force_regenerate: bool = False,
    ) -> GenerationJob:
        """Synchronous core implementation of video composition."""
        db: DBSession = db_session_factory()
        job: Optional[GenerationJob] = None

        try:
            # 1. Fetch project and verify ownership
            project = db.get(VideoProject, project_id)
            if not project or str(project.user_id) != str(user_id):
                raise NotFoundError(detail="Project not found or access denied")

            # 2. Check for existing final video asset unless force_regenerate is requested
            existing = self.get_existing_final_video_asset(project.id, db)
            if existing and not force_regenerate:
                logger.info(f"Project {project.id} already has a valid FINAL_VIDEO asset {existing.id}. Skipping.")
                if not job_id:
                    job = GenerationJob(
                        project_id=project.id,
                        stage="COMPOSING",
                        status="COMPLETED",
                        progress_percent=100,
                        started_at=datetime.now(timezone.utc),
                        completed_at=datetime.now(timezone.utc),
                        external_provider_ids={"reused_asset_id": str(existing.id)},
                    )
                    db.add(job)
                else:
                    job = db.get(GenerationJob, job_id)
                    if job:
                        job.stage = "COMPOSING"
                        job.status = "COMPLETED"
                        job.progress_percent = 100
                        job.completed_at = datetime.now(timezone.utc)
                db.commit()
                if job:
                    db.refresh(job)
                return job

            # 3. Initialize or update GenerationJob
            if job_id:
                job = db.get(GenerationJob, job_id)
            if not job:
                job = GenerationJob(
                    project_id=project.id,
                    stage="COMPOSING",
                    status="RUNNING",
                    progress_percent=10,
                    started_at=datetime.now(timezone.utc),
                )
                db.add(job)
            else:
                job.stage = "COMPOSING"
                job.status = "RUNNING"
                job.progress_percent = 10
                job.started_at = job.started_at or datetime.now(timezone.utc)
                job.error_message = None

            db.commit()
            db.refresh(job)

            # 4. Validate and resolve all required assets
            # A. Video Clips
            clip_stmt = (
                select(GeneratedAsset)
                .where(
                    and_(
                        GeneratedAsset.project_id == project.id,
                        GeneratedAsset.asset_type == "CLIP"
                    )
                )
                .order_by(GeneratedAsset.created_at.asc())
            )
            all_clips = db.execute(clip_stmt).scalars().all()

            scene_clips_map: Dict[int, GeneratedAsset] = {}
            scene_paths_map: Dict[int, str] = {}
            for c in all_clips:
                sc_num = (c.meta_data or {}).get("scene_number")
                if sc_num is not None:
                    resolved = self._resolve_file_path(c.file_path)
                    if resolved:
                        scene_clips_map[int(sc_num)] = c
                        scene_paths_map[int(sc_num)] = resolved

            # Check required scene numbers from scene_plan
            scene_plan = project.scene_plan or {}
            plan_scenes = scene_plan.get("scenes", [])
            if plan_scenes:
                expected_scenes = [
                    sc.get("scene_number") for sc in plan_scenes if sc.get("scene_number") is not None
                ]
            else:
                expected_scenes = sorted(scene_clips_map.keys())

            if not expected_scenes or not scene_clips_map:
                raise BadRequestError(detail="No video clips found for project. Generate video clips first.")

            for sc_num in expected_scenes:
                if sc_num not in scene_clips_map:
                    raise BadRequestError(
                        detail=f"Missing video clip for scene {sc_num}. All scenes must be generated before composing final video."
                    )

            ordered_scene_numbers = sorted(expected_scenes)
            ordered_clip_assets = [scene_clips_map[n] for n in ordered_scene_numbers]
            ordered_clip_paths = [scene_paths_map[n] for n in ordered_scene_numbers]

            # B. Voiceover Audio
            vo_stmt = (
                select(GeneratedAsset)
                .where(
                    and_(
                        GeneratedAsset.project_id == project.id,
                        GeneratedAsset.asset_type == "VOICEOVER"
                    )
                )
                .order_by(GeneratedAsset.created_at.desc())
            )
            vo_asset = db.execute(vo_stmt).scalars().first()
            if not vo_asset:
                raise BadRequestError(
                    detail="Voiceover audio is missing. Generate voiceover before composing final video."
                )

            vo_path = self._resolve_file_path(vo_asset.file_path)
            if not vo_path:
                raise BadRequestError(
                    detail=f"Voiceover audio file does not exist on disk: {vo_asset.file_path}"
                )

            # C. Background Music Audio (ACE-Step or Static Library)
            music_stmt = (
                select(GeneratedAsset)
                .where(
                    and_(
                        GeneratedAsset.project_id == project.id,
                        GeneratedAsset.asset_type == "MUSIC"
                    )
                )
                .order_by(GeneratedAsset.created_at.desc())
            )
            music_asset = db.execute(music_stmt).scalars().first()
            if not music_asset:
                raise BadRequestError(
                    detail="Background music is missing. Select or generate music before composing final video."
                )

            music_path = self._resolve_file_path(music_asset.file_path)
            if not music_path:
                raise BadRequestError(
                    detail=f"Background music audio file does not exist on disk: {music_asset.file_path}"
                )

            job.progress_percent = 30
            db.commit()

            # 5. Build output path and FFmpeg composition command
            filename = f"final_{project.id}_{int(time.time())}.mp4"
            final_output_path = os.path.join(self.output_dir, filename)

            cmd = build_composition_command(
                clip_paths=ordered_clip_paths,
                voiceover_path=vo_path,
                music_path=music_path,
                output_path=final_output_path,
                aspect_ratio=project.aspect_ratio or "9:16",
                duration_seconds=project.duration_seconds or 15,
                ffmpeg_exe=self.ffmpeg_exe,
            )

            job.progress_percent = 50
            db.commit()

            # 6. Execute FFmpeg composition
            logger.info(
                f"Starting composition for project {project.id} "
                f"({len(ordered_clip_paths)} clips, ratio={project.aspect_ratio}, dur={project.duration_seconds}s)"
            )
            execute_composition(cmd=cmd, output_path=final_output_path)

            job.progress_percent = 85
            db.commit()

            # 7. Validate generated MP4 file
            meta = validate_video_file(
                file_path=final_output_path,
                expected_duration=float(project.duration_seconds or 15),
                expected_ratio=project.aspect_ratio or "9:16",
                ffmpeg_exe=self.ffmpeg_exe,
            )

            try:
                rel_path = os.path.relpath(final_output_path, start=".").replace("\\", "/")
            except ValueError:
                rel_path = final_output_path.replace("\\", "/")

            # 8. Clean up old FINAL_VIDEO assets for this project to keep assets current
            old_assets = db.execute(
                select(GeneratedAsset).where(
                    and_(
                        GeneratedAsset.project_id == project.id,
                        GeneratedAsset.asset_type == "FINAL_VIDEO"
                    )
                )
            ).scalars().all()
            for old in old_assets:
                try:
                    db.delete(old)
                except Exception:
                    pass
            db.commit()

            # 9. Create new GeneratedAsset record
            final_asset = GeneratedAsset(
                id=uuid.uuid4(),
                project_id=project.id,
                asset_type="FINAL_VIDEO",
                file_path=rel_path,
                duration_seconds=float(meta["duration_seconds"]),
                file_size=meta["file_size"],
                meta_data={
                    "resolution": meta["resolution"],
                    "width": meta["width"],
                    "height": meta["height"],
                    "aspect_ratio": project.aspect_ratio or "9:16",
                    "fps": 30,
                    "video_codec": meta["video_codec"],
                    "audio_codec": meta["audio_codec"],
                    "scene_count": len(ordered_clip_assets),
                    "clip_asset_ids": [str(c.id) for c in ordered_clip_assets],
                    "voiceover_asset_id": str(vo_asset.id),
                    "music_asset_id": str(music_asset.id),
                    "music_metadata": music_asset.meta_data,
                    "voiceover_metadata": vo_asset.meta_data,
                },
            )
            db.add(final_asset)

            # 10. Update Project Status to COMPLETED
            project.status = "COMPLETED"

            # 11. Update Job Status to COMPLETED
            job.status = "COMPLETED"
            job.progress_percent = 100
            job.completed_at = datetime.now(timezone.utc)
            job.external_provider_ids = {
                **(job.external_provider_ids or {}),
                "final_video_asset_id": str(final_asset.id),
                "duration_seconds": meta["duration_seconds"],
                "resolution": meta["resolution"],
                "file_size": meta["file_size"],
            }
            db.commit()
            db.refresh(job)

            logger.info(
                f"Video composition completed successfully for project {project.id}: "
                f"Asset {final_asset.id} ({meta['resolution']}, {meta['duration_seconds']}s, {meta['file_size']} bytes)"
            )
            return job

        except Exception as exc:
            logger.error(f"Video composition failed for project {project_id}: {exc}", exc_info=True)
            if job:
                job.status = "FAILED"
                job.error_message = str(exc)
                job.completed_at = datetime.now(timezone.utc)
                try:
                    db.commit()
                    db.refresh(job)
                except Exception:
                    db.rollback()
            raise
        finally:
            if db_session_factory is SessionLocal:
                db.close()

    async def compose_project_video(
        self,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        db_session_factory: Callable[[], DBSession] = SessionLocal,
        job_id: Optional[uuid.UUID] = None,
        force_regenerate: bool = False,
    ) -> GenerationJob:
        """Asynchronously orchestrates end-to-end video composition on a worker thread."""
        return await asyncio.to_thread(
            self._run_composition_sync,
            project_id=project_id,
            user_id=user_id,
            db_session_factory=db_session_factory,
            job_id=job_id,
            force_regenerate=force_regenerate,
        )


def get_composition_service() -> VideoCompositionService:
    """Dependency provider for VideoCompositionService."""
    return VideoCompositionService()
