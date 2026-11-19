import io
import os
import uuid
import logging
from typing import List, Optional, Union
from PIL import Image
from fastapi import APIRouter, Depends, status, UploadFile, File, Query, BackgroundTasks, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session as DBSession, selectinload
from sqlalchemy import select, desc, func, and_

from app.api.deps import get_db, get_current_user
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.exceptions import (
    NotFoundError,
    PermissionDeniedError,
    BadRequestError,
)
from app.models.user import User
from app.models.project import VideoProject
from app.models.image import ProjectImage
from app.models.job import GenerationJob
from app.schemas.project import (
    ProjectCreate,
    ProjectUpdate,
    ProjectResponse,
    ProjectDetailResponse,
)
from app.schemas.image import ImageResponse, ImageReorderRequest
from app.schemas.job import (
    JobResponse,
    ClipAssetResponse,
    VoiceoverAssetResponse,
    MusicAssetResponse,
    SelectMusicRequest,
    FinalVideoResponse,
)
from app.schemas.scene import ScenePlan
from app.schemas.checkpoint import AnalysisProgressResponse
from app.services.storage import get_storage_service
from app.services.scene_planner import get_scene_planning_service, ScenePlanningService
from app.services.video_generator import VideoGenerationService, get_video_generation_service
from app.services.voiceover import VoiceoverService, get_voiceover_service
from app.services.music import MusicService, get_music_service
from app.services.composer import VideoCompositionService, get_composition_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["Video Projects"])

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_IMAGE_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def _get_project_or_404(
    project_id: uuid.UUID,
    current_user: User,
    db: DBSession,
    for_update: bool = False,
) -> VideoProject:
    """Helper to fetch a project and verify authenticated ownership."""
    stmt = (
        select(VideoProject)
        .options(
            selectinload(VideoProject.images),
            selectinload(VideoProject.jobs),
            selectinload(VideoProject.assets),
        )
        .where(VideoProject.id == project_id)
    )
    if for_update:
        stmt = stmt.with_for_update()
    project = db.execute(stmt).scalar_one_or_none()

    if not project:
        raise NotFoundError(detail="Video project not found")

    if project.user_id != current_user.id:
        raise PermissionDeniedError(detail="You do not have permission to access this project")

    return project


