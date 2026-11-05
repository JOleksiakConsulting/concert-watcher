from abc import ABC, abstractmethod

from app.schemas.events import RawEvent, SourceHealth


class WatchedArtistRef:
    """Lightweight artist reference passed to source adapters."""

    def __init__(
        self,
        *,
        id: int,
        canonical_name: str,
        normalized_name: str,
        aliases: list[str],
        normalized_aliases: list[str],
        countries: list[str],
        match_mode: str,
    ) -> None:
        self.id = id
        self.canonical_name = canonical_name
        self.normalized_name = normalized_name
        self.aliases = aliases
        self.normalized_aliases = normalized_aliases
        self.countries = countries
        self.match_mode = match_mode


class SourceAdapter(ABC):
    name: str

    @abstractmethod
    async def fetch_events(
        self,
        country_code: str,
        watched_artists: list[WatchedArtistRef],
    ) -> list[RawEvent]: ...

    async def healthcheck(self) -> SourceHealth:
        return SourceHealth(source=self.name, healthy=True)
