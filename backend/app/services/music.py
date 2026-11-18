import os
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Callable
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import select, and_, desc

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.exceptions import NotFoundError, BadRequestError, ServiceUnavailableError
from app.models.project import VideoProject
from app.models.job import GenerationJob
from app.models.asset import GeneratedAsset
from app.ai.base import BaseMusicProvider
from app.ai.hf_acestep import HFAceStepProvider, verify_music_content
from app.ai.static_music import StaticMusicLibraryProvider
from app.services.storage.base import BaseStorageService
from app.services.storage import get_storage_service

logger = logging.getLogger("video_gen_app.music")


class MusicService:
    """Orchestrates background music via ACE-Step AI generation and Static Royalty-Free Library."""

    def __init__(
        self,
        music_provider: Optional[BaseMusicProvider] = None,
        library_provider: Optional[StaticMusicLibraryProvider] = None,
        storage_service: Optional[BaseStorageService] = None,
    ):
        self.music_provider = music_provider or HFAceStepProvider()
        self.library_provider = library_provider or StaticMusicLibraryProvider()
        self.storage_service = storage_service or get_storage_service()

    def get_existing_music_asset(self, project_id: uuid.UUID, db: DBSession) -> Optional[GeneratedAsset]:
        """Returns the latest valid GeneratedAsset for project music that exists on disk."""
        stmt = (
            select(GeneratedAsset)
            .where(
                and_(
                    GeneratedAsset.project_id == project_id,
                    GeneratedAsset.asset_type == "MUSIC"
                )
            )
            .order_by(GeneratedAsset.created_at.desc())
        )
        asset = db.execute(stmt).scalars().first()
        if not asset:
            return None

        # Verify physical file existence
        clean_path = asset.file_path.replace("\\", "/").lstrip("/")
        candidates = [
            os.path.abspath(clean_path),
            os.path.abspath(os.path.join("storage", clean_path)),
            os.path.abspath(os.path.join(settings.OUTPUT_DIR, clean_path.replace("outputs/", ""))),
            os.path.abspath(os.path.join(settings.OUTPUT_DIR, "music", os.path.basename(clean_path))),
            os.path.abspath(os.path.join("backend", clean_path)),
            os.path.abspath(os.path.join("media", "music", clean_path.replace("media/music/", ""))),
            os.path.abspath(os.path.join("backend", "media", "music", clean_path.replace("media/music/", ""))),
        ]
        try:
            candidates.append(self.storage_service.get_full_path(clean_path))
        except Exception:
            pass

        for c in candidates:
            if os.path.isfile(c) and os.path.getsize(c) > 44:
                return asset

        return None

    def has_predefined_music_selected(self, project: VideoProject, db: DBSession) -> Optional[str]:
        """Checks if the project has a valid predefined static music library track selected.

        Returns the track_id if selected and available on disk, otherwise None.
        """
        track_id = None
        if project.scene_plan and isinstance(project.scene_plan, dict):
            track_id = project.scene_plan.get("selected_music_track_id")

        if not track_id:
            asset = db.execute(
                select(GeneratedAsset)
                .where(
                    and_(
                        GeneratedAsset.project_id == project.id,
                        GeneratedAsset.asset_type == "MUSIC"
                    )
                )
                .order_by(GeneratedAsset.created_at.desc())
            ).scalars().first()
            if asset and asset.meta_data and asset.meta_data.get("provider") == "static_library":
                track_id = asset.meta_data.get("track_id")

        if track_id:
            track = self.library_provider.get_track(track_id)
            if track and track.get("available"):
                return track_id

        return None

    def select_library_track_for_project(
        self,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        track_id: str,
        db: DBSession,
    ) -> GeneratedAsset:
        """Associates a selected Static Music Library track with a project as its GeneratedAsset."""
        # 1. Fetch project and verify ownership
        project = db.get(VideoProject, project_id)
        if not project or str(project.user_id) != str(user_id):
            raise NotFoundError(detail="Project not found or access denied")

        # 2. Retrieve and validate track from library
        track = self.library_provider.get_track(track_id)
        if not track:
            raise NotFoundError(detail=f"Music track '{track_id}' not found in library")
        if not track.get("available"):
            raise BadRequestError(detail=f"Audio file for track '{track_id}' is not available on disk")

        # 3. Clean up any previous MUSIC assets for this project to avoid duplicate tracks
        old_assets = db.execute(
            select(GeneratedAsset).where(
                and_(
                    GeneratedAsset.project_id == project.id,
                    GeneratedAsset.asset_type == "MUSIC"
                )
            )
        ).scalars().all()
        for old in old_assets:
            db.delete(old)
        db.flush()

        # 4. Create new GeneratedAsset record pointing to the local library audio
        asset = GeneratedAsset(
            id=uuid.uuid4(),
            project_id=project.id,
            asset_type="MUSIC",
            file_path=track["relative_path"],
            duration_seconds=float(track["duration_seconds"]),
            file_size=track["file_size"],
            meta_data={
                "provider": "static_library",
                "track_id": track["id"],
                "title": track["title"],
                "category": track["category"],
                "description": track.get("description", ""),
                "attribution": track.get("attribution", ""),
                "license": track.get("license", ""),
                "source": track.get("source", ""),
                "music_mood": track["category"].capitalize(),
                "format": track.get("format", "wav"),
                "sample_rate": track.get("sample_rate", 44100),
                "channels": track.get("channels", 2),
                "is_synthetic": True,
            },
        )
        db.add(asset)

        # 5. Persist selected track in project.scene_plan
        plan = dict(project.scene_plan or {})
        plan["selected_music_track_id"] = track["id"]
        project.scene_plan = plan
        flag_modified(project, "scene_plan")

        # 6. Update or initialize completed GenerationJob for UI polling sync
        job = db.execute(
            select(GenerationJob)
            .where(
                and_(
                    GenerationJob.project_id == project.id,
                    GenerationJob.stage == "GENERATING_MUSIC"
                )
            )
            .order_by(GenerationJob.created_at.desc())
        ).scalars().first()

        now = datetime.now(timezone.utc)
        if not job:
            job = GenerationJob(
                project_id=project.id,
                stage="GENERATING_MUSIC",
                status="COMPLETED",
                progress_percent=100,
                started_at=now,
                completed_at=now,
                external_provider_ids={
                    "provider": "static_library",
                    "track_id": track["id"],
                    "music_asset_id": str(asset.id),
                },
            )
            db.add(job)
        else:
            job.status = "COMPLETED"
            job.progress_percent = 100
            job.completed_at = now
            job.error_message = None
            job.external_provider_ids = {
                **(job.external_provider_ids or {}),
                "provider": "static_library",
                "track_id": track["id"],
                "music_asset_id": str(asset.id),
            }

        db.commit()
        db.refresh(asset)

        logger.info(
            f"Successfully selected library music track '{track_id}' for project {project.id} "
            f"(Asset {asset.id}, {track['duration_seconds']}s)"
        )
        return asset

    def unselect_library_track_for_project(
        self,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        db: DBSession,
    ) -> None:
        """Removes predefined library music selection from a project."""
        project = db.get(VideoProject, project_id)
        if not project or str(project.user_id) != str(user_id):
            raise NotFoundError(detail="Project not found or access denied")

        # Clear from scene_plan
        if project.scene_plan and "selected_music_track_id" in project.scene_plan:
            plan = dict(project.scene_plan)
            plan.pop("selected_music_track_id", None)
            project.scene_plan = plan
            flag_modified(project, "scene_plan")

        # Delete static library music asset if any
        music_assets = db.execute(
            select(GeneratedAsset).where(
                and_(
                    GeneratedAsset.project_id == project.id,
                    GeneratedAsset.asset_type == "MUSIC"
                )
            )
        ).scalars().all()
        for a in music_assets:
            if a.meta_data and a.meta_data.get("provider") == "static_library":
                db.delete(a)

        # Clear GenerationJob for GENERATING_MUSIC
        jobs = db.execute(
            select(GenerationJob).where(
                and_(
                    GenerationJob.project_id == project.id,
                    GenerationJob.stage == "GENERATING_MUSIC"
                )
            )
        ).scalars().all()
        for j in jobs:
            db.delete(j)

        db.commit()
        logger.info(f"Unselected music track for project {project.id}")

    async def generate_project_music(
        self,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        db_session_factory: Callable[[], DBSession] = SessionLocal,
        job_id: Optional[uuid.UUID] = None,
        force_regenerate: bool = False,
    ) -> GenerationJob:
        """Asynchronously handles background music for a project.

        Conditional workflow:
        1. Predefined Music Selected:
           - Automatically uses selected static library track.
           - Skips ACE-Step AI music generation completely (assert no API call, no provider request).
           - Ensures static music asset is active and job status is COMPLETED.
        2. No Predefined Music Selected:
           - Automatically invokes ACE-Step AI music generation with mood and context.
           - Saves generated music asset and marks job COMPLETED.
           - On failure, marks job FAILED and raises without silent fallback to static music.
        """
        db: DBSession = db_session_factory()
        job: Optional[GenerationJob] = None

        try:
            # 1. Fetch project and verify ownership
            project = db.get(VideoProject, project_id)
            if not project or str(project.user_id) != str(user_id):
                raise NotFoundError(detail="Project not found or access denied")

            # 2. Concurrency protection: Check for active music job already running or queued
            active_job_stmt = select(GenerationJob).where(
                and_(
                    GenerationJob.project_id == project.id,
                    GenerationJob.stage == "GENERATING_MUSIC",
                    GenerationJob.status.in_(["QUEUED", "RUNNING"]),
                )
            )
            if job_id:
                active_job_stmt = active_job_stmt.where(GenerationJob.id != job_id)
            active_job = db.execute(active_job_stmt).scalars().first()
            if active_job:
                raise BadRequestError(detail="A music generation job is already in progress for this project.")

            # 3. CASE 1: Predefined Music Selection
            selected_track_id = self.has_predefined_music_selected(project, db)
            if selected_track_id:
                logger.info(
                    f"Project {project.id} has predefined music track '{selected_track_id}' selected. "
                    f"Using predefined static track and bypassing ACE-Step generation completely."
                )
                asset = self.get_existing_music_asset(project.id, db)
                if not asset or (asset.meta_data or {}).get("track_id") != selected_track_id:
                    asset = self.select_library_track_for_project(
                        project_id=project.id,
                        user_id=user_id,
                        track_id=selected_track_id,
                        db=db,
                    )

                now = datetime.now(timezone.utc)
                if job_id:
                    job = db.get(GenerationJob, job_id)
                if not job:
                    job = db.execute(
                        select(GenerationJob)
                        .where(
                            and_(
                                GenerationJob.project_id == project.id,
                                GenerationJob.stage == "GENERATING_MUSIC"
                            )
                        )
                        .order_by(GenerationJob.created_at.desc())
                    ).scalars().first()

                if not job:
                    job = GenerationJob(
                        project_id=project.id,
                        stage="GENERATING_MUSIC",
                        status="COMPLETED",
                        progress_percent=100,
                        started_at=now,
                        completed_at=now,
                        external_provider_ids={
                            "provider": "static_library",
                            "track_id": selected_track_id,
                            "music_asset_id": str(asset.id),
                        },
                    )
                    db.add(job)
                else:
                    job.stage = "GENERATING_MUSIC"
                    job.status = "COMPLETED"
                    job.progress_percent = 100
                    job.started_at = job.started_at or now
                    job.completed_at = now
                    job.error_message = None
                    job.external_provider_ids = {
                        **(job.external_provider_ids or {}),
                        "provider": "static_library",
                        "track_id": selected_track_id,
                        "music_asset_id": str(asset.id),
                    }

                db.commit()
                db.refresh(job)
                return job

            # 4. CASE 2: No Predefined Music Selection -> ACE-Step AI Music Generation
            scene_plan = project.scene_plan or {}
            mood = (project.music_mood or scene_plan.get("music_mood") or "Cinematic").strip()
            custom_prompt = (scene_plan.get("background_music_prompt") or project.custom_music_prompt or "").strip()
            duration = int(project.duration_seconds or scene_plan.get("total_duration") or 15)

            # Check for existing AI music asset unless force_regenerate is requested
            existing = self.get_existing_music_asset(project.id, db)
            if existing and not force_regenerate and (existing.meta_data or {}).get("provider") != "static_library":
                logger.info(f"Project {project.id} already has a valid AI music asset {existing.id}. Skipping.")
                now = datetime.now(timezone.utc)
                if not job_id:
                    job = GenerationJob(
                        project_id=project.id,
                        stage="GENERATING_MUSIC",
                        status="COMPLETED",
                        progress_percent=100,
                        started_at=now,
                        completed_at=now,
                        external_provider_ids={"mood": mood, "reused_asset_id": str(existing.id)},
                    )
                    db.add(job)
                else:
                    job = db.get(GenerationJob, job_id)
                    if job:
                        job.stage = "GENERATING_MUSIC"
                        job.status = "COMPLETED"
                        job.progress_percent = 100
                        job.completed_at = now
                db.commit()
                if job:
                    db.refresh(job)
                return job

            # Initialize or update GenerationJob for ACE-Step
            now = datetime.now(timezone.utc)
            if job_id:
                job = db.get(GenerationJob, job_id)
            if not job:
                job = GenerationJob(
                    project_id=project.id,
                    stage="GENERATING_MUSIC",
                    status="RUNNING",
                    progress_percent=20,
                    started_at=now,
                    external_provider_ids={"mood": mood, "duration": duration, "provider": "ace_step"},
                )
                db.add(job)
            else:
                job.stage = "GENERATING_MUSIC"
                job.status = "RUNNING"
                job.progress_percent = 20
                job.started_at = job.started_at or now
                job.error_message = None

            db.commit()
            db.refresh(job)

            # Generate music with ACE-Step
            logger.info(
                f"Starting ACE-Step music generation for project {project.id} "
                f"(mood='{mood}', duration={duration}s)"
            )
            job.progress_percent = 40
            db.commit()

            audio_path = await self.music_provider.generate_music(
                mood=mood,
                custom_prompt=custom_prompt,
                duration_seconds=duration,
            )

            job.progress_percent = 80
            db.commit()

            # Verify audio content
            meta = verify_music_content(audio_path)
            try:
                rel_path = os.path.relpath(audio_path, start=".").replace("\\", "/")
            except ValueError:
                rel_path = audio_path.replace("\\", "/")

            # Clean up old music assets for this project
            old_assets = db.execute(
                select(GeneratedAsset).where(
                    and_(
                        GeneratedAsset.project_id == project.id,
                        GeneratedAsset.asset_type == "MUSIC"
                    )
                )
            ).scalars().all()
            for old in old_assets:
                try:
                    db.delete(old)
                except Exception:
                    pass
            db.commit()

            # Create new GeneratedAsset record
            asset = GeneratedAsset(
                id=uuid.uuid4(),
                project_id=project.id,
                asset_type="MUSIC",
                file_path=rel_path,
                duration_seconds=float(meta["duration_seconds"]),
                file_size=meta["file_size"],
                meta_data={
                    "music_mood": mood,
                    "music_prompt": custom_prompt,
                    "sample_rate": meta.get("sample_rate", 44100),
                    "channels": meta.get("channels", 2),
                    "format": "wav",
                    "provider": "ACE-Step/ACE-Step",
                },
            )
            db.add(asset)

            # Mark Job as COMPLETED
            job.status = "COMPLETED"
            job.progress_percent = 100
            job.completed_at = datetime.now(timezone.utc)
            job.external_provider_ids = {
                **(job.external_provider_ids or {}),
                "music_asset_id": str(asset.id),
                "duration_seconds": meta["duration_seconds"],
                "provider": "ACE-Step/ACE-Step",
            }
            db.commit()
            db.refresh(job)

            logger.info(
                f"Music generation completed for project {project.id}: "
                f"Asset {asset.id} ({meta['duration_seconds']}s, {meta['file_size']} bytes)"
            )
            return job

        except Exception as exc:
            logger.error(f"Music generation failed for project {project_id}: {exc}", exc_info=True)
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


def get_music_service() -> MusicService:
    """Dependency provider for MusicService."""
    return MusicService()