# ---------------------------------------------------------------------------
# Project CRUD Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=List[ProjectResponse])
def list_projects(
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    """Retrieve all video projects belonging to the authenticated user."""
    stmt = (
        select(VideoProject)
        .options(selectinload(VideoProject.images))
        .where(VideoProject.user_id == current_user.id)
        .order_by(desc(VideoProject.created_at))
    )
    projects = db.execute(stmt).scalars().all()

    return [
        ProjectResponse(
            id=p.id,
            user_id=p.user_id,
            title=p.title,
            occasion=p.occasion,
            duration_seconds=p.duration_seconds,
            aspect_ratio=p.aspect_ratio,
            music_mood=p.music_mood,
            custom_music_prompt=p.custom_music_prompt,
            description=p.description,
            status=p.status,
            image_count=len(p.images),
            created_at=p.created_at,
            updated_at=p.updated_at,
        )
        for p in projects
    ]


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    data: ProjectCreate,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    """Create a new video generation project."""
    project = VideoProject(
        user_id=current_user.id,
        title=data.title,
        occasion=data.occasion.value,
        duration_seconds=data.duration_seconds.value,
        aspect_ratio=data.aspect_ratio.value,
        music_mood=data.music_mood.value,
        custom_music_prompt=data.custom_music_prompt,
        description=data.description,
        status="DRAFT"
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    return ProjectResponse(
        id=project.id,
        user_id=project.user_id,
        title=project.title,
        occasion=project.occasion,
        duration_seconds=project.duration_seconds,
        aspect_ratio=project.aspect_ratio,
        music_mood=project.music_mood,
        custom_music_prompt=project.custom_music_prompt,
        description=project.description,
        status=project.status,
        image_count=0,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.get("/{project_id}", response_model=ProjectDetailResponse)
def get_project(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    """Get complete details, uploaded images, and latest job status for a video project."""
    project = _get_project_or_404(project_id, current_user, db)

    latest_job = project.jobs[0] if project.jobs else None
    final_video = next((a for a in project.assets if a.asset_type == "FINAL_VIDEO"), None)
    final_video_url = f"/api/v1/assets/{final_video.id}/stream" if final_video else None

    # Sort images by order_index
    sorted_images = sorted(project.images, key=lambda img: img.order_index)

    return ProjectDetailResponse(
        id=project.id,
        user_id=project.user_id,
        title=project.title,
        occasion=project.occasion,
        duration_seconds=project.duration_seconds,
        aspect_ratio=project.aspect_ratio,
        music_mood=project.music_mood,
        custom_music_prompt=project.custom_music_prompt,
        description=project.description,
        status=project.status,
        image_count=len(sorted_images),
        scene_plan=project.scene_plan,
        created_at=project.created_at,
        updated_at=project.updated_at,
        images=[ImageResponse.from_model(img) for img in sorted_images],
        latest_job=JobResponse.model_validate(latest_job) if latest_job else None,
        final_video_url=final_video_url,
    )


@router.put("/{project_id}", response_model=ProjectResponse)
@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: uuid.UUID,
    data: ProjectUpdate,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    """Update settings for an existing draft project."""
    project = _get_project_or_404(project_id, current_user, db)

    if data.title is not None:
        project.title = data.title
    if data.occasion is not None:
        project.occasion = data.occasion.value
    if data.duration_seconds is not None:
        project.duration_seconds = data.duration_seconds.value
    if data.aspect_ratio is not None:
        project.aspect_ratio = data.aspect_ratio.value
    if data.music_mood is not None:
        project.music_mood = data.music_mood.value
    if data.custom_music_prompt is not None:
        project.custom_music_prompt = data.custom_music_prompt
    if data.description is not None:
        project.description = data.description

    db.commit()
    db.refresh(project)

    return ProjectResponse(
        id=project.id,
        user_id=project.user_id,
        title=project.title,
        occasion=project.occasion,
        duration_seconds=project.duration_seconds,
        aspect_ratio=project.aspect_ratio,
        music_mood=project.music_mood,
        custom_music_prompt=project.custom_music_prompt,
        description=project.description,
        status=project.status,
        image_count=len(project.images),
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    """Delete a video project and all associated media files from storage.

    Transaction-safe: only deletes physical storage files after database commit succeeds.
    """
    project = _get_project_or_404(project_id, current_user, db)

    # 1. Collect all storage keys to delete after successful DB deletion
    storage = get_storage_service()
    files_to_delete = [img.file_path for img in project.images if img.file_path]
    for asset in project.assets:
        if asset.file_path:
            files_to_delete.append(asset.file_path)

    # 2. Delete project from database inside transaction
    db.delete(project)
    db.commit()

    # 3. Only delete files from disk after DB commit succeeds!
    for fp in files_to_delete:
        try:
            storage.delete_file(fp)
        except Exception as exc:
            logger.warning(f"Failed to delete storage file {fp} during project deletion: {exc}")

    return None


# ---------------------------------------------------------------------------
# Reference Image Upload Endpoints
# ---------------------------------------------------------------------------

@router.post("/{project_id}/images", response_model=List[ImageResponse], status_code=status.HTTP_201_CREATED)
async def upload_project_images(
    project_id: uuid.UUID,
    files: Optional[List[UploadFile]] = File(None),
    file: Optional[UploadFile] = File(None),
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    """Upload one or more reference images to a video project.

    Enforces:
    - Maximum 5 images per project (strictly enforced across multiple requests)
    - Allowed formats: JPG, JPEG, PNG, WEBP
    - File size <= 25MB enforced on actual streamed bytes
    - Real binary image inspection using Pillow
    - Preserves sequential order
    - Transaction-safe file rollback on failure
    """
    project = _get_project_or_404(project_id, current_user, db)

    # Consolidate files from either 'files' (list) or 'file' (single)
    upload_list: List[UploadFile] = []
    if files:
        upload_list.extend(files)
    elif file:
        upload_list.append(file)

    if not upload_list:
        raise BadRequestError(detail="No image files were provided for upload")

    # Point 1: Check 5-image limit directly against PostgreSQL to prevent race conditions & multi-request overflow
    count_stmt = select(func.count(ProjectImage.id)).where(ProjectImage.project_id == project.id)
    current_count = db.execute(count_stmt).scalar() or 0

    if current_count + len(upload_list) > settings.MAX_IMAGES_PER_PROJECT:
        remaining = max(0, settings.MAX_IMAGES_PER_PROJECT - current_count)
        raise BadRequestError(
            detail=(
                f"Cannot upload {len(upload_list)} image(s). Maximum {settings.MAX_IMAGES_PER_PROJECT} "
                f"images allowed per project (currently {current_count} uploaded, {remaining} slot(s) remaining)."
            )
        )

    storage = get_storage_service()
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    created_images: List[ProjectImage] = []
    saved_storage_keys: List[str] = []

    try:
        for i, upload_file in enumerate(upload_list):
            filename = upload_file.filename or "uploaded_image.jpg"
            ext = os.path.splitext(filename)[1].lower()

            # 1. Extension check
            if ext not in ALLOWED_IMAGE_EXTENSIONS:
                raise BadRequestError(
                    detail=(
                        f"Unsupported file extension '{ext}' for file '{filename}'. "
                        f"Allowed formats: JPG, JPEG, PNG, WEBP."
                    )
                )

            # Point 4: Stream and enforce size limit on actual uploaded bytes
            chunks = []
            actual_bytes = 0
            while True:
                chunk = await upload_file.read(1024 * 1024)  # 1MB chunk
                if not chunk:
                    break
                actual_bytes += len(chunk)
                if actual_bytes > max_bytes:
                    raise BadRequestError(
                        detail=f"File '{filename}' exceeds the maximum allowed size of {settings.MAX_UPLOAD_SIZE_MB}MB"
                    )
                chunks.append(chunk)

            if actual_bytes == 0:
                raise BadRequestError(detail=f"Uploaded file '{filename}' is empty")

            content = b"".join(chunks)

            # 2. Deep binary image validation and dimension extraction with Pillow
            try:
                with Image.open(io.BytesIO(content)) as img:
                    img_format = img.format.upper() if img.format else ""
                    if img_format not in ALLOWED_IMAGE_FORMATS:
                        raise BadRequestError(
                            detail=(
                                f"Unsupported image format '{img_format}' for file '{filename}'. "
                                f"Allowed formats: JPG, JPEG, PNG, WEBP."
                            )
                        )
                    canonical_mime = ALLOWED_IMAGE_FORMATS[img_format]
                    width, height = img.size
            except Exception as e:
                if isinstance(e, BadRequestError):
                    raise
                raise BadRequestError(detail=f"File '{filename}' is not a valid or readable image: {str(e)}")

            # 3. Save file to storage
            storage_key = storage.save_file(
                content=content,
                filename=filename,
                subfolder=f"projects/{project_id}"
            )
            saved_storage_keys.append(storage_key)

            # 4. Create database record
            order_index = current_count + i
            project_image = ProjectImage(
                project_id=project.id,
                file_path=storage_key,
                original_filename=os.path.basename(filename),
                mime_type=canonical_mime,
                file_size=actual_bytes,
                width=width,
                height=height,
                order_index=order_index,
            )
            db.add(project_image)
            created_images.append(project_image)

        # Commit all new records inside the transaction
        db.commit()
        for img in created_images:
            db.refresh(img)

    except Exception:
        # Point 2: Transaction-safe rollback — if anything fails, roll back DB and clean up saved files from disk
        db.rollback()
        for key in saved_storage_keys:
            try:
                storage.delete_file(key)
            except Exception:
                pass
        raise

    return [ImageResponse.from_model(img) for img in created_images]


@router.get("/{project_id}/images", response_model=List[ImageResponse])
def list_project_images(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    """Retrieve all reference images uploaded to a video project, in order."""
    project = _get_project_or_404(project_id, current_user, db)

    stmt = (
        select(ProjectImage)
        .where(ProjectImage.project_id == project.id)
        .order_by(ProjectImage.order_index.asc())
    )
    images = db.execute(stmt).scalars().all()

    return [ImageResponse.from_model(img) for img in images]


@router.put("/{project_id}/images/reorder", response_model=List[ImageResponse])
def reorder_project_images(
    project_id: uuid.UUID,
    data: ImageReorderRequest,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    """Point 5: Update the sequence order of reference images for scene planning.

    Accepts the complete list of image IDs in the desired order and updates order_index.
    """
    project = _get_project_or_404(project_id, current_user, db)

    # Fetch all existing images for this project
    stmt = select(ProjectImage).where(ProjectImage.project_id == project.id)
    existing_images = db.execute(stmt).scalars().all()
    existing_map = {img.id: img for img in existing_images}

    if set(data.image_ids) != set(existing_map.keys()):
        raise BadRequestError(
            detail="image_ids must contain all and only the IDs of images currently in this project"
        )

    # Update order_index according to provided sequence
    for new_idx, img_id in enumerate(data.image_ids):
        existing_map[img_id].order_index = new_idx

    db.commit()

    # Return refreshed and sorted list
    sorted_images = sorted(existing_images, key=lambda x: x.order_index)
    return [ImageResponse.from_model(img) for img in sorted_images]


@router.delete("/{project_id}/images/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project_image(
    project_id: uuid.UUID,
    image_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    """Delete a reference image from a project and remove its file from storage.

    Transaction-safe: only deletes physical storage file after database commit succeeds.
    """
    project = _get_project_or_404(project_id, current_user, db)

    stmt = select(ProjectImage).where(
        ProjectImage.id == image_id,
        ProjectImage.project_id == project.id
    )
    image = db.execute(stmt).scalar_one_or_none()

    if not image:
        raise NotFoundError(detail="Image not found in this project")

    file_to_delete = image.file_path

    # Delete from DB and re-sequence remaining
    db.delete(image)
    db.flush()

    remaining_stmt = (
        select(ProjectImage)
        .where(ProjectImage.project_id == project.id)
        .order_by(ProjectImage.order_index.asc())
    )
    remaining_images = db.execute(remaining_stmt).scalars().all()
    for idx, rem_img in enumerate(remaining_images):
        rem_img.order_index = idx

    db.commit()

    # Physical file cleanup after successful commit
    storage = get_storage_service()
    if file_to_delete:
        try:
            storage.delete_file(file_to_delete)
        except Exception as exc:
            logger.warning(f"Failed to delete storage file {file_to_delete}: {exc}")

    return None


@router.get("/{project_id}/images/{image_id}/file")
def get_project_image_file(
    project_id: uuid.UUID,
    image_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    """Securely stream or preview an uploaded project image file.

    Enforces ownership and safe storage path resolution.
    """
    project = _get_project_or_404(project_id, current_user, db)

    stmt = select(ProjectImage).where(
        ProjectImage.id == image_id,
        ProjectImage.project_id == project.id
    )
    image = db.execute(stmt).scalar_one_or_none()

    if not image:
        raise NotFoundError(detail="Image not found in this project")

    storage = get_storage_service()
    try:
        full_path = storage.get_full_path(image.file_path)
    except PermissionError:
        raise PermissionDeniedError(detail="Invalid file path")

    if not os.path.isfile(full_path):
        raise NotFoundError(detail="Image file not found on disk")

    return FileResponse(
        path=full_path,
        media_type=image.mime_type,
        filename=image.original_filename
    )


# ---------------------------------------------------------------------------
# AI Image Analysis & Scene Planning Endpoints (Phase 4)
# ---------------------------------------------------------------------------

@router.post("/{project_id}/analyze", response_model=Union[ScenePlan, JobResponse], status_code=status.HTTP_200_OK)
async def analyze_project_images(
    project_id: uuid.UUID,
    response: Response,
    background_tasks: BackgroundTasks,
    background: bool = Query(default=False, description="Run planning in background and return job tracker"),
    force: bool = Query(default=False, description="Force re-generation of scene plan bypassing plan reuse"),
    batch_size: Optional[int] = Query(default=None, ge=1, le=3, description="Optional batch size for vision calls (1-3)"),
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
    planner_service: ScenePlanningService = Depends(get_scene_planning_service),
):
    """Analyze uploaded reference photos and generate a structured ScenePlan.
    
    Supports:
    - Persistent checkpoint-based resume for Groq rate limits (HTTP 429).
    - Immediate persistence per image analysis.
    - Synchronous execution returning 200 ScenePlan (default) for seamless compatibility.
    - Asynchronous execution (?background=true) returning 202 JobResponse for background polling.
    - Concurrency protection: returns existing active job if analysis is already running.
    """
    project = _get_project_or_404(project_id, current_user, db)

    if background:
        # Concurrency protection: return active job if already running or waiting
        active_job = db.execute(
            select(GenerationJob)
            .where(
                GenerationJob.project_id == project.id,
                GenerationJob.stage == "ANALYZING_IMAGES",
                GenerationJob.status.in_(["RUNNING", "WAITING_FOR_RATE_LIMIT", "QUEUED"]),
            )
            .order_by(desc(GenerationJob.created_at))
            .limit(1)
        ).scalar_one_or_none()

        if active_job:
            response.status_code = status.HTTP_202_ACCEPTED
            return active_job

        # Create new queued job
        job = GenerationJob(
            project_id=project.id,
            stage="ANALYZING_IMAGES",
            status="QUEUED",
            progress_percent=0,
            external_provider_ids={"force": force, "batch_size": batch_size},
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        background_tasks.add_task(
            planner_service.execute_checkpoint_planning_pipeline,
            project_id=project.id,
            user_id=current_user.id,
            db_session_factory=SessionLocal,
            force_regenerate=force,
            batch_size=batch_size,
            job_id=job.id,
        )
        response.status_code = status.HTTP_202_ACCEPTED
        return job

    return await planner_service.generate_scene_plan(
        project_id=project_id,
        user_id=current_user.id,
        db=db,
        force_regenerate=force,
        batch_size=batch_size,
    )


@router.get("/{project_id}/analysis-checkpoints", response_model=AnalysisProgressResponse, status_code=status.HTTP_200_OK)
def get_project_analysis_checkpoints(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
    planner_service: ScenePlanningService = Depends(get_scene_planning_service),
):
    """Retrieve checkpoint-based analysis progress for project reference photos,
    including per-photo statuses, cooldown countdown, and active job status.
    """
    return planner_service.get_project_analysis_progress(
        project_id=project_id,
        user_id=current_user.id,
        db=db,
    )


@router.get("/{project_id}/scene-plan", response_model=ScenePlan, status_code=status.HTTP_200_OK)
def get_project_scene_plan(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
    planner_service: ScenePlanningService = Depends(get_scene_planning_service),
):
    """Retrieve existing saved ScenePlan for a project."""
    return planner_service.get_project_scene_plan(
        project_id=project_id,
        user_id=current_user.id,
        db=db,
    )


# ---------------------------------------------------------------------------
# Phase 5: Wan 2.2 Video Clip Generation & Job Tracking
# ---------------------------------------------------------------------------

@router.post("/{project_id}/generate-clips", status_code=status.HTTP_202_ACCEPTED, response_model=JobResponse)
async def generate_clips(
    project_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    scene_number: Optional[int] = Query(None, description="Optional scene number to generate/regenerate individually"),
    force_slideshow: bool = Query(False, description="Generate clips using Animated Photo Slideshow fallback"),
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
    generator: VideoGenerationService = Depends(get_video_generation_service),
):
    """Initiates asynchronous generation of scene video clips using Wan 2.2 with automated FFmpeg slideshow fallback.

    Returns HTTP 202 Accepted with a GenerationJob tracker.
    Runs non-blocking in background to prevent connection drops.
    """
    project = _get_project_or_404(project_id, current_user, db)

    if not project.scene_plan or not isinstance(project.scene_plan, dict) or not project.scene_plan.get("scenes"):
        raise BadRequestError(detail="Project does not have an approved ScenePlan. Run scene planning first.")

    total_scenes = len(project.scene_plan.get("scenes", []))

    # Create GenerationJob in database
    job = GenerationJob(
        project_id=project.id,
        stage="GENERATING_CLIPS",
        status="QUEUED",
        progress_percent=0,
        external_provider_ids={
            "current_scene": scene_number,
            "completed_scenes": [],
            "fallback_scenes": [],
            "failed_scenes": [],
            "force_slideshow": force_slideshow,
            "total_scenes": total_scenes,
        },
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Dispatch in-process asynchronous background task
    background_tasks.add_task(
        generator.generate_project_clips,
        project_id=project.id,
        user_id=current_user.id,
        db_session_factory=SessionLocal,
        job_id=job.id,
        force_retry_scene=scene_number,
        force_slideshow=force_slideshow,
    )

    logger.info(
        f"Queued clip generation job {job.id} for project {project.id} "
        f"(scenes={total_scenes}, retry_scene={scene_number}, force_slideshow={force_slideshow})"
    )
    return job


@router.get("/{project_id}/jobs/latest", response_model=JobResponse, status_code=status.HTTP_200_OK)
def get_latest_project_job(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
):
    """Retrieve the latest GenerationJob status and progress for a video project."""
    project = _get_project_or_404(project_id, current_user, db)

    stmt = (
        select(GenerationJob)
        .where(GenerationJob.project_id == project.id)
        .order_by(desc(GenerationJob.created_at))
        .limit(1)
    )
    job = db.execute(stmt).scalar_one_or_none()
    if not job:
        raise NotFoundError(detail="No generation job found for this project")

    return job


@router.get("/{project_id}/clips", response_model=List[ClipAssetResponse], status_code=status.HTTP_200_OK)
def get_project_clips(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
    generator: VideoGenerationService = Depends(get_video_generation_service),
):
    """Retrieve all successfully generated video clips for a project."""
    project = _get_project_or_404(project_id, current_user, db)

    scene_assets = generator.get_existing_scene_assets(project.id, db)
    results = []

    for sc_num in sorted(scene_assets.keys()):
        asset = scene_assets[sc_num]
        meta = asset.meta_data or {}
        results.append(
            ClipAssetResponse(
                id=asset.id,
                project_id=asset.project_id,
                scene_number=sc_num,
                title=meta.get("title", f"Scene {sc_num}"),
                duration_seconds=asset.duration_seconds or 0.0,
                file_size=asset.file_size or 0,
                stream_url=f"/api/v1/assets/{asset.id}/stream",
                resolution=meta.get("resolution"),
                provider=meta.get("provider", "wan_video"),
                fallback_used=bool(meta.get("fallback_used", False) or (meta.get("provider") == "ffmpeg_slideshow")),
                camera_movement=meta.get("camera_movement"),
                original_error=meta.get("original_wan_error"),
                created_at=asset.created_at,
            )
        )

    return results


# ---------------------------------------------------------------------------
# Phase 6: Chatterbox AI Voiceover Generation
# ---------------------------------------------------------------------------

@router.post("/{project_id}/generate-voiceover", status_code=status.HTTP_202_ACCEPTED, response_model=JobResponse)
async def generate_voiceover(
    project_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    force: bool = Query(False, description="Force regeneration even if voiceover exists"),
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
    voiceover_service: VoiceoverService = Depends(get_voiceover_service),
):
    """Initiates asynchronous generation of AI voiceover narration using Chatterbox.

    Returns HTTP 202 Accepted with a GenerationJob tracker.
    Runs non-blocking in background to prevent connection drops.
    """
    project = _get_project_or_404(project_id, current_user, db)

    if not project.scene_plan or not isinstance(project.scene_plan, dict):
        raise BadRequestError(detail="Project does not have an approved ScenePlan. Run scene planning first.")

    script = (project.scene_plan.get("full_narration_script") or "").strip()
    if not script:
        scenes = project.scene_plan.get("scenes", [])
        script = " ".join(sc.get("narration_text", "").strip() for sc in scenes if sc.get("narration_text"))
    if not script:
        raise BadRequestError(detail="Project ScenePlan has no narration text to synthesize.")

    # Create GenerationJob in database
    job = GenerationJob(
        project_id=project.id,
        stage="GENERATING_VOICEOVER",
        status="QUEUED",
        progress_percent=0,
        external_provider_ids={
            "char_count": len(script),
            "force": force,
        },
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Dispatch in-process asynchronous background task
    background_tasks.add_task(
        voiceover_service.generate_project_voiceover,
        project_id=project.id,
        user_id=current_user.id,
        db_session_factory=SessionLocal,
        job_id=job.id,
        force_regenerate=force,
    )

    logger.info(f"Queued voiceover generation job {job.id} for project {project.id} ({len(script)} chars)")
    return job


@router.get("/{project_id}/voiceover", response_model=VoiceoverAssetResponse, status_code=status.HTTP_200_OK)
def get_project_voiceover(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
    voiceover_service: VoiceoverService = Depends(get_voiceover_service),
):
    """Retrieve the generated voiceover audio asset for a project."""
    project = _get_project_or_404(project_id, current_user, db)

    asset = voiceover_service.get_existing_voiceover_asset(project.id, db)
    if not asset:
        raise NotFoundError(detail="No voiceover has been generated for this project yet")

    meta = asset.meta_data or {}
    return VoiceoverAssetResponse(
        id=asset.id,
        project_id=asset.project_id,
        duration_seconds=asset.duration_seconds or 0.0,
        file_size=asset.file_size or 0,
        stream_url=f"/api/v1/assets/{asset.id}/stream",
        sample_rate=meta.get("sample_rate", 24000),
        narration_text=meta.get("narration_text"),
        created_at=asset.created_at,
    )


@router.post("/{project_id}/generate-music", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def generate_project_music(
    project_id: uuid.UUID,
    force: bool = Query(default=False, description="Force re-generation of background music"),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
    music_service: MusicService = Depends(get_music_service),
):
    """Initiate background music workflow for a project.

    Conditional routing:
    - If a predefined library track is selected: uses that track and completes immediately
      without calling ACE-Step AI music generation or triggering unnecessary provider requests.
    - If no predefined track is selected: initiates asynchronous ACE-Step AI generation in background.
    - Concurrency protection: returns HTTP 400 if a music generation job is already running or queued.
    """
    project = _get_project_or_404(project_id, current_user, db)

    # Concurrency guard: Check for active music job already running or queued
    active_stmt = select(GenerationJob).where(
        and_(
            GenerationJob.project_id == project.id,
            GenerationJob.stage == "GENERATING_MUSIC",
            GenerationJob.status.in_(["QUEUED", "RUNNING"]),
        )
    )
    active_job = db.execute(active_stmt).scalars().first()
    if active_job:
        raise BadRequestError(detail="A music generation job is already in progress for this project.")

    # Check if predefined music is selected
    selected_track_id = music_service.has_predefined_music_selected(project, db)
    if selected_track_id and not force:
        logger.info(
            f"Predefined music '{selected_track_id}' selected for project {project.id}. "
            f"Completing music generation with static library track; ACE-Step bypassed."
        )
        job = await music_service.generate_project_music(
            project_id=project.id,
            user_id=current_user.id,
            db_session_factory=lambda: db,
            force_regenerate=False,
        )
        return job

    # Initialize GenerationJob record for AI generation
    job = GenerationJob(
        project_id=project.id,
        stage="GENERATING_MUSIC",
        status="QUEUED",
        progress_percent=0,
        external_provider_ids={
            "mood": project.music_mood or "Cinematic",
            "force": force,
        },
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Dispatch in-process asynchronous background task
    background_tasks.add_task(
        music_service.generate_project_music,
        project_id=project.id,
        user_id=current_user.id,
        db_session_factory=SessionLocal,
        job_id=job.id,
        force_regenerate=force,
    )

    logger.info(f"Queued ACE-Step music generation job {job.id} for project {project.id} (mood: {project.music_mood})")
    return job


@router.get("/{project_id}/music", response_model=MusicAssetResponse, status_code=status.HTTP_200_OK)
def get_project_music(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
    music_service: MusicService = Depends(get_music_service),
):
    """Retrieve the background music asset for a project."""
    project = _get_project_or_404(project_id, current_user, db)

    asset = music_service.get_existing_music_asset(project.id, db)
    if not asset:
        raise NotFoundError(detail="No background music has been generated for this project yet")

    meta = asset.meta_data or {}
    return MusicAssetResponse(
        id=asset.id,
        project_id=asset.project_id,
        asset_type="MUSIC",
        duration_seconds=asset.duration_seconds or 0.0,
        file_size=asset.file_size or 0,
        stream_url=f"/api/v1/assets/{asset.id}/stream",
        music_mood=meta.get("music_mood") or project.music_mood,
        music_prompt=meta.get("music_prompt"),
        provider=meta.get("provider", "ace_step"),
        title=meta.get("title"),
        track_id=meta.get("track_id"),
        attribution=meta.get("attribution"),
        license=meta.get("license"),
        created_at=asset.created_at,
    )


@router.post("/{project_id}/select-music", response_model=Optional[MusicAssetResponse], status_code=status.HTTP_200_OK)
def select_project_music(
    project_id: uuid.UUID,
    data: SelectMusicRequest,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
    music_service: MusicService = Depends(get_music_service),
):
    """Select or clear a Static Music Library track for a video project (synchronous and instant)."""
    project = _get_project_or_404(project_id, current_user, db)

    if not data.track_id:
        music_service.unselect_library_track_for_project(
            project_id=project.id,
            user_id=current_user.id,
            db=db,
        )
        return None

    asset = music_service.select_library_track_for_project(
        project_id=project.id,
        user_id=current_user.id,
        track_id=data.track_id,
        db=db,
    )

    meta = asset.meta_data or {}
    return MusicAssetResponse(
        id=asset.id,
        project_id=asset.project_id,
        asset_type="MUSIC",
        duration_seconds=asset.duration_seconds or 0.0,
        file_size=asset.file_size or 0,
        stream_url=f"/api/v1/assets/{asset.id}/stream",
        music_mood=meta.get("music_mood") or project.music_mood,
        music_prompt=meta.get("music_prompt"),
        provider="static_library",
        title=meta.get("title"),
        track_id=meta.get("track_id"),
        attribution=meta.get("attribution"),
        license=meta.get("license"),
        created_at=asset.created_at,
    )


@router.delete("/{project_id}/music", status_code=status.HTTP_204_NO_CONTENT)
def delete_project_music(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
    music_service: MusicService = Depends(get_music_service),
):
    """Clear/unselect any background music selection for a project."""
    project = _get_project_or_404(project_id, current_user, db)
    music_service.unselect_library_track_for_project(project.id, current_user.id, db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Phase 8: Core FFmpeg Video Composition Endpoints
# ---------------------------------------------------------------------------

@router.post("/{project_id}/compose", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def compose_project_video(
    project_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    force: bool = Query(default=False, description="Force re-composition even if final video exists"),
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
    composer_service: VideoCompositionService = Depends(get_composition_service),
    video_service: VideoGenerationService = Depends(get_video_generation_service),
    voiceover_service: VoiceoverService = Depends(get_voiceover_service),
    music_service: MusicService = Depends(get_music_service),
):
    """Initiates asynchronous final video composition with FFmpeg.

    Validates all required scene clips, voiceover, and background music.
    Returns HTTP 202 Accepted with a GenerationJob tracking composition progress.
    Runs non-blocking in background to prevent connection drops.
    """
    # 1. Fetch project and verify ownership
    project = _get_project_or_404(project_id, current_user, db)

    # 2. Prevent duplicate simultaneous composition jobs for the same project
    active_stmt = (
        select(GenerationJob)
        .where(
            and_(
                GenerationJob.project_id == project.id,
                GenerationJob.stage == "COMPOSING",
                GenerationJob.status.in_(["QUEUED", "RUNNING"]),
            )
        )
    )
    active_job = db.execute(active_stmt).scalars().first()
    if active_job:
        raise BadRequestError(detail="A video composition job is already in progress for this project.")

    # 3. Validate required scene video clips
    scene_assets = video_service.get_existing_scene_assets(project.id, db)
    scene_plan = project.scene_plan or {}
    plan_scenes = scene_plan.get("scenes", [])
    if plan_scenes:
        expected_scenes = [sc.get("scene_number") for sc in plan_scenes if sc.get("scene_number") is not None]
    else:
        expected_scenes = sorted(scene_assets.keys())

    if not expected_scenes or not scene_assets:
        raise BadRequestError(detail="No video clips found for project. Generate video clips first.")

    for sc_num in expected_scenes:
        if sc_num not in scene_assets:
            raise BadRequestError(
                detail=f"Missing video clip for scene {sc_num}. All scenes must be generated before composing final video."
            )

    # 4. Validate voiceover narration audio
    vo_asset = voiceover_service.get_existing_voiceover_asset(project.id, db)
    if not vo_asset:
        raise BadRequestError(detail="Voiceover audio is missing. Generate voiceover before composing final video.")

    # 5. Validate background music audio (ACE-Step or Static Library)
    music_asset = music_service.get_existing_music_asset(project.id, db)
    if not music_asset:
        selected_track_id = music_service.has_predefined_music_selected(project, db)
        if selected_track_id:
            music_asset = music_service.select_library_track_for_project(
                project_id=project.id,
                user_id=current_user.id,
                track_id=selected_track_id,
                db=db,
            )
        else:
            raise BadRequestError(detail="Background music is missing. Select or generate music before composing final video.")

    # 6. Initialize GenerationJob in database
    job = GenerationJob(
        project_id=project.id,
        stage="COMPOSING",
        status="QUEUED",
        progress_percent=0,
        external_provider_ids={
            "scene_count": len(expected_scenes),
            "aspect_ratio": project.aspect_ratio,
            "duration_seconds": project.duration_seconds,
            "force": force,
        },
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # 7. Dispatch asynchronous composition in background
    background_tasks.add_task(
        composer_service.compose_project_video,
        project_id=project.id,
        user_id=current_user.id,
        db_session_factory=SessionLocal,
        job_id=job.id,
        force_regenerate=force,
    )

    logger.info(
        f"Queued video composition job {job.id} for project {project.id} "
        f"(scenes={len(expected_scenes)}, aspect={project.aspect_ratio}, dur={project.duration_seconds}s)"
    )
    return job


@router.get("/{project_id}/final-video", response_model=FinalVideoResponse, status_code=status.HTTP_200_OK)
def get_final_video(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db),
    composer_service: VideoCompositionService = Depends(get_composition_service),
):
    """Retrieve details, streaming URL, and composition status for a project's final composed video."""
    project = _get_project_or_404(project_id, current_user, db)

    # Check for existing final video asset on disk
    asset = composer_service.get_existing_final_video_asset(project.id, db)

    # Check latest composing job to report current status if not completed
    latest_job_stmt = (
        select(GenerationJob)
        .where(
            and_(
                GenerationJob.project_id == project.id,
                GenerationJob.stage == "COMPOSING",
            )
        )
        .order_by(desc(GenerationJob.created_at))
        .limit(1)
    )
    latest_job = db.execute(latest_job_stmt).scalar_one_or_none()

    if asset:
        meta = asset.meta_data or {}
        comp_status = "COMPLETED"
        return FinalVideoResponse(
            id=asset.id,
            project_id=project.id,
            asset_type="FINAL_VIDEO",
            duration_seconds=asset.duration_seconds or 0.0,
            resolution=meta.get("resolution"),
            file_size=asset.file_size or 0,
            video_codec=meta.get("video_codec", "h264"),
            audio_codec=meta.get("audio_codec", "aac"),
            stream_url=f"/api/v1/assets/{asset.id}/stream",
            download_url=f"/api/v1/assets/{asset.id}/download",
            status=comp_status,
            created_at=asset.created_at,
        )

    # Asset does not exist yet; report status from job or default to NOT_STARTED
    comp_status = latest_job.status if latest_job else "NOT_STARTED"
    return FinalVideoResponse(
        id=None,
        project_id=project.id,
        asset_type="FINAL_VIDEO",
        duration_seconds=None,
        resolution=None,
        file_size=None,
        video_codec=None,
        audio_codec=None,
        stream_url=None,
        download_url=None,
        status=comp_status,
        created_at=None,
    )




