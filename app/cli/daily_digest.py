import argparse
import asyncio
import logging
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.core.config import settings
from app.services.digest import Digest, build_digest
from app.services.digest import send_email as send_digest_email

logger = logging.getLogger(__name__)


DailyDigestStatus = Literal[
    "sent",
    "nothing_to_send",
    "dry_run",
    "delivery_failed",
    "scan_in_progress",
    "scan_degraded",
]


@dataclass(frozen=True)
class DailyDigestResult:
    status: DailyDigestStatus
    notification_ids: list[int]


def _is_retryable_response(response: httpx.Response) -> bool:
    return response.status_code == 429 or 500 <= response.status_code < 600


async def _request_with_retries(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
    attempts: int = 3,
) -> httpx.Response:
    response: httpx.Response | None = None
    for attempt in range(attempts):
        try:
            response = await client.request(method, path, json=json)
        except httpx.RequestError:
            if attempt == attempts - 1:
                raise
            await asyncio.sleep(2**attempt)
            continue

        if not _is_retryable_response(response):
            return response
        if attempt < attempts - 1:
            await asyncio.sleep(2**attempt)

    assert response is not None
    return response


def _source_problems(scan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "source": source,
            "status": result.get("status"),
            "events_found": result.get("events_found", 0),
            "error": result.get("error"),
        }
        for source, result in (scan.get("sources") or {}).items()
        if isinstance(result, dict) and result.get("status") in {"degraded", "error"}
    ]


async def _mark_failed(
    client: httpx.AsyncClient,
    notification_ids: Sequence[int],
    error: str,
) -> None:
    for notification_id in notification_ids:
        response = await _request_with_retries(
            client,
            "POST",
            f"/api/v1/notifications/{notification_id}/fail",
            json={"error": error},
        )
        response.raise_for_status()


async def run_daily_digest(
    *,
    api_base_url: str,
    api_key: str,
    country_codes: Sequence[str],
    sources: Sequence[str],
    limit: int = 200,
    dry_run: bool = False,
    send_email: Callable[[Digest], None] | None = None,
    on_digest: Callable[[Digest], None] | None = None,
) -> DailyDigestResult:
    headers = {"X-API-Key": api_key}
    timeout = httpx.Timeout(
        connect=10,
        read=settings.digest_http_timeout_seconds,
        write=30,
        pool=10,
    )

    async with httpx.AsyncClient(
        base_url=api_base_url.rstrip("/"),
        headers=headers,
        timeout=timeout,
    ) as client:
        scan_response = await _request_with_retries(
            client,
            "POST",
            "/api/v1/scan",
            json={
                "country_codes": [country.strip().upper() for country in country_codes if country.strip()],
                "sources": [source.strip() for source in sources if source.strip()] or None,
            },
        )
        if scan_response.status_code == 409:
            return DailyDigestResult(status="scan_in_progress", notification_ids=[])
        scan_response.raise_for_status()
        scan = scan_response.json()
        problems = _source_problems(scan)
        for problem in problems:
            logger.warning(
                "Source %s: %s (%s)",
                problem["source"],
                problem["status"],
                problem["error"],
            )

        pending_response = await _request_with_retries(
            client,
            "GET",
            f"/api/v1/notifications/pending?limit={limit}",
        )
        pending_response.raise_for_status()
        pending = pending_response.json()
        if not isinstance(pending, list):
            raise ValueError("Pending notifications response must be a JSON list")
        if not pending:
            if problems:
                return DailyDigestResult(status="scan_degraded", notification_ids=[])
            return DailyDigestResult(status="nothing_to_send", notification_ids=[])

        digest = build_digest(pending, source_problems=problems)
        if on_digest is not None:
            on_digest(digest)
        if dry_run:
            return DailyDigestResult(status="dry_run", notification_ids=digest.notification_ids)
        if send_email is None:
            raise ValueError("send_email callback is required unless dry_run is enabled")

        try:
            await asyncio.to_thread(send_email, digest)
        except Exception as exc:
            error = f"Email delivery failed ({type(exc).__name__})"
            await _mark_failed(client, digest.notification_ids, error)
            return DailyDigestResult(
                status="delivery_failed",
                notification_ids=digest.notification_ids,
            )

        acknowledged_ids: list[int] = []
        for index, notification_id in enumerate(digest.notification_ids):
            try:
                ack_response = await _request_with_retries(
                    client,
                    "POST",
                    f"/api/v1/notifications/{notification_id}/ack",
                )
                ack_response.raise_for_status()
            except Exception:
                logger.error(
                    "Notification ACK failed after email delivery",
                    extra={
                        "acknowledged_notification_ids": acknowledged_ids,
                        "pending_notification_ids": digest.notification_ids[index:],
                    },
                    exc_info=True,
                )
                raise
            acknowledged_ids.append(notification_id)
            logger.info(
                "Notification acknowledged",
                extra={"notification_id": notification_id},
            )

        return DailyDigestResult(status="sent", notification_ids=digest.notification_ids)


