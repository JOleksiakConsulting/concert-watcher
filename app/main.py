from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import artists, health, notifications, scan
from app.core.config import settings
from app.db.paths import ensure_sqlite_directory


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_sqlite_directory(settings.database_url)
    yield


app = FastAPI(title="Concert Watcher", version="0.1.0", lifespan=lifespan)

app.include_router(health.router)
app.include_router(artists.router)
app.include_router(scan.router)
app.include_router(notifications.router)
