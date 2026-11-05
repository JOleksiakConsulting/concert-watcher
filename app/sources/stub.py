"""Explicitly unsupported adapters that are replaced in later milestones."""

from app.schemas.events import RawEvent, SourceHealth
from app.sources.base import SourceAdapter, WatchedArtistRef


class StubSourceAdapter(SourceAdapter):
    def __init__(self, name: str, *, enabled: bool = True) -> None:
        self.name = name
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def fetch_events(
        self,
        country_code: str,
        watched_artists: list[WatchedArtistRef],
    ) -> list[RawEvent]:
        del country_code, watched_artists
        raise NotImplementedError(f"{self.name} adapter not implemented")

    async def healthcheck(self) -> SourceHealth:
        if not self._enabled:
            return SourceHealth(source=self.name, healthy=False, message="disabled")
        return SourceHealth(source=self.name, healthy=False, message="adapter not implemented")
