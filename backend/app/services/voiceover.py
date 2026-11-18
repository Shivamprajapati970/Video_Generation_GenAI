import os
import uuid
import logging
from datetime import datetime
from typing import Optional, Dict, Any, Callable
from sqlalchemy.orm import Session as DBSession
from sqlalchemy import select, and_, desc

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.exceptions import NotFoundError, BadRequestError, ServiceUnavailableError
from app.models.project import VideoProject
from app.models.job import GenerationJob
from app.models.asset import GeneratedAsset
from app.ai.base import BaseTTSProvider
from app.ai.hf_chatterbox import HFChatterboxProvider, verify_audio_content
from app.services.storage.base import BaseStorageService
from app.services.storage import get_storage_service

logger = logging.getLogger("video_gen_app.voiceover")


class VoiceoverService:
    """Orchestrates AI voiceover narration synthesis using Chatterbox and manages asset lifecycle."""

    def __init__(
        self,
        tts_provider: Optional[BaseTTSProvider] = None,
        storage_service: Optional[BaseStorageService] = None,
    ):
        self.tts_provider = tts_provider or HFChatterboxProvider()
        self.storage_service = storage_service or get_storage_service()

    def get_existing_voiceover_asset(self, project_id: uuid.UUID, db: DBSession) -> Optional[GeneratedAsset]:
        """Returns the latest valid GeneratedAsset for project voiceover that exists on disk."""
        stmt = (
            select(GeneratedAsset)
            .where(
                and_(
                    GeneratedAsset.project_id == project_id,
                    GeneratedAsset.asset_type == "VOICEOVER"
                )
            )
            .order_by(GeneratedAsset.created_at.desc())
        )
        asset = db.execute(stmt).scalars().first()
        if not asset:
            return None

        if not asset or not asset.file_path:
            return None

        # Verify physical file existence
        clean_path = asset.file_path.replace("\\", "/").lstrip("/")
        if not clean_path:
            return None
        candidates = [
            os.path.abspath(clean_path),
            os.path.abspath(os.path.join("storage", clean_path)),
            os.path.abspath(os.path.join(settings.OUTPUT_DIR, clean_path.replace("outputs/", ""))),
            os.path.abspath(os.path.join(settings.OUTPUT_DIR, "audio", os.path.basename(clean_path))),
        ]
        try:
            candidates.append(self.storage_service.get_full_path(clean_path))
        except Exception:
            pass

        for c in candidates:
            if c and os.path.isfile(c) and os.path.getsize(c) > 44:
                return asset

        return None

    async def generate_project_voiceover(
        self,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        db_session_factory: Callable[[], DBSession] = SessionLocal,
        job_id: Optional[uuid.UUID] = None,
        force_regenerate: bool = False,
    ) -> GenerationJob:
        """Asynchronously synthesizes voiceover audio from the project's ScenePlan narration script."""
        db: DBSession = db_session_factory()
        job: Optional[GenerationJob] = None

        try:
            # 1. Fetch project and verify ownership
            project = db.get(VideoProject, project_id)
            if not project or str(project.user_id) != str(user_id):
                raise NotFoundError(detail="Project not found or access denied")

            scene_plan = project.scene_plan
            if not scene_plan or not isinstance(scene_plan, dict):
                raise BadRequestError(detail="Project has no ScenePlan. Run scene planning first.")

            # 2. Extract narration text
            narration_script = (scene_plan.get("full_narration_script") or "").strip()
            if not narration_script:
                # Assemble from scene narration texts
                scenes = scene_plan.get("scenes", [])
                scene_texts = [sc.get("narration_text", "").strip() for sc in scenes if sc.get("narration_text")]
                narration_script = " ".join(scene_texts).strip()

            if not narration_script:
                raise BadRequestError(detail="Project ScenePlan contains no narration script or scene lines.")

            # 3. Check for existing voiceover asset unless force_regenerate is requested
            existing = self.get_existing_voiceover_asset(project.id, db)
            if existing and not force_regenerate:
                logger.info(f"Project {project.id} already has a valid voiceover asset {existing.id}. Skipping.")
                if not job_id:
                    job = GenerationJob(
                        project_id=project.id,
                        stage="GENERATING_VOICEOVER",
                        status="COMPLETED",
                        progress_percent=100,
                        started_at=datetime.utcnow(),
                        completed_at=datetime.utcnow(),
                        external_provider_ids={"char_count": len(narration_script), "reused_asset_id": str(existing.id)},
                    )
                    db.add(job)
                else:
                    job = db.get(GenerationJob, job_id)
                    if job:
                        job.stage = "GENERATING_VOICEOVER"
                        job.status = "COMPLETED"
                        job.progress_percent = 100
                        job.completed_at = datetime.utcnow()
                db.commit()
                if job:
                    db.refresh(job)
                return job

            # 4. Initialize or update GenerationJob
            if job_id:
                job = db.get(GenerationJob, job_id)
            if not job:
                job = GenerationJob(
                    project_id=project.id,
                    stage="GENERATING_VOICEOVER",
                    status="RUNNING",
                    progress_percent=20,
                    started_at=datetime.utcnow(),
                    external_provider_ids={"char_count": len(narration_script)},
                )
                db.add(job)
            else:
                job.stage = "GENERATING_VOICEOVER"
                job.status = "RUNNING"
                job.progress_percent = 20
                job.started_at = job.started_at or datetime.utcnow()
                job.error_message = None

            db.commit()
            db.refresh(job)

            # 5. Synthesize voiceover audio with Chatterbox
            logger.info(f"Starting Chatterbox voiceover synthesis for project {project.id} ({len(narration_script)} chars)")
            job.progress_percent = 40
            db.commit()

            audio_path = await self.tts_provider.generate_voiceover(
                narration_text=narration_script,
                mood=project.music_mood or "cinematic",
                target_duration=project.duration_seconds,
            )

            if not audio_path:
                raise ServiceUnavailableError(detail="Chatterbox voiceover synthesis did not return an audio file.")

            job.progress_percent = 80
            db.commit()

            # 6. Verify audio content
            meta = verify_audio_content(audio_path)
            try:
                rel_path = os.path.relpath(audio_path, start=".").replace("\\", "/")
            except ValueError:
                rel_path = audio_path.replace("\\", "/")

            # 7. Remove previous voiceover asset if regenerating
            old_assets = db.execute(
                select(GeneratedAsset).where(
                    and_(
                        GeneratedAsset.project_id == project.id,
                        GeneratedAsset.asset_type == "VOICEOVER"
                    )
                )
            ).scalars().all()
            for old in old_assets:
                try:
                    db.delete(old)
                except Exception:
                    pass
            db.commit()

            # 8. Create new GeneratedAsset record
            asset = GeneratedAsset(
                id=uuid.uuid4(),
                project_id=project.id,
                asset_type="VOICEOVER",
                file_path=rel_path,
                duration_seconds=float(meta["duration_seconds"]),
                file_size=meta["file_size"],
                meta_data={
                    "narration_text": narration_script,
                    "sample_rate": meta["sample_rate"],
                    "channels": meta["channels"],
                    "bits_per_sample": meta["bits_per_sample"],
                    "format": "wav",
                    "provider": "ResembleAI/Chatterbox",
                    "max_amplitude": meta.get("max_amplitude", 0),
                },
            )
            db.add(asset)

            # 9. Mark Job as COMPLETED
            job.status = "COMPLETED"
            job.progress_percent = 100
            job.completed_at = datetime.utcnow()
            job.external_provider_ids = {
                **(job.external_provider_ids or {}),
                "voiceover_asset_id": str(asset.id),
                "duration_seconds": meta["duration_seconds"],
            }
            db.commit()
            db.refresh(job)

            logger.info(
                f"Voiceover generation completed for project {project.id}: "
                f"Asset {asset.id} ({meta['duration_seconds']}s, {meta['file_size']} bytes)"
            )
            return job

        except Exception as exc:
            logger.error(f"Voiceover generation failed for project {project_id}: {exc}", exc_info=True)
            if job:
                job.status = "FAILED"
                job.error_message = str(exc)
                job.completed_at = datetime.utcnow()
                try:
                    db.commit()
                    db.refresh(job)
                except Exception:
                    db.rollback()
            raise
        finally:
            if db_session_factory is SessionLocal:
                db.close()


def get_voiceover_service() -> VoiceoverService:
    """Dependency provider for VoiceoverService."""
    return VoiceoverService()
