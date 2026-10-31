import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

ALIAS_PATTERN = r"^[A-Za-z0-9_-]+$"


def validate_future(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("expiration time must include a timezone")
    if value <= datetime.now(UTC):
        raise ValueError("expiration time must be in the future")
    return value


class ShortURLCreate(BaseModel):
    original_url: HttpUrl
    custom_alias: str | None = Field(
        default=None, min_length=3, max_length=32, pattern=ALIAS_PATTERN
    )
    expires_at: datetime | None = None

    _future_expiration = field_validator("expires_at")(validate_future)


class ShortURLUpdate(BaseModel):
    original_url: HttpUrl | None = None
    is_active: bool | None = None
    expires_at: datetime | None = None

    _future_expiration = field_validator("expires_at")(validate_future)

    @model_validator(mode="after")
    def require_a_change(self) -> "ShortURLUpdate":
        if not self.model_fields_set:
            raise ValueError("at least one field must be supplied")
        if "original_url" in self.model_fields_set and self.original_url is None:
            raise ValueError("original_url cannot be null")
        if "is_active" in self.model_fields_set and self.is_active is None:
            raise ValueError("is_active cannot be null")
        return self


class ShortURLResponse(BaseModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    short_code: str
    short_url: str
    original_url: str
    is_active: bool
    expires_at: datetime | None
    click_count: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ShortURLListResponse(BaseModel):
    items: list[ShortURLResponse]
    page: int
    page_size: int
    total: int
