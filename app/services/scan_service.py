import asyncio
import time
import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models import ScanRun, ScanSourceRun, WatchedArtist
from app.schemas.events import RawEvent
from app.schemas.scan import ScanRequest, ScanResponse, SourceScanResult
from app.services.event_pipeline import process_events
from app.sources.base import SourceAdapter, SourceDegradedError, WatchedArtistRef
from app.sources.registry import build_adapters

_scan_lock = asyncio.Lock()


class ScanInProgressError(Exception):
    pass


def _safe_adapter_error(exc: Exception) -> str:
    """Return an operationally useful error without request URLs or credentials."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code} returned by source"
    if isinstance(exc, httpx.TimeoutException):
        return "Source request timed out"
    if isinstance(exc, httpx.RequestError):
        return f"Source request failed ({type(exc).__name__})"
    if isinstance(exc, SourceDegradedError):
        return str(exc)
    return f"Source adapter failed ({type(exc).__name__})"


def _artist_to_ref(artist: WatchedArtist) -> WatchedArtistRef:
    return WatchedArtistRef(
        id=artist.id,
        canonical_name=artist.canonical_name,
        normalized_name=artist.normalized_name,
        aliases=[a.alias for a in artist.aliases],
        normalized_aliases=[a.normalized_alias for a in artist.aliases],
        countries=[c.country_code for c in artist.countries],
        match_mode=artist.match_mode,
    )


async def _load_watched_artists(session: AsyncSession) -> list[WatchedArtistRef]:
    result = await session.execute(
        select(WatchedArtist)
        .where(WatchedArtist.enabled.is_(True))
        .options(
            selectinload(WatchedArtist.aliases),
            selectinload(WatchedArtist.countries),
        )
    )
    return [_artist_to_ref(a) for a in result.scalars().all()]


async def _run_adapter(
    adapter: SourceAdapter,
    country_codes: list[str],
    watched_artists: list[WatchedArtistRef],
) -> tuple[SourceScanResult, int, int, list[RawEvent]]:
    start = time.perf_counter()
    events: list[RawEvent] = []

    adapter.last_http_requests = 0

    try:
        for country in country_codes:
            batch = await adapter.fetch_events(country, watched_artists)
            events.extend(batch)
        duration_ms = int((time.perf_counter() - start) * 1000)
        http_requests = adapter.last_http_requests
        return (
            SourceScanResult(status="ok", events_found=len(events)),
            duration_ms,
            http_requests,
            events,
        )
    except SourceDegradedError as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        return (
            SourceScanResult(
                status="degraded",
                events_found=len(events),
                error=_safe_adapter_error(exc),
            ),
            duration_ms,
            adapter.last_http_requests,
            events,
        )
    except Exception as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        return (
            SourceScanResult(
                status="error",
                events_found=len(events),
                error=_safe_adapter_error(exc),
            ),
            duration_ms,
            adapter.last_http_requests,
            events,
        )


async def run_scan(
    session: AsyncSession,
    request: ScanRequest,
    adapters: dict[str, SourceAdapter] | None = None,
) -> ScanResponse:
    if _scan_lock.locked():
        raise ScanInProgressError()

    async with _scan_lock:
        scan_id = str(uuid.uuid4())
        started_at = datetime.now(UTC)
        country_codes = request.country_codes or settings.default_country_list
        country_codes = list(dict.fromkeys(c.strip().upper() for c in country_codes if c.strip()))

        scan_run = ScanRun(
            id=scan_id,
            started_at=started_at,
            status="running",
            requested_countries=",".join(country_codes),
        )
        session.add(scan_run)
        await session.commit()

        try:
            watched_artists = await _load_watched_artists(session)
            all_adapters = adapters if adapters is not None else build_adapters()

            if request.sources:
                selected = {k: v for k, v in all_adapters.items() if k in request.sources}
            else:
                selected = all_adapters

            source_results: dict[str, SourceScanResult] = {}
            all_raw_events: list[RawEvent] = []

            for name, adapter in selected.items():
                if hasattr(adapter, "enabled") and not adapter.enabled:
                    source_results[name] = SourceScanResult(status="skipped", events_found=0)
                    session.add(
                        ScanSourceRun(
                            scan_id=scan_id,
                            source=name,
                            status="skipped",
                            events_found=0,
                            duration_ms=0,
                            http_requests=0,
                        )
                    )
                    continue

                result, duration_ms, http_requests, events = await _run_adapter(adapter, country_codes, watched_artists)
                all_raw_events.extend(events)
                source_results[name] = result
                session.add(
                    ScanSourceRun(
                        scan_id=scan_id,
                        source=name,
                        status=result.status,
                        events_found=result.events_found,
                        duration_ms=duration_ms,
                        http_requests=http_requests,
                        error_message=result.error,
                    )
                )

            pipeline_result = await process_events(session, all_raw_events, watched_artists)

            await session.refresh(scan_run)
            finished_at = datetime.now(UTC)
            scan_run.finished_at = finished_at
            scan_run.status = "completed"
            await session.commit()
        except Exception:
            await session.rollback()
            failed_run = await session.get(ScanRun, scan_id)
            if failed_run is not None:
                failed_run.finished_at = datetime.now(UTC)
                failed_run.status = "failed"
                await session.commit()
            raise

        return ScanResponse(
            scan_id=scan_id,
            started_at=started_at,
            finished_at=finished_at,
            sources=source_results,
            matched_events=pipeline_result.matched_events,
            new_events=pipeline_result.new_events,
            notifications_created=pipeline_result.notifications_created,
        )
