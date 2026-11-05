import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NotificationOutbox
from app.schemas.notification import NotificationFailRequest, PendingNotification


async def list_pending(session: AsyncSession, limit: int = 50) -> list[PendingNotification]:
    result = await session.execute(
        select(NotificationOutbox)
        .where(NotificationOutbox.status == "pending")
        .order_by(NotificationOutbox.created_at)
        .limit(limit)
    )
    pending: list[PendingNotification] = []
    for row in result.scalars():
        payload = json.loads(row.payload_json)
        pending.append(
            PendingNotification(
                id=row.id,
                type=row.notification_type,
                artist=payload.get("artist", ""),
                date=payload.get("date"),
                time=payload.get("time"),
                city=payload.get("city"),
                venue=payload.get("venue"),
                country_code=payload.get("country_code", ""),
                sources=payload.get("sources", []),
            )
        )
    return pending


async def ack_notification(session: AsyncSession, notification_id: int) -> bool:
    result = await session.execute(select(NotificationOutbox).where(NotificationOutbox.id == notification_id))
    notification = result.scalar_one_or_none()
    if notification is None:
        return False
    if notification.status == "sent":
        return True
    notification.status = "sent"
    notification.sent_at = datetime.now(UTC)
    await session.commit()
    return True


async def fail_notification(session: AsyncSession, notification_id: int, body: NotificationFailRequest) -> bool:
    result = await session.execute(select(NotificationOutbox).where(NotificationOutbox.id == notification_id))
    notification = result.scalar_one_or_none()
    if notification is None:
        return False
    if notification.status == "sent":
        return True
    notification.attempts += 1
    notification.last_attempt_at = datetime.now(UTC)
    notification.last_error = body.error
    notification.status = "pending"
    await session.commit()
    return True
