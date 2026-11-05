import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deduplication.dedupe import venues_compatible
from app.matching.matcher import ArtistMatch, match_event_to_artists
from app.matching.normalization import (
    content_hash,
    is_past_event,
    normalize_city,
    normalize_venue,
)
from app.models import (
    CanonicalEvent,
    CanonicalEventSource,
    EventArtistMatch,
    NotificationOutbox,
    SourceEvent,
    WatchedArtist,
)
from app.schemas.events import EventStatus, RawEvent


@dataclass
class ProcessResult:
    matched_events: int = 0
    new_events: int = 0
    notifications_created: int = 0


@dataclass(frozen=True)
class NotificationIntent:
    canonical: CanonicalEvent
    notification_type: str
    notification_key: str
    artist_name: str


def _source_event_id(raw: RawEvent) -> str:
    if raw.source_event_id:
        return raw.source_event_id

    parsed_url = urlsplit(raw.source_url.strip())
    normalized_url = urlunsplit(
        (
            parsed_url.scheme.lower(),
            parsed_url.netloc.lower(),
            parsed_url.path.rstrip("/"),
            "",
            "",
        )
    )
    return sha256(f"{raw.source}:{normalized_url}".encode()).hexdigest()[:32]


def _payload_for_notification(
    *,
    notification_type: str,
    artist_name: str,
    canonical: CanonicalEvent,
    sources: list[dict[str, str]],
) -> dict:
    return {
        "type": notification_type,
        "artist": artist_name,
        "date": canonical.local_date.isoformat() if canonical.local_date else None,
        "time": canonical.local_time.isoformat() if canonical.local_time else None,
        "city": canonical.city,
        "venue": canonical.venue_name,
        "country_code": canonical.country_code,
        "status": canonical.status,
        "sources": sources,
    }


async def _get_artist_name(session: AsyncSession, artist_id: int) -> str:
    result = await session.execute(select(WatchedArtist.canonical_name).where(WatchedArtist.id == artist_id))
    name = result.scalar_one_or_none()
    return name or "Unknown"


async def _find_canonical(
    session: AsyncSession,
    *,
    artist_id: int,
    country_code: str,
    local_date,
    normalized_city: str | None,
    venue_name: str | None,
    normalized_venue: str | None,
) -> CanonicalEvent | None:
    if local_date is None or normalized_city is None:
        return None

    result = await session.execute(
        select(CanonicalEvent).where(
            CanonicalEvent.artist_id == artist_id,
            CanonicalEvent.country_code == country_code,
            CanonicalEvent.local_date == local_date,
            CanonicalEvent.normalized_city == normalized_city,
        )
    )
    for candidate in result.scalars():
        if venues_compatible(
            candidate.venue_name,
            candidate.normalized_venue,
            venue_name,
            normalized_venue,
        ):
            return candidate
    return None


async def _find_linked_canonical(
    session: AsyncSession, *, source_event_id: int, artist_id: int
) -> CanonicalEvent | None:
    result = await session.execute(
        select(CanonicalEvent)
        .join(
            CanonicalEventSource,
            CanonicalEventSource.canonical_event_id == CanonicalEvent.id,
        )
        .where(
            CanonicalEventSource.source_event_id == source_event_id,
            CanonicalEvent.artist_id == artist_id,
        )
        .order_by(CanonicalEvent.id)
    )
    return result.scalars().first()


async def _refresh_canonical_status(session: AsyncSession, canonical: CanonicalEvent) -> None:
    result = await session.execute(
        select(SourceEvent.status)
        .join(
            CanonicalEventSource,
            CanonicalEventSource.source_event_id == SourceEvent.id,
        )
        .where(CanonicalEventSource.canonical_event_id == canonical.id)
    )
    statuses = list(result.scalars())
    if statuses:
        canonical.status = max(statuses, key=_status_priority)


def _update_canonical_details(
    canonical: CanonicalEvent,
    raw: RawEvent,
    normalized_city: str | None,
    normalized_venue: str | None,
) -> None:
    if raw.local_date is not None:
        canonical.local_date = raw.local_date
    if raw.local_time is not None:
        canonical.local_time = raw.local_time
    if raw.city and normalized_city:
        canonical.city = raw.city
        canonical.normalized_city = normalized_city
    if raw.venue_name and normalized_venue:
        canonical.venue_name = raw.venue_name
        canonical.normalized_venue = normalized_venue
    canonical.country_code = raw.country_code.upper()


async def _create_notification(
    session: AsyncSession,
    *,
    canonical: CanonicalEvent,
    notification_type: str,
    notification_key: str,
    artist_name: str,
    sources: list[dict[str, str]],
) -> bool:
    existing = await session.execute(
        select(NotificationOutbox.id).where(NotificationOutbox.notification_key == notification_key)
    )
    if existing.scalar_one_or_none() is not None:
        return False

    session.add(
        NotificationOutbox(
            canonical_event_id=canonical.id,
            notification_type=notification_type,
            notification_key=notification_key,
            payload_json=json.dumps(
                _payload_for_notification(
                    notification_type=notification_type,
                    artist_name=artist_name,
                    canonical=canonical,
                    sources=sources,
                )
            ),
            status="pending",
            attempts=0,
        )
    )
    return True


