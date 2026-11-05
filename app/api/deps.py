import secrets

from fastapi import Header, HTTPException, status

from app.core.config import PLACEHOLDER_API_KEYS, settings


async def verify_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    if (
        x_api_key is None
        or settings.app_api_key.strip() in PLACEHOLDER_API_KEYS
        or not secrets.compare_digest(x_api_key, settings.app_api_key)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
