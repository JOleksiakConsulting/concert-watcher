"""Conservative, single-origin HTTP crawler for public promoter pages."""

import asyncio
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.core.config import settings


class SourceDegradedError(NotImplementedError):
    """Source cannot currently be read reliably without browser automation."""


class PublicCrawler:
    def __init__(self, origin: str, client: httpx.AsyncClient) -> None:
        self.origin = origin.rstrip("/")
        self.client = client
        self.http_requests = 0
        self._last_request: float | None = None
        self._robots: RobotFileParser | None = None
        self._delay = max(0.0, settings.crawler_min_delay_seconds)

    def absolute_url(self, url: str) -> str:
        url = urljoin(self.origin + "/", url)
        parsed = urlsplit(url)
        if (
            (parsed.scheme, parsed.netloc) != ("https", urlsplit(self.origin).netloc)
            or parsed.username
            or parsed.password
        ):
            raise SourceDegradedError("external_redirect_or_link_not_allowed")
        return url

    async def _request(self, url: str) -> httpx.Response:
        for attempt in range(3):
            if self._last_request is not None:
                await asyncio.sleep(max(0, self._delay - (time.monotonic() - self._last_request)))
            self._last_request = time.monotonic()
            self.http_requests += 1
            try:
                response = await self.client.get(
                    url,
                    headers={"User-Agent": settings.crawler_user_agent},
                    follow_redirects=False,
                )
            except httpx.TransportError:
                if attempt == 2:
                    raise
                await asyncio.sleep(2**attempt)
                continue
            if response.status_code not in {429, 500, 502, 503, 504} or attempt == 2:
                return response
            delay = float(2**attempt)
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    try:
                        when = parsedate_to_datetime(retry_after)
                        delay = max(delay, (when - datetime.now(UTC)).total_seconds())
                    except (TypeError, ValueError, OverflowError):
                        pass
            if delay > 120:
                raise SourceDegradedError("retry_after_too_long; retry at next scan")
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    async def _get(self, url: str, *, robots: bool = False) -> httpx.Response:
        for _ in range(6):
            url = self.absolute_url(url)
            if not robots and self._robots and not self._robots.can_fetch(settings.crawler_user_agent, url):
                raise SourceDegradedError("robots_disallowed")
            response = await self._request(url)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                if not location:
                    raise SourceDegradedError("invalid_redirect")
                url = urljoin(url, location)
                continue
            if response.status_code in {401, 403}:
                raise SourceDegradedError("unsupported_without_browser: HTTP access denied")
            return response
        raise SourceDegradedError("redirect_loop")

    async def get_html(self, url: str) -> str:
        if self._robots is None:
            response = await self._get(self.origin + "/robots.txt", robots=True)
            if response.status_code not in {404, 410}:
                response.raise_for_status()
            parser = RobotFileParser()
            parser.parse([] if response.status_code in {404, 410} else response.text.splitlines())
            self._robots = parser
            self._delay = max(self._delay, parser.crawl_delay(settings.crawler_user_agent) or 0)
            rate = parser.request_rate(settings.crawler_user_agent)
            if rate and rate.requests:
                self._delay = max(self._delay, rate.seconds / rate.requests)
        response = await self._get(url)
        response.raise_for_status()
        return response.text