def _csv_values(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _smtp_delivery(digest: Digest) -> None:
    send_digest_email(
        digest,
        smtp_host=settings.smtp_host,
        smtp_port=settings.smtp_port,
        smtp_username=settings.smtp_username,
        smtp_password=settings.smtp_password,
        smtp_from=settings.smtp_from,
        smtp_to=settings.smtp_recipient_list,
        smtp_use_tls=settings.smtp_use_tls,
        smtp_use_ssl=settings.smtp_use_ssl,
    )


def _smtp_configuration_error() -> str | None:
    if not settings.smtp_host or not settings.smtp_from or not settings.smtp_recipient_list:
        return "Configure SMTP_HOST, SMTP_FROM and SMTP_TO in .env, or use --dry-run."
    if settings.smtp_use_tls and settings.smtp_use_ssl:
        return "SMTP_USE_TLS and SMTP_USE_SSL cannot both be enabled."
    return None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan concerts and send a daily digest.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="scan and print the digest without sending or ACK/FAIL",
    )
    parser.add_argument(
        "--countries",
        default=settings.digest_country_codes,
        help="comma-separated ISO country codes (default: from .env)",
    )
    parser.add_argument(
        "--sources",
        default=settings.digest_sources,
        help="comma-separated source names (default: from .env)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.dry_run:
        smtp_error = _smtp_configuration_error()
        if smtp_error is not None:
            print(smtp_error, file=sys.stderr)
            return 2

    try:
        result = asyncio.run(
            run_daily_digest(
                api_base_url=settings.digest_api_url,
                api_key=settings.app_api_key,
                country_codes=_csv_values(args.countries),
                sources=_csv_values(args.sources),
                limit=settings.digest_limit,
                dry_run=args.dry_run,
                send_email=None if args.dry_run else _smtp_delivery,
                on_digest=(lambda digest: print(digest.text)) if args.dry_run else None,
            )
        )
    except (httpx.HTTPError, OSError, ValueError) as exc:
        print(f"Daily digest failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if result.status == "scan_in_progress":
        print("Scan already in progress; no notifications were changed.", file=sys.stderr)
        return 2
    if result.status == "scan_degraded":
        print("Scan incomplete: source errors reported; no pending notifications.", file=sys.stderr)
        return 1
    if result.status == "nothing_to_send":
        print("No pending notifications; delivery skipped.")
    elif result.status == "dry_run":
        print(f"Dry run: {len(result.notification_ids)} notification(s), nothing acknowledged.")
    elif result.status == "delivery_failed":
        print(
            f"Email delivery failed; {len(result.notification_ids)} notification(s) left pending.",
            file=sys.stderr,
        )
        return 1
    else:
        print(f"Digest sent and {len(result.notification_ids)} notification(s) acknowledged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
