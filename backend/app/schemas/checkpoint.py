import uuid
from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class ImageAnalysisCheckpointResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    image_id: uuid.UUID
    order_index: int
    status: str  # PENDING, IN_PROGRESS, COMPLETED, FAILED, WAITING_FOR_RATE_LIMIT
    analysis_result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    retry_count: int
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class AnalysisProgressResponse(BaseModel):
    project_id: uuid.UUID
    total_images: int
    completed_count: int
    pending_count: int
    active_image_id: Optional[uuid.UUID] = None
    active_order_index: Optional[int] = None
    job_id: Optional[uuid.UUID] = None
    job_status: Optional[str] = None  # QUEUED, RUNNING, WAITING_FOR_RATE_LIMIT, COMPLETED, FAILED
    rate_limit_reset_at: Optional[datetime] = None
    rate_limit_type: Optional[str] = None
    cooldown_seconds_remaining: Optional[float] = None
    auto_resume: bool = True
    checkpoints: List[ImageAnalysisCheckpointResponse] = []
