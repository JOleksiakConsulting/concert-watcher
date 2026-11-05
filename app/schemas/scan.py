import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SourceName = Literal[
    "ticketmaster",
    "knockout",
    "fource",
    "ebilet",
    "eventim",
    "going",
    "livenation",
]
COUNTRY_CODE_RE = re.compile(r"^[A-Za-z]{2}$")


class ScanRequest(BaseModel):
    country_codes: list[str] | None = None
    sources: list[SourceName] | None = None
    force: bool = False

    @field_validator("country_codes")
    @classmethod
    def validate_country_codes(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        if not values:
            raise ValueError("country_codes cannot be empty")

        countries: list[str] = []
        for value in values:
            country = value.strip().upper()
            if not COUNTRY_CODE_RE.fullmatch(country):
                raise ValueError("country_codes must contain ISO 3166-1 alpha-2 codes")
            if country not in countries:
                countries.append(country)
        return countries

    @field_validator("sources")
    @classmethod
    def validate_sources(cls, values: list[SourceName] | None) -> list[SourceName] | None:
        if values == []:
            raise ValueError("sources cannot be empty")
        return list(dict.fromkeys(values)) if values is not None else None


class SourceScanResult(BaseModel):
    status: Literal["ok", "degraded", "error", "skipped"]
    events_found: int = 0
    error: str | None = None


class ScanResponse(BaseModel):
    scan_id: str
    started_at: datetime
    finished_at: datetime
    sources: dict[str, SourceScanResult] = Field(default_factory=dict)
    matched_events: int = 0
    new_events: int = 0
    notifications_created: int = 0
