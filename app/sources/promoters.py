"""Parsers based on public HTML captured on 2026-09-06 (see fixtures)."""

import re
from datetime import datetime, time
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup, Tag

from app.core.config import settings
from app.matching.matcher import match_event_to_artists
from app.matching.normalization import strict_normalize
from app.schemas.events import EventParticipant, EventStatus, RawEvent, SourceHealth
from app.sources.base import SourceAdapter, SourceDegradedError, WatchedArtistRef
from app.sources.crawler import PublicCrawler

KNOCKOUT_URL = "https://knockoutprod.net/public/index.php/pl/concert"
FOURCE_URL = "https://fource.pl/pl/events/"

# Never infer country solely from a promoter's domain. Unknown cities are unresolved.
PL_CITIES = {
    strict_normalize(city)
    for city in (
        "Warszawa",
        "Kraków",
        "Wrocław",
        "Poznań",
        "Gdańsk",
        "Gdynia",
        "Sopot",
        "Gliwice",
        "Katowice",
        "Łódź",
        "Lublin",
        "Szczecin",
        "Białystok",
        "Bydgoszcz",
        "Toruń",
        "Rzeszów",
        "Zabrze",
        "Chorzów",
        "Częstochowa",
        "Bielsko-Biała",
    )
}


def _text(node: Tag | None) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def _participants(text: str, role: Literal["headliner", "support"]) -> list[EventParticipant]:
    # '+' is an explicit bill separator on these pages; preserve '/' and '&' in names.
    return [EventParticipant(name=name.strip(), role=role) for name in re.split(r"\s+\+\s+", text) if name.strip()]


def _status(text: str) -> EventStatus:
    text = strict_normalize(text)
    if "odwołan" in text or "cancel" in text:
        return EventStatus.cancelled
    if "przełożon" in text or "postpon" in text:
        return EventStatus.postponed
    if "wyprzedan" in text or "sold" in text:
        return EventStatus.soldout
    return EventStatus.scheduled


def _country(city: str) -> str:
    return "PL" if strict_normalize(city) in PL_CITIES else ""


def parse_knockout_listing(html: str) -> tuple[list[RawEvent], str | None]:
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select("a.concerts-list__item")
    if not cards:
        raise SourceDegradedError("knockout: listing_structure_changed_or_empty")
    events = []
    for card in cards:
        name = _text(card.select_one(".concerts-list__name strong"))
        place = _text(card.select_one(".concerts-list__place"))
        match = re.fullmatch(r"(.+),\s*(\d{2}\.\d{2}\.\d{4})", place)
        href = card.get("href", "")
        if not name or not match or not re.search(r"/concert/detail/\d+$", href):
            raise SourceDegradedError("knockout: ambiguous_listing_card")
        city, day = match.groups()
        try:
            local_date = datetime.strptime(day, "%d.%m.%Y").date()
        except ValueError as exc:
            raise SourceDegradedError("knockout: invalid_date") from exc
        events.append(
            RawEvent(
                source="knockout",
                source_event_id=href.rsplit("/", 1)[-1],
                source_url=urljoin(KNOCKOUT_URL, href),
                event_name=name,
                participants=_participants(name, "headliner")
                + _participants(_text(card.select_one(".concerts-list__support")), "support"),
                local_date=local_date,
                city=city,
                country_code=_country(city),
                timezone="Europe/Warsaw" if _country(city) else None,
                status=_status(_text(card)),
                raw_data={"listing_text": _text(card)},
            )
        )
    next_page = soup.select_one('.pagination a[rel="next"]')
    return events, urljoin(KNOCKOUT_URL, next_page["href"]) if next_page else None


def parse_knockout_detail(html: str, listing: RawEvent) -> RawEvent:
    soup = BeautifulSoup(html, "lxml")
    fields = {
        _text(item.select_one("small")).casefold(): _text(item.select_one("h5"))
        for item in soup.select(".concert-info__item")
    }
    place = fields.get("miejsce", "").split(",", 2)
    if not fields.get("headliner") or len(place) < 2:
        raise SourceDegradedError("knockout: detail_structure_changed")
    day = _text(soup.select_one(".concert-info__date"))
    try:
        local_date = datetime.strptime(day, "%d.%m.%Y").date()
    except ValueError as exc:
        raise SourceDegradedError("knockout: ambiguous_detail_date") from exc
    start = fields.get("start", "")
    local_time = time.fromisoformat(start) if re.fullmatch(r"\d{2}:\d{2}", start) else None
    city, venue = (part.strip() for part in place[:2])
    detail_status = _status(" ".join(fields.values()))
    return listing.model_copy(
        update={
            "participants": _participants(fields["headliner"], "headliner")
            + _participants(fields.get("support", ""), "support"),
            "event_name": fields["headliner"],
            "local_date": local_date,
            "local_time": local_time,
            "city": city,
            "venue_name": venue,
            "country_code": _country(city),
            "raw_data": fields,
            "status": detail_status if detail_status != EventStatus.scheduled else listing.status,
        }
    )


