from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import verify_api_key
from app.db.session import get_session
from app.schemas.notification import NotificationFailRequest, PendingNotification
from app.services import notification_service

router = APIRouter(
    prefix="/api/v1/notifications",
    tags=["notifications"],
    dependencies=[Depends(verify_api_key)],
)


@router.get("/pending", response_model=list[PendingNotification])
async def get_pending_notifications(
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[PendingNotification]:
    return await notification_service.list_pending(session, limit=limit)


@router.post("/{notification_id}/ack")
async def ack_notification(notification_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    ok = await notification_service.ack_notification(session, notification_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    return {"status": "sent"}


@router.post("/{notification_id}/fail")
async def fail_notification(
    notification_id: int,
    body: NotificationFailRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    ok = await notification_service.fail_notification(session, notification_id, body)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    return {"status": "pending"}
