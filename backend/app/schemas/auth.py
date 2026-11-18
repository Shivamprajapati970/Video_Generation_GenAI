import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict, field_validator


class UserBase(BaseModel):
    email: str = Field(..., min_length=3, max_length=255, description="Valid email address")
    full_name: Optional[str] = Field(None, max_length=255)

    @field_validator("email")
    @classmethod
    def validate_email_format(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Invalid email format")
        return v


class UserRegister(UserBase):
    password: str = Field(..., min_length=8, max_length=128, description="Password (minimum 8 characters)")


class UserLogin(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=1)

    @field_validator("email")
    @classmethod
    def validate_email_format(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v:
            raise ValueError("Invalid email format")
        return v


class UserResponse(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    is_active: bool
    created_at: datetime


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user: UserResponse
    expires_at: datetime
    message: str = "Authenticated successfully"