async def _source_links(session: AsyncSession, canonical_id: int) -> list[dict[str, str]]:
    result = await session.execute(
        select(SourceEvent.source, SourceEvent.source_url)
        .join(CanonicalEventSource, CanonicalEventSource.source_event_id == SourceEvent.id)
        .where(CanonicalEventSource.canonical_event_id == canonical_id)
    )
    return [{"name": row.source, "url": row.source_url} for row in result.all()]


async def _refresh_pending_source_links(
    session: AsyncSession,
    canonicals: dict[int, CanonicalEvent],
) -> None:
    """Add newly linked sources without changing the notification's event snapshot."""
    for canonical in canonicals.values():
        pending_result = await session.execute(
            select(NotificationOutbox).where(
                NotificationOutbox.canonical_event_id == canonical.id,
                NotificationOutbox.status == "pending",
            )
        )
        pending = list(pending_result.scalars())
        if not pending:
            continue

        sources = await _source_links(session, canonical.id)
        for notification in pending:
            payload = json.loads(notification.payload_json)
            payload["sources"] = sources
            notification.payload_json = json.dumps(payload)


async def process_events(
    session: AsyncSession,
    raw_events: list[RawEvent],
    artists: list,
) -> ProcessResult:
    result = ProcessResult()
    now = datetime.now(UTC)
    notification_intents: list[NotificationIntent] = []
    touched_canonicals: dict[int, CanonicalEvent] = {}

    for raw in raw_events:
        if is_past_event(raw.local_date):
            continue

        event_key = _source_event_id(raw)
        norm_city = normalize_city(raw.city)
        norm_venue = normalize_venue(raw.venue_name)
        payload = raw.model_dump(mode="json")
        event_hash = content_hash({key: value for key, value in payload.items() if key != "discovered_at"})

        existing = await session.execute(
            select(SourceEvent).where(
                SourceEvent.source == raw.source,
                SourceEvent.source_event_id == event_key,
            )
        )
        source_event = existing.scalar_one_or_none()
        old_date = None
        old_venue: str | None = None

        if source_event is None:
            source_event = SourceEvent(
                source=raw.source,
                source_event_id=event_key,
                source_url=raw.source_url,
                event_name=raw.event_name,
                local_date=raw.local_date,
                local_time=raw.local_time,
                venue_name=raw.venue_name,
                normalized_venue=norm_venue,
                city=raw.city,
                normalized_city=norm_city,
                country_code=raw.country_code.upper(),
                timezone=raw.timezone,
                status=raw.status.value,
                first_seen_at=now,
                last_seen_at=now,
                raw_payload_json=json.dumps(payload),
                content_hash=event_hash,
            )
            session.add(source_event)
            await session.flush()
            is_new_source = True
        else:
            old_date = source_event.local_date
            old_venue = source_event.normalized_venue
            source_event.event_name = raw.event_name
            if raw.source_url:
                source_event.source_url = raw.source_url
            source_event.local_date = raw.local_date
            source_event.local_time = raw.local_time
            source_event.venue_name = raw.venue_name
            source_event.normalized_venue = norm_venue
            source_event.city = raw.city
            source_event.normalized_city = norm_city
            source_event.country_code = raw.country_code.upper()
            source_event.timezone = raw.timezone
            source_event.status = raw.status.value
            source_event.last_seen_at = now
            source_event.raw_payload_json = json.dumps(payload)
            source_event.content_hash = event_hash
            is_new_source = False

        matches = match_event_to_artists(raw, artists)
        if not matches:
            continue

        result.matched_events += len(matches)

        for match in matches:
            await _upsert_match(session, source_event.id, match, now)
            canonical, is_new_canonical, old_canonical_status = await _link_canonical(
                session, source_event, raw, match, norm_city, norm_venue, now
            )
            if canonical is None:
                continue

            touched_canonicals[canonical.id] = canonical
            artist_name = await _get_artist_name(session, match.artist_id)

            if is_new_canonical:
                notification_intents.append(
                    NotificationIntent(
                        canonical=canonical,
                        notification_type="NEW_EVENT",
                        notification_key=f"NEW_EVENT:{canonical.id}",
                        artist_name=artist_name,
                    )
                )
            else:
                notification_intents.extend(
                    _detect_changes(
                        canonical,
                        source_event,
                        old_canonical_status,
                        old_date,
                        old_venue,
                        artist_name,
                        include_detail_changes=not is_new_source,
                    )
                )

    for intent in notification_intents:
        sources = await _source_links(session, intent.canonical.id)
        created = await _create_notification(
            session,
            canonical=intent.canonical,
            notification_type=intent.notification_type,
            notification_key=intent.notification_key,
            artist_name=intent.artist_name,
            sources=sources,
        )
        if created:
            result.notifications_created += 1
            if intent.notification_type == "NEW_EVENT":
                result.new_events += 1

    await _refresh_pending_source_links(session, touched_canonicals)
    await session.commit()
    return result


