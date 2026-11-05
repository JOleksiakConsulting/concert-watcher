from dataclasses import dataclass

from rapidfuzz import fuzz

from app.matching.normalization import strict_normalize
from app.schemas.events import EventParticipant, RawEvent
from app.sources.base import WatchedArtistRef

# Long enough to pass MIN_FUZZY_NAME_LENGTH but too generic to fuzzy-match.
GENERIC_ARTIST_NAMES = frozenset({"health"})
FUZZY_THRESHOLD = 92
MIN_FUZZY_NAME_LENGTH = 6


@dataclass(frozen=True)
class ArtistMatch:
    artist_id: int
    confidence: float
    match_method: str


def _excluded_from_fuzzy(normalized: str) -> bool:
    return len(normalized) < MIN_FUZZY_NAME_LENGTH or normalized in GENERIC_ARTIST_NAMES


def _exact_canonical(participant_norm: str, artist: WatchedArtistRef) -> bool:
    return participant_norm == artist.normalized_name


def _exact_alias(participant_norm: str, artist: WatchedArtistRef) -> bool:
    return participant_norm in artist.normalized_aliases


def _fuzzy_match(participant_norm: str, artist: WatchedArtistRef) -> tuple[bool, float]:
    if _excluded_from_fuzzy(artist.normalized_name):
        return False, 0.0
    if len(participant_norm) < MIN_FUZZY_NAME_LENGTH:
        return False, 0.0

    names = [artist.normalized_name, *artist.normalized_aliases]
    best = max(fuzz.ratio(participant_norm, name) for name in names)
    if best >= FUZZY_THRESHOLD:
        return True, best / 100.0
    return False, 0.0


def _match_participant(participant: EventParticipant, artist: WatchedArtistRef) -> ArtistMatch | None:
    participant_norm = strict_normalize(participant.name)
    role = participant.role

    if role == "attraction" and (_exact_canonical(participant_norm, artist) or _exact_alias(participant_norm, artist)):
        return ArtistMatch(artist.id, 1.0, "structured_exact")
    if _exact_alias(participant_norm, artist):
        return ArtistMatch(artist.id, 0.99, "alias_exact")
    if role in {"headliner", "support", "lineup", "unknown"} and _exact_canonical(participant_norm, artist):
        return ArtistMatch(artist.id, 0.98, "parsed_exact")
    if artist.match_mode == "strict":
        return None
    if role in {"headliner", "support", "lineup", "attraction", "unknown"}:
        ok, score = _fuzzy_match(participant_norm, artist)
        if ok:
            return ArtistMatch(artist.id, score, "fuzzy")
    return None


def match_event_to_artists(event: RawEvent, artists: list[WatchedArtistRef]) -> list[ArtistMatch]:
    results: dict[int, ArtistMatch] = {}

    for artist in artists:
        if event.country_code.upper() not in {c.upper() for c in artist.countries}:
            continue

        for participant in event.participants:
            match = _match_participant(participant, artist)
            if match is None:
                continue
            existing = results.get(match.artist_id)
            if existing is None or match.confidence > existing.confidence:
                results[match.artist_id] = match

    return list(results.values())
