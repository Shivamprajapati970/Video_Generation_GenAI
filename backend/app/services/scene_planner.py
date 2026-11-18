import os
import uuid
import logging
import asyncio
from typing import List, Dict, Any, Optional, Set
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session as DBSession, selectinload
from sqlalchemy import select, desc

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.exceptions import (
    NotFoundError,
    PermissionDeniedError,
    BadRequestError,
    RateLimitError,
    ValidationError as AppValidationError,
)
from app.models.project import VideoProject
from app.models.image import ProjectImage
from app.models.image_analysis import ImageAnalysisCheckpoint
from app.models.job import GenerationJob
from app.schemas.scene import ScenePlan, ImageAnalysisItem
from app.schemas.checkpoint import AnalysisProgressResponse, ImageAnalysisCheckpointResponse
from app.ai.groq_vision import GroqVisionProvider
from app.ai.base import BaseVisionProvider
from app.services.storage import get_storage_service

logger = logging.getLogger(__name__)


class ScenePlanningService:
    """Service orchestrating AI image analysis and multi-scene cinematic planning
    with persistent checkpoint-based resume for Groq rate limits (HTTP 429).
    """

    def __init__(self, vision_provider: Optional[BaseVisionProvider] = None):
        self.vision_provider = vision_provider or GroqVisionProvider()
        self._project_locks: Dict[uuid.UUID, asyncio.Lock] = {}

    def _get_project_lock(self, project_id: uuid.UUID) -> asyncio.Lock:
        if project_id not in self._project_locks:
            self._project_locks[project_id] = asyncio.Lock()
        return self._project_locks[project_id]

    def get_or_init_checkpoints(
        self,
        project_id: uuid.UUID,
        images: List[ProjectImage],
        db: DBSession,
        force: bool = False,
    ) -> List[ImageAnalysisCheckpoint]:
        """Ensures that every project image has a corresponding ImageAnalysisCheckpoint record in DB.
        
        If force=True, existing checkpoints are reset to PENDING and their results cleared.
        Returns the ordered list of checkpoints matching image order_index.
        """
        stmt = (
            select(ImageAnalysisCheckpoint)
            .where(ImageAnalysisCheckpoint.project_id == project_id)
            .order_by(ImageAnalysisCheckpoint.order_index.asc())
        )
        existing_cps = {cp.image_id: cp for cp in db.execute(stmt).scalars().all()}

        checkpoints: List[ImageAnalysisCheckpoint] = []
        for img in images:
            cp = existing_cps.get(img.id)
            if cp is None:
                cp = ImageAnalysisCheckpoint(
                    project_id=project_id,
                    image_id=img.id,
                    order_index=img.order_index,
                    status="PENDING",
                    retry_count=0,
                )
                db.add(cp)
                db.flush()
            elif force:
                cp.status = "PENDING"
                cp.analysis_result = None
                cp.error_message = None
                cp.completed_at = None
                cp.retry_count = 0
            checkpoints.append(cp)

        db.commit()
        for cp in checkpoints:
            db.refresh(cp)
        return sorted(checkpoints, key=lambda x: x.order_index)

    def get_project_analysis_progress(
        self,
        project_id: uuid.UUID,
        user_id: Optional[uuid.UUID],
        db: DBSession,
    ) -> AnalysisProgressResponse:
        """Retrieves real-time progress for reference photo analysis including checkpoint states,
        cooldown remaining, and active job status.
        """
        proj_stmt = select(VideoProject).where(VideoProject.id == project_id)
        project = db.execute(proj_stmt).scalar_one_or_none()
        if not project:
            raise NotFoundError(detail="Video project not found")

        if user_id and project.user_id != user_id:
            raise PermissionDeniedError(detail="You do not have permission to view this project")

        cp_stmt = (
            select(ImageAnalysisCheckpoint)
            .where(ImageAnalysisCheckpoint.project_id == project_id)
            .order_by(ImageAnalysisCheckpoint.order_index.asc())
        )
        checkpoints = db.execute(cp_stmt).scalars().all()

        job_stmt = (
            select(GenerationJob)
            .where(
                GenerationJob.project_id == project_id,
                GenerationJob.stage == "ANALYZING_IMAGES",
            )
            .order_by(desc(GenerationJob.created_at))
            .limit(1)
        )
        job = db.execute(job_stmt).scalar_one_or_none()

        completed_count = sum(1 for c in checkpoints if c.status == "COMPLETED")
        pending_count = sum(1 for c in checkpoints if c.status != "COMPLETED")

        active_image_id = None
        active_order_index = None
        job_status = job.status if job else None
        rate_limit_reset_at = None
        rate_limit_type = None
        cooldown_remaining = None

        if job and job.external_provider_ids and isinstance(job.external_provider_ids, dict):
            ext = job.external_provider_ids
            raw_active_img = ext.get("active_image_id")
            if raw_active_img:
                try:
                    active_image_id = uuid.UUID(raw_active_img)
                except (ValueError, TypeError):
                    pass
            active_order_index = ext.get("active_order_index")
            rate_limit_type = ext.get("rate_limit_type")
            raw_reset = ext.get("rate_limit_reset_at")
            if raw_reset:
                try:
                    rate_limit_reset_at = datetime.fromisoformat(raw_reset)
                    now = datetime.now(timezone.utc)
                    if rate_limit_reset_at.tzinfo is None:
                        rate_limit_reset_at = rate_limit_reset_at.replace(tzinfo=timezone.utc)
                    rem = (rate_limit_reset_at - now).total_seconds()
                    cooldown_remaining = max(0.0, rem) if rem > 0 else 0.0
                except Exception:
                    pass

        cp_responses = [
            ImageAnalysisCheckpointResponse(
                id=c.id,
                project_id=c.project_id,
                image_id=c.image_id,
                order_index=c.order_index,
                status=c.status,
                analysis_result=c.analysis_result,
                error_message=c.error_message,
                retry_count=c.retry_count,
                completed_at=c.completed_at,
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
            for c in checkpoints
        ]

        return AnalysisProgressResponse(
            project_id=project.id,
            total_images=len(checkpoints),
            completed_count=completed_count,
            pending_count=pending_count,
            active_image_id=active_image_id,
            active_order_index=active_order_index,
            job_id=job.id if job else None,
            job_status=job_status,
            rate_limit_reset_at=rate_limit_reset_at,
            rate_limit_type=rate_limit_type,
            cooldown_seconds_remaining=cooldown_remaining,
            auto_resume=True,
            checkpoints=cp_responses,
        )

    async def execute_checkpoint_planning_pipeline(
        self,
        project_id: uuid.UUID,
        user_id: Optional[uuid.UUID],
        db_session_factory,
        force_regenerate: bool = False,
        batch_size: Optional[int] = None,
        job_id: Optional[uuid.UUID] = None,
        max_rate_limit_retries: int = 10,
    ) -> ScenePlan:
        """Core resilient scene planning pipeline with checkpoint-based resume for Groq 429 rate limits.
        
        Execution guarantees:
        1. Single concurrent worker per project (asyncio.Lock).
        2. Checks DB for existing completed checkpoints; skips completed images.
        3. Commits successful image analyses immediately to DB.
        4. On HTTP 429 (ITPM, TPM, RPM, OTPM):
           - Sets job status to WAITING_FOR_RATE_LIMIT.
           - Records reset cooldown and timestamp.
           - Automatically waits cooldown period.
           - Resumes from the first unanalyzed image without restarting from Image 1.
        5. Automatically synthesizes ScenePlan once all checkpoints are COMPLETED.
        6. If scene plan synthesis hits 429, waits and retries synthesis without re-analyzing images.
        """
        lock = self._get_project_lock(project_id)
        worker_token = str(uuid.uuid4())
        max_retries = max_rate_limit_retries or getattr(settings, "GROQ_MAX_RETRIES", 10)
        async with lock:
            with db_session_factory() as db:
                # 1. Fetch and validate project with row-level lock for cross-process concurrency safety
                proj_stmt = (
                    select(VideoProject)
                    .options(selectinload(VideoProject.images))
                    .where(VideoProject.id == project_id)
                    .with_for_update()
                )
                project = db.execute(proj_stmt).scalar_one_or_none()
                if not project:
                    raise NotFoundError(detail="Video project not found")

                if user_id and project.user_id != user_id:
                    raise PermissionDeniedError(detail="You do not have permission to analyze this project")

                img_stmt = (
                    select(ProjectImage)
                    .where(ProjectImage.project_id == project.id)
                    .order_by(ProjectImage.order_index.asc())
                )
                images = db.execute(img_stmt).scalars().all()

                if not images or len(images) == 0:
                    raise BadRequestError(
                        detail="Cannot generate scene plan without reference images. Please upload between 1 and 5 images."
                    )
                if len(images) > 5:
                    raise BadRequestError(
                        detail=f"Project exceeds maximum allowed reference images (found {len(images)}, maximum is 5)."
                    )

                # Verify files exist on disk
                storage = get_storage_service()
                image_metadata_map: Dict[str, Dict[str, Any]] = {}
                valid_image_ids: Set[str] = set()

                for img in images:
                    img_id_str = str(img.id)
                    valid_image_ids.add(img_id_str)
                    try:
                        full_path = storage.get_full_path(img.file_path)
                    except PermissionError:
                        if os.path.isabs(img.file_path) and os.path.isfile(img.file_path):
                            full_path = img.file_path
                        else:
                            raise PermissionDeniedError(detail=f"Invalid storage path for image {img.original_filename}")

                    if not os.path.isfile(full_path):
                        raise BadRequestError(
                            detail=f"Reference image file '{img.original_filename}' not found in storage. Please re-upload."
                        )

                    image_metadata_map[img_id_str] = {
                        "image_id": img_id_str,
                        "file_path": full_path,
                        "original_filename": img.original_filename,
                        "order_index": img.order_index,
                    }

                # Check plan reuse
                if project.scene_plan and isinstance(project.scene_plan, dict) and not force_regenerate:
                    existing_plan_data = dict(project.scene_plan)
                    same_duration = (existing_plan_data.get("total_duration") == project.duration_seconds)
                    same_occasion = (existing_plan_data.get("occasion") == project.occasion)
                    same_aspect = (existing_plan_data.get("aspect_ratio") == project.aspect_ratio)

                    existing_scenes = existing_plan_data.get("scenes", [])
                    plan_img_ids = {str(rid) for sc in existing_scenes for rid in sc.get("reference_image_ids", [])}

                    if same_duration and same_occasion and same_aspect and plan_img_ids.issubset(valid_image_ids):
                        existing_plan_data["music_mood"] = project.music_mood
                        if project.custom_music_prompt:
                            existing_plan_data["background_music_prompt"] = project.custom_music_prompt
                        else:
                            existing_plan_data["background_music_prompt"] = (
                                f"{project.music_mood} soundtrack tailored for {project.occasion}"
                            )
                        try:
                            reused_plan = ScenePlan.model_validate(existing_plan_data)
                            logger.info(f"Reusing existing ScenePlan for project {project.id}. Zero AI tokens consumed.")
                            project.scene_plan = reused_plan.model_dump()
                            project.status = "PLANNED"
                            db.commit()
                            return reused_plan
                        except Exception as exc:
                            logger.warning(f"Could not reuse existing plan: {exc}")

                # Ensure/manage GenerationJob with atomic claim
                job = None
                if job_id:
                    job = db.get(GenerationJob, job_id)
                if not job:
                    job_stmt = (
                        select(GenerationJob)
                        .where(
                            GenerationJob.project_id == project.id,
                            GenerationJob.stage == "ANALYZING_IMAGES",
                            GenerationJob.status.in_(["QUEUED", "RUNNING", "WAITING_FOR_RATE_LIMIT"]),
                        )
                        .order_by(desc(GenerationJob.created_at))
                        .limit(1)
                    )
                    job = db.execute(job_stmt).scalar_one_or_none()

                # Cross-process duplicate worker guard
                if job and job.status == "RUNNING" and job.external_provider_ids:
                    existing_worker = job.external_provider_ids.get("worker_id")
                    claimed_at_str = job.external_provider_ids.get("claimed_at")
                    if existing_worker and existing_worker != worker_token and claimed_at_str:
                        try:
                            claimed_dt = datetime.fromisoformat(claimed_at_str)
                            if claimed_dt.tzinfo is None:
                                claimed_dt = claimed_dt.replace(tzinfo=timezone.utc)
                            if (datetime.now(timezone.utc) - claimed_dt).total_seconds() < 300:
                                logger.warning(
                                    f"Project {project_id} is already claimed by active worker {existing_worker}. "
                                    "Aborting duplicate worker execution."
                                )
                                if project.scene_plan:
                                    return ScenePlan.model_validate(project.scene_plan)
                                return None  # type: ignore
                        except Exception:
                            pass

                was_waiting_rate_limit = False
                existing_reset_at_str = None
                if not job:
                    job = GenerationJob(
                        project_id=project.id,
                        stage="ANALYZING_IMAGES",
                        status="RUNNING",
                        progress_percent=0,
                        started_at=datetime.now(timezone.utc),
                        external_provider_ids={
                            "total_images": len(images),
                            "completed_images": [],
                            "worker_id": worker_token,
                            "claimed_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    db.add(job)
                    db.commit()
                    db.refresh(job)
                else:
                    was_waiting_rate_limit = (job.status == "WAITING_FOR_RATE_LIMIT")
                    if job.external_provider_ids:
                        existing_reset_at_str = job.external_provider_ids.get("rate_limit_reset_at")
                    job.status = "RUNNING"
                    if job.external_provider_ids is None:
                        job.external_provider_ids = {}
                    job.external_provider_ids["worker_id"] = worker_token
                    job.external_provider_ids["claimed_at"] = datetime.now(timezone.utc).isoformat()
                    db.commit()

                # Ensure image analysis checkpoints exist
                self.get_or_init_checkpoints(project.id, images, db, force=force_regenerate)

            # Check if this job was waiting for rate-limit reset before a backend restart
            if was_waiting_rate_limit and existing_reset_at_str:
                try:
                    reset_dt = datetime.fromisoformat(existing_reset_at_str)
                    if reset_dt.tzinfo is None:
                        reset_dt = reset_dt.replace(tzinfo=timezone.utc)
                    rem_cooldown = (reset_dt - datetime.now(timezone.utc)).total_seconds()
                    if rem_cooldown > 0:
                        cooldown_s = min(60.0, max(0.1, rem_cooldown))
                        logger.info(
                            f"Startup Recovery: Job {job.id} waiting for rate limit until {reset_dt.isoformat()}. "
                            f"Waiting remaining cooldown {cooldown_s:.1f}s before resuming..."
                        )
                        if not getattr(self.vision_provider, "effective_mock_mode", False) and not settings.is_testing:
                            await asyncio.sleep(cooldown_s)
                except Exception as exc:
                    logger.warning(f"Failed parsing rate_limit_reset_at on startup recovery: {exc}")

            # Execution loop: analyze images until all checkpoints are COMPLETED
            rate_limit_attempts = 0
            configured_batch_size = batch_size or getattr(settings, "GROQ_VISION_BATCH_SIZE", 3)
            current_batch_size = max(1, min(configured_batch_size, 3))

            while True:
                with db_session_factory() as db:
                    # Query current checkpoint statuses
                    cp_stmt = (
                        select(ImageAnalysisCheckpoint)
                        .where(ImageAnalysisCheckpoint.project_id == project_id)
                        .order_by(ImageAnalysisCheckpoint.order_index.asc())
                    )
                    all_cps = db.execute(cp_stmt).scalars().all()
                    completed_cps = [c for c in all_cps if c.status == "COMPLETED"]
                    pending_cps = [c for c in all_cps if c.status != "COMPLETED"]

                    total_cps = len(all_cps)
                    completed_count = len(completed_cps)

                    # Update progress in job
                    cur_job = db.get(GenerationJob, job.id)
                    if cur_job:
                        cur_job.status = "RUNNING"
                        cur_job.progress_percent = min(80, int((completed_count / total_cps) * 75))
                        if cur_job.external_provider_ids is None:
                            cur_job.external_provider_ids = {}
                        cur_job.external_provider_ids["completed_images"] = [str(c.image_id) for c in completed_cps]
                        db.commit()

                    if not pending_cps:
                        logger.info(f"All {total_cps} image analysis checkpoints COMPLETED for project {project_id}.")
                        break

                    # Select next batch of pending checkpoints
                    batch_cps = pending_cps[:current_batch_size]
                    batch_meta = [image_metadata_map[str(c.image_id)] for c in batch_cps]

                    # Mark in-progress
                    for c in batch_cps:
                        c.status = "IN_PROGRESS"
                    if cur_job and cur_job.external_provider_ids:
                        cur_job.external_provider_ids["active_image_id"] = str(batch_cps[0].image_id)
                        cur_job.external_provider_ids["active_order_index"] = batch_cps[0].order_index
                    db.commit()

                    proj = db.get(VideoProject, project_id)
                    user_desc = proj.description or ""
                    occasion = proj.occasion

                logger.info(
                    f"Analyzing batch of {len(batch_meta)} image(s) for project {project_id} "
                    f"(indices: {[m['order_index'] for m in batch_meta]}, completed: {completed_count}/{total_cps})"
                )

                try:
                    analyses: List[ImageAnalysisItem] = await self.vision_provider.analyze_image_batch(
                        batch_images=batch_meta,
                        user_description=user_desc,
                        occasion=occasion,
                    )

                    with db_session_factory() as db:
                        analyses_by_id = {str(a.image_id): a for a in analyses}
                        for b_meta in batch_meta:
                            b_id = str(b_meta["image_id"])
                            item = analyses_by_id.get(b_id)
                            cp = db.execute(
                                select(ImageAnalysisCheckpoint).where(
                                    ImageAnalysisCheckpoint.project_id == project_id,
                                    ImageAnalysisCheckpoint.image_id == uuid.UUID(b_id),
                                )
                            ).scalar_one_or_none()

                            if cp and item:
                                cp.status = "COMPLETED"
                                cp.analysis_result = item.model_dump()
                                cp.completed_at = datetime.now(timezone.utc)
                                cp.error_message = None
                                logger.info(
                                    f"Checkpoint SAVED: Image {cp.order_index + 1} ({cp.image_id}) COMPLETED and persisted."
                                )
                        db.commit()

                    # Reset consecutive rate limit attempts on success
                    rate_limit_attempts = 0

                except RateLimitError as rle:
                    rate_limit_attempts += 1
                    if rate_limit_attempts > max_rate_limit_retries:
                        with db_session_factory() as db:
                            cur_job = db.get(GenerationJob, job.id)
                            if cur_job:
                                cur_job.status = "FAILED"
                                cur_job.error_message = f"Exceeded maximum rate limit retries ({max_rate_limit_retries}): {rle.detail}"
                                db.commit()
                        raise

                    if rle.retry_after is not None and rle.retry_after > 0:
                        retry_after = float(rle.retry_after)
                    else:
                        init_backoff = getattr(settings, "GROQ_INITIAL_BACKOFF_SECONDS", 5.0)
                        factor = getattr(settings, "GROQ_BACKOFF_FACTOR", 2.0)
                        max_backoff = getattr(settings, "GROQ_MAX_BACKOFF_SECONDS", 60.0)
                        retry_after = min(max_backoff, init_backoff * (factor ** (rate_limit_attempts - 1)))

                    # If mock/testing mode, minimize sleep duration
                    if getattr(self.vision_provider, "effective_mock_mode", False) or settings.is_testing:
                        cooldown_s = 0.05
                    else:
                        cooldown_s = min(60.0, max(2.0, retry_after + 1.0))

                    reset_timestamp = datetime.now(timezone.utc) + timedelta(seconds=retry_after)

                    with db_session_factory() as db:
                        for b_meta in batch_meta:
                            b_id = str(b_meta["image_id"])
                            cp = db.execute(
                                select(ImageAnalysisCheckpoint).where(
                                    ImageAnalysisCheckpoint.project_id == project_id,
                                    ImageAnalysisCheckpoint.image_id == uuid.UUID(b_id),
                                )
                            ).scalar_one_or_none()
                            if cp and cp.status != "COMPLETED":
                                cp.status = "PENDING"
                                cp.retry_count += 1
                                cp.error_message = f"Rate limit ({rle.code}): {rle.detail}"

                        cur_job = db.get(GenerationJob, job.id)
                        if cur_job:
                            cur_job.status = "WAITING_FOR_RATE_LIMIT"
                            if cur_job.external_provider_ids is None:
                                cur_job.external_provider_ids = {}
                            cur_job.external_provider_ids.update({
                                "active_image_id": str(batch_meta[0]["image_id"]),
                                "active_order_index": batch_meta[0]["order_index"],
                                "rate_limit_type": rle.code or "RATE_LIMIT_EXCEEDED",
                                "rate_limit_reset_at": reset_timestamp.isoformat(),
                                "retry_after": retry_after,
                            })
                        db.commit()

                    logger.warning(
                        f"Groq Rate Limit ({rle.code}) hit on image {batch_meta[0]['order_index'] + 1}. "
                        f"Status set to WAITING_FOR_RATE_LIMIT. Sleeping {cooldown_s:.2f}s before auto-resume..."
                    )
                    await asyncio.sleep(cooldown_s)

                    # After wait, if batch was multi-image, drop to single-image requests for finer granularity
                    if current_batch_size > 1:
                        current_batch_size = 1
                        logger.info("Reduced subsequent Groq vision batch size to 1 image per request for granular token safety.")

                except Exception as exc:
                    with db_session_factory() as db:
                        for b_meta in batch_meta:
                            b_id = str(b_meta["image_id"])
                            cp = db.execute(
                                select(ImageAnalysisCheckpoint).where(
                                    ImageAnalysisCheckpoint.project_id == project_id,
                                    ImageAnalysisCheckpoint.image_id == uuid.UUID(b_id),
                                )
                            ).scalar_one_or_none()
                            if cp and cp.status != "COMPLETED":
                                cp.status = "FAILED"
                                cp.error_message = str(exc)

                        cur_job = db.get(GenerationJob, job.id)
                        if cur_job:
                            cur_job.status = "FAILED"
                            cur_job.error_message = str(exc)
                        db.commit()
                    logger.error(f"Failed analyzing image batch: {exc}")
                    raise

            # ---------------------------------------------------------------
            # Automatic ScenePlan Synthesis Phase
            # ---------------------------------------------------------------
            with db_session_factory() as db:
                cp_stmt = (
                    select(ImageAnalysisCheckpoint)
                    .where(ImageAnalysisCheckpoint.project_id == project_id)
                    .order_by(ImageAnalysisCheckpoint.order_index.asc())
                )
                final_cps = db.execute(cp_stmt).scalars().all()
                combined_analyses = [
                    ImageAnalysisItem.model_validate(c.analysis_result)
                    for c in final_cps
                    if c.analysis_result
                ]

                proj = db.get(VideoProject, project_id)
                proj_meta = {
                    "occasion": proj.occasion,
                    "description": proj.description or "",
                    "duration": proj.duration_seconds,
                    "aspect_ratio": proj.aspect_ratio,
                    "music_mood": proj.music_mood,
                    "custom_music_prompt": proj.custom_music_prompt,
                }
                cur_job = db.get(GenerationJob, job.id)
                if cur_job:
                    cur_job.status = "RUNNING"
                    cur_job.progress_percent = 85
                    if cur_job.external_provider_ids is None:
                        cur_job.external_provider_ids = {}
                    cur_job.external_provider_ids["stage"] = "SYNTHESIZING_SCENE_PLAN"
                    db.commit()

            logger.info(
                f"All {len(combined_analyses)} images analyzed successfully. "
                f"Synthesizing complete ScenePlan for project {project_id}..."
            )

            # Synthesize ScenePlan with rate limit retry protection
            synthesis_attempts = 0
            while True:
                try:
                    plan: ScenePlan = await self.vision_provider.synthesize_scene_plan(
                        combined_analyses=combined_analyses,
                        project_metadata=proj_meta,
                    )
                    break
                except RateLimitError as rle:
                    synthesis_attempts += 1
                    if synthesis_attempts > max_rate_limit_retries:
                        with db_session_factory() as db:
                            cur_job = db.get(GenerationJob, job.id)
                            if cur_job:
                                cur_job.status = "FAILED"
                                cur_job.error_message = f"Exceeded rate limit retries during ScenePlan synthesis: {rle.detail}"
                                db.commit()
                        raise

                    if rle.retry_after is not None and rle.retry_after > 0:
                        retry_after = float(rle.retry_after)
                    else:
                        init_backoff = getattr(settings, "GROQ_INITIAL_BACKOFF_SECONDS", 5.0)
                        factor = getattr(settings, "GROQ_BACKOFF_FACTOR", 2.0)
                        max_backoff = getattr(settings, "GROQ_MAX_BACKOFF_SECONDS", 60.0)
                        retry_after = min(max_backoff, init_backoff * (factor ** (synthesis_attempts - 1)))

                    if getattr(self.vision_provider, "effective_mock_mode", False) or settings.is_testing:
                        cooldown_s = 0.05
                    else:
                        cooldown_s = min(60.0, max(2.0, retry_after + 1.0))
                    reset_timestamp = datetime.now(timezone.utc) + timedelta(seconds=retry_after)

                    with db_session_factory() as db:
                        cur_job = db.get(GenerationJob, job.id)
                        if cur_job:
                            cur_job.status = "WAITING_FOR_RATE_LIMIT"
                            if cur_job.external_provider_ids is None:
                                cur_job.external_provider_ids = {}
                            cur_job.external_provider_ids.update({
                                "stage": "SYNTHESIZING_SCENE_PLAN",
                                "rate_limit_type": rle.code or "RATE_LIMIT_EXCEEDED",
                                "rate_limit_reset_at": reset_timestamp.isoformat(),
                                "retry_after": retry_after,
                            })
                            db.commit()

                    logger.warning(
                        f"Groq Rate Limit during ScenePlan synthesis. Status set to WAITING_FOR_RATE_LIMIT. "
                        f"Sleeping {cooldown_s:.2f}s before retry (images remain completed)..."
                    )
                    await asyncio.sleep(cooldown_s)

            # Strict validations
            with db_session_factory() as db:
                proj = db.get(VideoProject, project_id)
                total_scene_duration = sum(s.duration_seconds for s in plan.scenes)
                if total_scene_duration != proj.duration_seconds:
                    raise AppValidationError(
                        detail=f"Scene duration sum mismatch: scenes sum to {total_scene_duration}s, expected {proj.duration_seconds}s"
                    )

                if not (3 <= len(plan.scenes) <= 6):
                    raise AppValidationError(
                        detail=f"Scene count must be between 3 and 6 scenes, got {len(plan.scenes)}"
                    )

                first_valid_id = list(valid_image_ids)[0]
                for scene in plan.scenes:
                    cleaned_ids = [str(ref_id) for ref_id in scene.reference_image_ids if str(ref_id) in valid_image_ids]
                    if not cleaned_ids:
                        cleaned_ids = [first_valid_id]
                    scene.reference_image_ids = cleaned_ids

                    if not scene.narration_text or not scene.narration_text.strip():
                        raise AppValidationError(
                            detail=f"Scene {scene.scene_number} is missing mandatory voiceover narration text"
                        )

                # Reconcile unselected images explanation
                used_ids = {str(rid) for sc in plan.scenes for rid in sc.reference_image_ids}
                unused_ids = valid_image_ids - used_ids
                if unused_ids and not plan.unselected_images_explanation:
                    plan.unselected_images_explanation = (
                        f"Reference image(s) {', '.join(sorted(unused_ids))} were not selected in order to maintain "
                        f"the requested {len(plan.scenes)}-scene ({proj.duration_seconds}s) narrative pacing."
                    )

                # Guarantee narration script consistency
                scene_narrations = [s.narration_text.strip() for s in plan.scenes if s.narration_text]
                components = []
                if plan.opening_narration and plan.opening_narration.strip():
                    components.append(plan.opening_narration.strip())
                components.extend(scene_narrations)
                if plan.closing_narration and plan.closing_narration.strip():
                    closing_text = plan.closing_narration.strip()
                    if scene_narrations and closing_text.lower() == scene_narrations[-1].lower():
                        closing_text = f"May these cherished moments and milestones inspire joy for years to come."
                        plan.closing_narration = closing_text
                    components.append(closing_text)
                plan.full_narration_script = " ".join(components)

                # Persist final ScenePlan and mark Job COMPLETED
                proj.scene_plan = plan.model_dump()
                proj.status = "PLANNED"

                cur_job = db.get(GenerationJob, job.id)
                if cur_job:
                    cur_job.status = "COMPLETED"
                    cur_job.progress_percent = 100
                    cur_job.completed_at = datetime.now(timezone.utc)
                    if cur_job.external_provider_ids is None:
                        cur_job.external_provider_ids = {}
                    cur_job.external_provider_ids["stage"] = "COMPLETED"

                db.commit()
                db.refresh(proj)

            logger.info(f"Scene planning pipeline completed successfully for project {project_id}")
            return plan

    async def generate_scene_plan(
        self,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        db: DBSession,
        force_regenerate: bool = False,
        batch_size: Optional[int] = None,
    ) -> ScenePlan:
        """Backward-compatible synchronous entry point: executes checkpoint planning pipeline."""
        return await self.execute_checkpoint_planning_pipeline(
            project_id=project_id,
            user_id=user_id,
            db_session_factory=SessionLocal,
            force_regenerate=force_regenerate,
            batch_size=batch_size,
        )

    def get_project_scene_plan(
        self,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        db: DBSession,
    ) -> ScenePlan:
        """Retrieves previously generated and validated ScenePlan from database."""
        stmt = select(VideoProject).where(VideoProject.id == project_id)
        project = db.execute(stmt).scalar_one_or_none()

        if not project:
            raise NotFoundError(detail="Video project not found")

        if project.user_id != user_id:
            raise PermissionDeniedError(detail="You do not have permission to view this project's scene plan")

        if not project.scene_plan:
            raise NotFoundError(detail="Scene plan has not been generated for this project yet")

        return ScenePlan.model_validate(project.scene_plan)

    async def resume_interrupted_jobs(self, db_session_factory) -> int:
        """Startup Recovery: checks for jobs interrupted mid-way (RUNNING, WAITING_FOR_RATE_LIMIT, QUEUED)
        and automatically resumes them without restarting from Image 1.
        """
        with db_session_factory() as db:
            stmt = (
                select(GenerationJob)
                .where(
                    GenerationJob.stage == "ANALYZING_IMAGES",
                    GenerationJob.status.in_(["RUNNING", "WAITING_FOR_RATE_LIMIT", "QUEUED"]),
                )
                .order_by(desc(GenerationJob.created_at))
            )
            interrupted_jobs = db.execute(stmt).scalars().all()
            
            # Deduplicate by project_id so only one recovery worker is started per project
            seen_projects: Set[uuid.UUID] = set()
            job_infos = []
            for j in interrupted_jobs:
                if j.project_id not in seen_projects:
                    seen_projects.add(j.project_id)
                    job_infos.append((j.id, j.project_id))

        resumed_count = 0
        for job_id, proj_id in job_infos:
            logger.info(
                f"Startup Recovery: Resuming interrupted scene planning job {job_id} for project {proj_id}"
            )
            asyncio.create_task(
                self.execute_checkpoint_planning_pipeline(
                    project_id=proj_id,
                    user_id=None,
                    db_session_factory=db_session_factory,
                    force_regenerate=False,
                    job_id=job_id,
                )
            )
            resumed_count += 1

        return resumed_count


_scene_planner_singleton: Optional[ScenePlanningService] = None


def get_scene_planning_service() -> ScenePlanningService:
    global _scene_planner_singleton
    if _scene_planner_singleton is None:
        _scene_planner_singleton = ScenePlanningService()
    return _scene_planner_singleton
