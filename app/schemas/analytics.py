import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ClickEventResponse(BaseModel):
    id: uuid.UUID
    clicked_at: datetime
    ip_address: str | None
    user_agent: str | None
    referrer: str | None

    model_config = ConfigDict(from_attributes=True)


class AnalyticsResponse(BaseModel):
    short_url_id: uuid.UUID
    short_code: str
    total_clicks: int
    events: list[ClickEventResponse]
    page: int
    page_size: int
    total_events: int
