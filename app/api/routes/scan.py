from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import verify_api_key
from app.db.session import get_session
from app.schemas.scan import ScanRequest, ScanResponse
from app.services.scan_service import ScanInProgressError, run_scan

router = APIRouter(prefix="/api/v1", tags=["scan"], dependencies=[Depends(verify_api_key)])


@router.post("/scan", response_model=ScanResponse)
async def trigger_scan(
    request: ScanRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> ScanResponse:
    try:
        return await run_scan(session, request or ScanRequest())
    except ScanInProgressError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Scan already in progress",
        ) from None