def parse_fource_listing(html: str) -> list[RawEvent]:
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select("a.events__item")
    if not cards:
        raise SourceDegradedError("fource: listing_structure_changed_or_empty")
    events: dict[str, RawEvent] = {}
    for card in cards:
        name = _text(card.select_one("h2"))
        spans = card.find_all("span", recursive=False)
        if not name or len(spans) < 2 or not card.get("href"):
            raise SourceDegradedError("fource: ambiguous_listing_card")
        match = re.fullmatch(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{2})\s*[•]?\s*(.+),\s*([^,]+)", _text(spans[-1]))
        if not match:
            raise SourceDegradedError("fource: ambiguous_date_or_location")
        day, month, year, venue, city = match.groups()
        if re.search(r"\d{1,2}\.\s*\d{1,2}\.", venue) or venue.startswith(("-", "–", "—")):
            raise SourceDegradedError("fource: ambiguous_date_or_location")
        try:
            local_date = datetime(2000 + int(year), int(month), int(day)).date()
        except ValueError as exc:
            raise SourceDegradedError("fource: invalid_date") from exc
        participants = _participants(name, "headliner")
        support = re.match(r"^(?:support|special guests?)\s*:?\s+(.+)$", _text(spans[0]), re.I)
        if support:
            participants += _participants(support[1], "support")
        url = urljoin(FOURCE_URL, card["href"])
        events[url] = RawEvent(
            source="fource",
            source_event_id=urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1],
            source_url=url,
            event_name=name,
            participants=participants,
            local_date=local_date,
            city=city,
            venue_name=venue,
            country_code=_country(city),
            timezone="Europe/Warsaw" if _country(city) else None,
            status=_status(card.get("state", "")),
            raw_data={"listing_text": _text(card), "state": card.get("state", "")},
        )
    return list(events.values())


class PromoterAdapter(SourceAdapter):
    name: str
    listing_url: str

    def __init__(self, *, enabled: bool = True, client: httpx.AsyncClient | None = None) -> None:
        self.enabled = enabled
        self._client = client
        self.last_http_requests = 0

    async def fetch_events(self, country_code: str, watched_artists: list[WatchedArtistRef]) -> list[RawEvent]:
        if not self.enabled or country_code.upper() != "PL":
            return []
        if self._client is not None:
            return await self._fetch(self._client, watched_artists)
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            return await self._fetch(client, watched_artists)

    async def _fetch(self, client: httpx.AsyncClient, artists: list[WatchedArtistRef]) -> list[RawEvent]:
        origin = "https://" + urlsplit(self.listing_url).netloc
        crawler = PublicCrawler(origin, client)
        try:
            return await self.collect(crawler, artists)
        finally:
            self.last_http_requests += crawler.http_requests

    async def collect(self, crawler: PublicCrawler, artists: list[WatchedArtistRef]) -> list[RawEvent]:
        raise NotImplementedError

    async def healthcheck(self) -> SourceHealth:
        # Configuration only; actual availability is recorded by each scan.
        return SourceHealth(source=self.name, healthy=self.enabled, message=None if self.enabled else "disabled")


class KnockoutAdapter(PromoterAdapter):
    name = "knockout"
    listing_url = KNOCKOUT_URL

    async def collect(self, crawler: PublicCrawler, artists: list[WatchedArtistRef]) -> list[RawEvent]:
        url: str | None = self.listing_url
        visited: set[str] = set()
        events: dict[str, RawEvent] = {}
        while url:
            if url in visited or len(visited) >= 20:
                raise SourceDegradedError("knockout: pagination_loop_or_limit")
            visited.add(url)
            batch, url = parse_knockout_listing(await crawler.get_html(url))
            for event in batch:
                if event.source_event_id in events:
                    continue
                crawler.absolute_url(event.source_url)
                if match_event_to_artists(event, artists):
                    event = parse_knockout_detail(await crawler.get_html(event.source_url), event)
                events[event.source_event_id] = event
        return [event for event in events.values() if event.country_code == "PL"]


class FourceAdapter(PromoterAdapter):
    name = "fource"
    listing_url = FOURCE_URL

    async def collect(self, crawler: PublicCrawler, artists: list[WatchedArtistRef]) -> list[RawEvent]:
        events = parse_fource_listing(await crawler.get_html(self.listing_url))
        for event in events:
            crawler.absolute_url(event.source_url)
        return [event for event in events if event.country_code == "PL"]
