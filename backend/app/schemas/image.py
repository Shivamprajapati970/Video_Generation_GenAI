import uuid
from datetime import datetime
from typing import Optional, Any, List
from pydantic import BaseModel, ConfigDict, Field


class ImageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    original_filename: str
    url: str
    mime_type: str
    file_size: int
    width: Optional[int] = None
    height: Optional[int] = None
    order_index: int
    created_at: datetime

    @classmethod
    def from_model(cls, img: Any) -> "ImageResponse":
        return cls(
            id=img.id,
            project_id=img.project_id,
            original_filename=img.original_filename,
            url=f"/api/v1/projects/{img.project_id}/images/{img.id}/file",
            mime_type=img.mime_type,
            file_size=img.file_size,
            width=img.width,
            height=img.height,
            order_index=img.order_index,
            created_at=img.created_at,
        )


class ImageReorderRequest(BaseModel):
    """Payload to update the ordering of reference images for scene synthesis."""
    image_ids: List[uuid.UUID] = Field(
        ...,
        min_length=1,
        max_length=5,
        description="Complete list of existing image IDs in the desired sequence"
    )
