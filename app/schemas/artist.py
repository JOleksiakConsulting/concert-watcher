import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

MatchMode = Literal["strict", "normal"]
COUNTRY_CODE_RE = re.compile(r"^[A-Za-z]{2}$")


def _clean_name(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("canonical_name cannot be blank")
    if len(value) > 512:
        raise ValueError("canonical_name cannot exceed 512 characters")
    return value


def _clean_aliases(values: list[str]) -> list[str]:
    aliases: list[str] = []
    seen: set[str] = set()
    for value in values:
        alias = value.strip()
        if not alias:
            raise ValueError("aliases cannot contain blank values")
        if len(alias) > 512:
            raise ValueError("alias cannot exceed 512 characters")
        key = alias.casefold()
        if key not in seen:
            seen.add(key)
            aliases.append(alias)
    return aliases


def _clean_countries(values: list[str]) -> list[str]:
    countries: list[str] = []
    seen: set[str] = set()
    for value in values:
        country = value.strip().upper()
        if not COUNTRY_CODE_RE.fullmatch(country):
            raise ValueError("countries must contain ISO 3166-1 alpha-2 codes")
        if country not in seen:
            seen.add(country)
            countries.append(country)
    return countries


class ArtistCreate(BaseModel):
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    countries: list[str] = Field(min_length=1)
    match_mode: MatchMode = "normal"

    @field_validator("canonical_name")
    @classmethod
    def validate_canonical_name(cls, value: str) -> str:
        return _clean_name(value)

    @field_validator("aliases")
    @classmethod
    def validate_aliases(cls, values: list[str]) -> list[str]:
        return _clean_aliases(values)

    @field_validator("countries")
    @classmethod
    def validate_countries(cls, values: list[str]) -> list[str]:
        return _clean_countries(values)


class ArtistUpdate(BaseModel):
    canonical_name: str | None = None
    aliases: list[str] | None = None
    countries: list[str] | None = None
    match_mode: MatchMode | None = None
    enabled: bool | None = None

    @field_validator("canonical_name")
    @classmethod
    def validate_canonical_name(cls, value: str | None) -> str | None:
        return _clean_name(value) if value is not None else None

    @field_validator("aliases")
    @classmethod
    def validate_aliases(cls, values: list[str] | None) -> list[str] | None:
        return _clean_aliases(values) if values is not None else None

    @field_validator("countries")
    @classmethod
    def validate_countries(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        if not values:
            raise ValueError("countries cannot be empty")
        return _clean_countries(values)


class ArtistResponse(BaseModel):
    id: int
    canonical_name: str
    aliases: list[str]
    countries: list[str]
    match_mode: MatchMode
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
