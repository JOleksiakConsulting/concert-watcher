from pydantic import BaseModel, Field


class PendingNotification(BaseModel):
    id: int
    type: str
    artist: str
    date: str | None = None
    time: str | None = None
    city: str | None = None
    venue: str | None = None
    country_code: str
    sources: list[dict[str, str]] = Field(default_factory=list)


class NotificationFailRequest(BaseModel):
    error: str