async def _upsert_match(session: AsyncSession, source_event_id: int, match: ArtistMatch, now: datetime) -> None:
    existing = await session.execute(
        select(EventArtistMatch).where(
            EventArtistMatch.source_event_id == source_event_id,
            EventArtistMatch.artist_id == match.artist_id,
        )
    )
    row = existing.scalar_one_or_none()
    if row is None:
        session.add(
            EventArtistMatch(
                source_event_id=source_event_id,
                artist_id=match.artist_id,
                confidence=match.confidence,
                match_method=match.match_method,
                created_at=now,
            )
        )
    else:
        row.confidence = match.confidence
        row.match_method = match.match_method


async def _link_canonical(
    session: AsyncSession,
    source_event: SourceEvent,
    raw: RawEvent,
    match: ArtistMatch,
    norm_city: str | None,
    norm_venue: str | None,
    now: datetime,
) -> tuple[CanonicalEvent | None, bool, str | None]:
    canonical = await _find_linked_canonical(session, source_event_id=source_event.id, artist_id=match.artist_id)
    if canonical is None:
        canonical = await _find_canonical(
            session,
            artist_id=match.artist_id,
            country_code=raw.country_code.upper(),
            local_date=raw.local_date,
            normalized_city=norm_city,
            venue_name=raw.venue_name,
            normalized_venue=norm_venue,
        )

    is_new = canonical is None
    old_status = canonical.status if canonical is not None else None
    if canonical is None:
        if raw.local_date is None or norm_city is None:
            return None, False, None
        canonical = CanonicalEvent(
            artist_id=match.artist_id,
            local_date=raw.local_date,
            local_time=raw.local_time,
            city=raw.city,
            normalized_city=norm_city,
            venue_name=raw.venue_name,
            normalized_venue=norm_venue,
            country_code=raw.country_code.upper(),
            status=raw.status.value,
            created_at=now,
            updated_at=now,
        )
        session.add(canonical)
        await session.flush()

    link = await session.execute(
        select(CanonicalEventSource).where(
            CanonicalEventSource.canonical_event_id == canonical.id,
            CanonicalEventSource.source_event_id == source_event.id,
        )
    )
    if link.scalar_one_or_none() is None:
        session.add(
            CanonicalEventSource(
                canonical_event_id=canonical.id,
                source_event_id=source_event.id,
            )
        )

    _update_canonical_details(canonical, raw, norm_city, norm_venue)
    canonical.updated_at = now
    await _refresh_canonical_status(session, canonical)

    return canonical, is_new, old_status


def _status_priority(status: str) -> int:
    order = {
        EventStatus.cancelled.value: 5,
        EventStatus.postponed.value: 4,
        EventStatus.rescheduled.value: 4,
        EventStatus.soldout.value: 3,
        EventStatus.onsale.value: 2,
        EventStatus.scheduled.value: 1,
        EventStatus.unknown.value: 0,
    }
    return order.get(status, 0)


def _detect_changes(
    canonical: CanonicalEvent,
    source_event: SourceEvent,
    old_canonical_status: str | None,
    old_date,
    old_venue: str | None,
    artist_name: str,
    *,
    include_detail_changes: bool,
) -> list[NotificationIntent]:
    notifications: list[tuple[str, str]] = []

    if old_canonical_status != canonical.status:
        if canonical.status == EventStatus.cancelled.value:
            key = f"CANCELLED:{canonical.id}:{canonical.status}"
            notifications.append(("CANCELLED", key))
        elif canonical.status == EventStatus.postponed.value:
            notifications.append(("POSTPONED", f"POSTPONED:{canonical.id}:{canonical.status}"))
        elif canonical.status == EventStatus.rescheduled.value:
            notifications.append(("RESCHEDULED", f"RESCHEDULED:{canonical.id}:{canonical.status}"))

    if include_detail_changes and old_date != source_event.local_date and source_event.local_date is not None:
        notifications.append(("DATE_CHANGED", f"DATE_CHANGED:{canonical.id}:{source_event.local_date}"))

    if include_detail_changes and old_venue != source_event.normalized_venue and source_event.normalized_venue:
        notifications.append(("VENUE_CHANGED", f"VENUE_CHANGED:{canonical.id}:{source_event.normalized_venue}"))

    return [
        NotificationIntent(
            canonical=canonical,
            notification_type=notification_type,
            notification_key=key,
            artist_name=artist_name,
        )
        for notification_type, key in notifications
    ]
