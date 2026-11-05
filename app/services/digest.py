import smtplib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from email.message import EmailMessage
from html import escape
from typing import Any
from urllib.parse import urlsplit

_TYPE_LABELS = {
    "NEW_EVENT": "NOWY KONCERT",
    "DATE_CHANGED": "ZMIANA DATY",
    "VENUE_CHANGED": "ZMIANA MIEJSCA",
    "CANCELLED": "ODWOŁANY KONCERT",
    "POSTPONED": "PRZEŁOŻONY KONCERT",
    "RESCHEDULED": "PRZENIESIONY KONCERT",
}


@dataclass(frozen=True)
class Digest:
    notification_ids: list[int]
    subject: str
    text: str
    html: str


def _format_date(value: Any) -> str:
    if not value:
        return "brak daty"
    parts = str(value).split("-")
    if len(parts) == 3:
        year, month, day = parts
        return f"{day}.{month}.{year}"
    return str(value)


def _format_time(value: Any) -> str:
    if not value:
        return ""
    return str(value)[:5]


def _location(notification: Mapping[str, Any]) -> str:
    values = [notification.get("city"), notification.get("venue")]
    return " · ".join(str(value) for value in values if value) or "miejsce nieznane"


def _source_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return value


def _type_label(notification: Mapping[str, Any]) -> str:
    notification_type = notification.get("type")
    return _TYPE_LABELS.get(str(notification_type), str(notification_type or "AKTUALIZACJA"))


def build_digest(
    notifications: Sequence[Mapping[str, Any]],
    *,
    source_problems: Sequence[Mapping[str, Any]] = (),
) -> Digest:
    notification_ids = [int(notification["id"]) for notification in notifications]
    text_blocks: list[str] = []
    html_blocks: list[str] = []

    for notification in notifications:
        date = _format_date(notification.get("date"))
        local_time = _format_time(notification.get("time"))
        date_line = f"{date}, {local_time}" if local_time else date
        source_lines: list[str] = []
        source_links: list[str] = []

        for source in notification.get("sources") or []:
            if not isinstance(source, Mapping):
                continue
            name = str(source.get("name") or "źródło")
            url = _source_url(source.get("url"))
            if url:
                source_lines.append(f"{name}: {url}")
                source_links.append(f'<li><a href="{escape(url, quote=True)}">{escape(name)}</a></li>')
            else:
                source_lines.append(name)
                source_links.append(f"<li>{escape(name)}</li>")

        text_blocks.append(
            "\n".join(
                line
                for line in (
                    _type_label(notification),
                    str(notification.get("artist") or "Nieznany artysta"),
                    date_line,
                    _location(notification),
                    f"Źródła:\n{chr(10).join(source_lines)}" if source_lines else "",
                )
                if line
            )
        )
        artist = escape(str(notification.get("artist") or "Nieznany artysta"))
        html_sources = f"<p>Źródła:</p><ul>{''.join(source_links)}</ul>" if source_links else ""
        html_blocks.append(
            "<section>"
            f"<h3>{escape(_type_label(notification))}</h3>"
            f"<p><strong>{artist}</strong><br>{escape(date_line)}"
            f"<br>{escape(_location(notification))}</p>"
            f"{html_sources}"
            "</section>"
        )

    warning_text = ""
    warning_html = ""
    if source_problems:
        warning_lines = []
        warning_items = []
        for problem in source_problems:
            source = str(problem.get("source") or "unknown")
            status = str(problem.get("status") or "unknown")
            error = problem.get("error")
            suffix = f" ({error})" if error else ""
            warning_lines.append(f"- {source}: {status}{suffix}")
            warning_items.append(f"<li>{escape(source)}: {escape(status)}{escape(str(suffix))}</li>")
        warning_text = "\n\nProblemy źródeł:\n" + "\n".join(warning_lines)
        warning_html = f"<aside><strong>Problemy źródeł:</strong><ul>{''.join(warning_items)}</ul></aside>"

    count = len(notification_ids)
    return Digest(
        notification_ids=notification_ids,
        subject=f"[Concert Watcher] {count} aktualizacji koncertowych",
        text=f"Aktualizacje koncertowe ({count})\n\n{'\n\n'.join(text_blocks)}{warning_text}",
        html=(
            f"<html><body><h2>Aktualizacje koncertowe ({count})</h2>{warning_html}{''.join(html_blocks)}</body></html>"
        ),
    )


def send_email(
    digest: Digest,
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_username: str,
    smtp_password: str,
    smtp_from: str,
    smtp_to: Sequence[str],
    smtp_use_tls: bool,
    smtp_use_ssl: bool,
) -> None:
    if not smtp_host:
        raise ValueError("SMTP_HOST is required")
    if not smtp_from:
        raise ValueError("SMTP_FROM is required")
    if not smtp_to:
        raise ValueError("SMTP_TO is required")
    if smtp_use_tls and smtp_use_ssl:
        raise ValueError("SMTP_USE_TLS and SMTP_USE_SSL cannot both be enabled")

    message = EmailMessage()
    message["Subject"] = digest.subject
    message["From"] = smtp_from
    message["To"] = ", ".join(smtp_to)
    message.set_content(digest.text)
    message.add_alternative(digest.html, subtype="html")

    smtp_factory = smtplib.SMTP_SSL if smtp_use_ssl else smtplib.SMTP
    with smtp_factory(smtp_host, smtp_port, timeout=30) as server:
        if smtp_use_tls:
            server.starttls()
        if smtp_username:
            server.login(smtp_username, smtp_password)
        server.send_message(message)
